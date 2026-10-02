"""
Suporte — chamados do cliente, com a mesma lógica do sistema antigo
(`plataforma_web`, `zendeskcontroller-class.php` + widget `zendesk`):

- o assunto do chamado é o "Serviço" escolhido numa lista fixa (a mesma do
  widget antigo, para as visões e gatilhos do Zendesk continuarem valendo);
- o solicitante é sempre o usuário logado (e-mail da sessão, nunca digitado);
- anexo opcional (imagem, PDF, DOCX, XLSX, CSV ou texto) vai primeiro para
  `/api/v2/uploads.json` e o token entra no comentário do chamado;
- "Meus chamados" lista os chamados do solicitante dos últimos 3 meses;
- o detalhe traz as respostas (comentários públicos).

Diferenças de propósito em relação ao antigo:
- credenciais do Zendesk só por variável de ambiente (ZENDESK_SUBDOMAIN,
  ZENDESK_EMAIL, ZENDESK_API_TOKEN) — no antigo o token estava no código;
- o detalhe confere que o chamado é do usuário antes de mostrar respostas
  (no antigo, qualquer id era aceito);
- o cliente escolhe a urgência em palavras simples (antes era sempre "normal").

⚠️ Sem as variáveis de ambiente, a integração fica DESLIGADA: o chamado é
registrado num SQLite local (`data/suporte.sqlite`) e a tela avisa que ainda
não foi enviado ao Zendesk. Ligar em produção é decisão da engenharia.
"""

import base64
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_read
from app.middleware.auth import get_current_user

router = APIRouter()

ARQUIVO = Path(__file__).resolve().parents[4] / "data" / "suporte.sqlite"
_trava = threading.Lock()
ANEXO_MAX_BYTES = 10 * 1024 * 1024
TIPOS_ANEXO = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf", "text/plain", "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# Lista do widget antigo (`dashboard/widgets/zendesk/zendesk.js`, selectOptions).
SERVICOS: list[str] = json.loads(
    (Path(__file__).with_name("suporte_servicos.json")).read_text(encoding="utf-8")
)

# SUPOSIÇÃO: assuntos de uso interno da SS (encerramentos pelo Voll, pedidos
# "para técnica", assuntos internos) não aparecem para o cliente. Confirmar
# com o time de Suporte quais devem ficar visíveis.
_INTERNOS = (
    "voll", "interno", "para técnica", "para tecnica", "abandono", "informações de chamado em aberto",
    "solicitação abertura de chamado", "validação volta teste", "auxilio eletricista", "cadastro de apn",
    "driver usb", "configurador não conecta", "comissionamento",
)

# Agrupamento só para a tela (o assunto enviado continua o nome exato do serviço).
# A ordem importa: o primeiro grupo cujo termo aparece no nome leva o serviço.
_GRUPOS: list[tuple[str, tuple[str, ...]]] = [
    ("Câmeras e vídeo", ("camera", "câmera", "vídeo", "video", "sd card")),
    ("Cadastros, usuários e acessos", ("cadastro", "usuário", "senha", "grupo/subgrupo", "instrutor", "remoção de veículo", "troca de veículo", "embarque de motoristas")),
    ("Erro ou lentidão na plataforma", ("plataforma", "time out", "app ", "app minha", "gráfico")),
    ("Indicadores, BI e relatórios", ("bi ", "bi -", "power bi", "relatório", "atualização bi", "novo bi", "indicador")),
    ("Configurações e alertas", ("configuração", "ativação", "desativar", "desabilitar", "sonoro", "audio", "áudio", "bip", "rotograma", "intervalo", "avisos")),
    ("Equipamento, sinal e telemetria", ("problema", "falha", "travamento", "travado", "perda", "sem transmissão", "odômetro", "odometro", "consumo", "chip", "sim card", "km", "rede can", "script", "ignição", "inércia")),
    ("Instalação, troca e materiais", ("técnica", "instalação", "desinstalação", "troca de", "devolução", "materiais", "material", "associação", "auditoria")),
    ("Treinamento e reuniões", ("treinamento", "reunião", "reciclagem")),
    ("Comercial e financeiro", ("comercial", "financeiro", "administrativo", "aditivo", "orçamento")),
]
OUTROS = "Dúvidas e outros assuntos"
PADRAO = "Suporte - Suporte ao usuário ou Plataforma"

PRIORIDADES = {"low": "Posso esperar", "normal": "Normal", "high": "Está atrapalhando a operação", "urgent": "Parou a operação"}
STATUS_PT = {
    "new": "novo", "open": "em atendimento", "pending": "aguardando você", "hold": "em espera",
    "solved": "resolvido", "closed": "fechado", "registrado": "registrado (não enviado)",
}


def _visivel(s: str) -> bool:
    n = s.lower()
    return not any(t in n for t in _INTERNOS)


def _grupo(s: str) -> str:
    n = s.lower() + " "
    for nome, termos in _GRUPOS:
        if any(t in n for t in termos):
            return nome
    return OUTROS


def _zendesk() -> Optional[tuple[str, str, str]]:
    sub, email, token = (os.getenv(k, "").strip() for k in ("ZENDESK_SUBDOMAIN", "ZENDESK_EMAIL", "ZENDESK_API_TOKEN"))
    return (sub, email, token) if sub and email and token else None


def _cliente_zendesk(cfg: tuple[str, str, str]) -> httpx.AsyncClient:
    sub, email, token = cfg
    return httpx.AsyncClient(
        base_url=f"https://{sub}.zendesk.com/api/v2",
        auth=(f"{email}/token", token),
        timeout=httpx.Timeout(20.0),
    )


def _con() -> sqlite3.Connection:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(ARQUIVO)
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE IF NOT EXISTS chamado (
            id INTEGER PRIMARY KEY AUTOINCREMENT, zendesk_id INTEGER, user_id INTEGER, email TEXT,
            assunto TEXT, descricao TEXT, prioridade TEXT, status TEXT, anexo TEXT, contexto TEXT,
            criado_em TEXT)"""
    )
    return c


async def _solicitante(db: AsyncSession, user) -> dict:
    """Nome e e-mail do usuário logado, lidos do cadastro (só leitura)."""
    r = (
        await db.execute(text("SELECT name, email FROM mova.users WHERE id = :u"), {"u": user.user_id})
    ).mappings().first()
    email = ((r and r["email"]) or getattr(user, "email", None) or "").strip()
    if not email:
        raise HTTPException(422, "Seu usuário não tem e-mail cadastrado. Peça ao administrador para incluir.")
    return {"nome": ((r and r["name"]) or email).strip(), "email": email}


# ------------------------------- Rotas -----------------------------------


@router.get("/servicos")
async def servicos(user=Depends(get_current_user)):
    grupos: dict[str, list[str]] = {}
    for s in SERVICOS:
        if _visivel(s):
            grupos.setdefault(_grupo(s), []).append(s)
    ordem = [g for g, _ in _GRUPOS] + [OUTROS]
    return {
        "integrado": _zendesk() is not None,
        "padrao": PADRAO,
        "prioridades": [{"valor": k, "rotulo": v} for k, v in PRIORIDADES.items()],
        "grupos": [{"nome": g, "servicos": sorted(grupos[g], key=str.lower)} for g in ordem if g in grupos],
    }


class Anexo(BaseModel):
    nome: str = Field(..., max_length=200)
    tipo: str
    base64: str


class NovoChamado(BaseModel):
    assunto: str
    descricao: str = Field(..., min_length=10, max_length=4000)
    prioridade: str = "normal"
    anexo: Optional[Anexo] = None
    contexto: Optional[dict] = Field(None, description="Tela de origem, organização ativa, navegador")


@router.post("/chamados")
async def abrir(dados: NovoChamado, db: AsyncSession = Depends(get_db_read), user=Depends(get_current_user)):
    if dados.assunto not in SERVICOS:
        raise HTTPException(422, "Escolha o assunto na lista.")
    if dados.prioridade not in PRIORIDADES:
        raise HTTPException(422, "Urgência inválida.")
    quem = await _solicitante(db, user)

    arquivo: Optional[bytes] = None
    if dados.anexo:
        if dados.anexo.tipo not in TIPOS_ANEXO:
            raise HTTPException(422, "Tipo de arquivo não aceito. Envie imagem, PDF, Word, Excel, CSV ou texto.")
        try:
            arquivo = base64.b64decode(dados.anexo.base64, validate=True)
        except Exception:
            raise HTTPException(422, "Não foi possível ler o anexo.")
        if len(arquivo) > ANEXO_MAX_BYTES:
            raise HTTPException(422, "O anexo passa de 10 MB.")

    ctx = dados.contexto or {}
    rodape = "\n\n—\nAberto pela plataforma SS (nova)"
    for rotulo, chave in (("Organização", "organizacao"), ("Tela", "tela"), ("Navegador", "navegador")):
        if ctx.get(chave):
            rodape += f"\n{rotulo}: {str(ctx[chave])[:200]}"
    corpo = dados.descricao.strip() + rodape

    zendesk_id = None
    situacao = "registrado"
    cfg = _zendesk()
    if cfg:
        async with _cliente_zendesk(cfg) as z:
            comentario: dict = {"body": corpo}
            if arquivo is not None and dados.anexo:
                up = await z.post(
                    "/uploads.json", params={"filename": dados.anexo.nome}, content=arquivo,
                    headers={"Content-Type": dados.anexo.tipo},
                )
                if up.status_code >= 300:
                    raise HTTPException(502, "O Zendesk não aceitou o anexo. Tente sem o arquivo ou com outro formato.")
                comentario["uploads"] = [up.json()["upload"]["token"]]
            r = await z.post(
                "/tickets.json",
                json={"ticket": {
                    "subject": dados.assunto, "comment": comentario, "priority": dados.prioridade,
                    "requester": {"name": quem["nome"], "email": quem["email"]},
                }},
            )
            if r.status_code != 201:
                raise HTTPException(502, "O Zendesk não respondeu como esperado. Tente de novo em alguns minutos.")
            t = r.json()["ticket"]
            zendesk_id, situacao = t["id"], t.get("status", "new")

    with _trava, _con() as c:
        cur = c.execute(
            "INSERT INTO chamado (zendesk_id, user_id, email, assunto, descricao, prioridade, status, anexo, contexto, criado_em)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (zendesk_id, user.user_id, quem["email"], dados.assunto, corpo, dados.prioridade, situacao,
             dados.anexo.nome if dados.anexo else None, json.dumps(ctx, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()),
        )
        local_id = cur.lastrowid
    return {"id": zendesk_id or f"L{local_id}", "integrado": cfg is not None, "status": STATUS_PT.get(situacao, situacao)}


def _local(user_id: int) -> list[dict]:
    with _con() as c:
        rows = c.execute(
            "SELECT * FROM chamado WHERE user_id = ? AND zendesk_id IS NULL ORDER BY id DESC LIMIT 100", (user_id,)
        ).fetchall()
    return [
        {
            "id": f"L{r['id']}", "assunto": r["assunto"], "status": STATUS_PT["registrado"], "status_codigo": "registrado",
            "prioridade": PRIORIDADES.get(r["prioridade"], r["prioridade"]), "criado_em": r["criado_em"],
            "atualizado_em": r["criado_em"], "descricao": r["descricao"], "anexo": r["anexo"],
        }
        for r in rows
    ]


@router.get("/chamados")
async def meus_chamados(db: AsyncSession = Depends(get_db_read), user=Depends(get_current_user)):
    cfg = _zendesk()
    locais = _local(user.user_id)
    if not cfg:
        return {"integrado": False, "chamados": locais}
    quem = await _solicitante(db, user)
    desde = (datetime.now(timezone.utc) - timedelta(days=92)).date().isoformat()
    async with _cliente_zendesk(cfg) as z:
        r = await z.get("/search.json", params={"query": f'type:ticket requester:"{quem["email"]}" created>{desde}', "sort_by": "updated_at", "sort_order": "desc"})
    if r.status_code != 200:
        raise HTTPException(502, "Não foi possível consultar seus chamados no Zendesk agora.")
    chamados = [
        {
            "id": t["id"], "assunto": t.get("subject"), "status": STATUS_PT.get(t.get("status"), t.get("status")),
            "status_codigo": t.get("status"), "prioridade": PRIORIDADES.get(t.get("priority") or "normal", t.get("priority")),
            "criado_em": t.get("created_at"), "atualizado_em": t.get("updated_at"), "descricao": t.get("description"),
        }
        for t in r.json().get("results", [])
    ]
    return {"integrado": True, "chamados": chamados + locais}


@router.get("/chamados/{chamado_id}/respostas")
async def respostas(chamado_id: str, db: AsyncSession = Depends(get_db_read), user=Depends(get_current_user)):
    if chamado_id.startswith("L"):
        if not any(c["id"] == chamado_id for c in _local(user.user_id)):
            raise HTTPException(404, "Chamado não encontrado.")
        return {"respostas": []}
    cfg = _zendesk()
    if not cfg or not chamado_id.isdigit():
        raise HTTPException(404, "Chamado não encontrado.")
    quem = await _solicitante(db, user)
    async with _cliente_zendesk(cfg) as z:
        t = await z.get(f"/tickets/{chamado_id}.json", params={"include": "users"})
        if t.status_code != 200:
            raise HTTPException(404, "Chamado não encontrado.")
        corpo = t.json()
        dono = next((u for u in corpo.get("users", []) if u["id"] == corpo["ticket"]["requester_id"]), None)
        if not dono or (dono.get("email") or "").lower() != quem["email"].lower():
            raise HTTPException(404, "Chamado não encontrado.")
        c = await z.get(f"/tickets/{chamado_id}/comments.json", params={"include": "users"})
    dados = c.json() if c.status_code == 200 else {"comments": [], "users": []}
    nomes = {u["id"]: u.get("name") for u in dados.get("users", [])}
    return {
        "respostas": [
            {
                "autor": "Você" if cm.get("author_id") == dono["id"] else (nomes.get(cm.get("author_id")) or "Suporte SS"),
                "do_suporte": cm.get("author_id") != dono["id"],
                "texto": cm.get("plain_body") or cm.get("body"),
                "em": cm.get("created_at"),
                "anexos": [{"nome": a.get("file_name"), "url": a.get("content_url")} for a in cm.get("attachments", [])],
            }
            for cm in dados.get("comments", [])
            if cm.get("public", True)
        ]
    }
