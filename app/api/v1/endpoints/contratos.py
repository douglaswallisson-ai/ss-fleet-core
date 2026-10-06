"""
Contratos de clientes — todo cliente (grupo) da plataforma tem um contrato.

Regras do PM (06/10/2026):
- grupo novo só nasce pelo cadastro de um contrato (Cadastros › Grupos não cria
  mais grupo, ver cadastros.py);
- os clientes que já existem ganham um contrato cada, pré-preenchido com o que o
  banco tem. Esses contratos ("existente") não têm trava nenhuma: todo campo é
  opcional e editável, porque o time vai completar à mão;
- o sistema nunca encerra contrato sozinho. Passar da data de fim só marca
  "vencido" na tela; encerrar é ação manual, com motivo;
- guardar todos os dados possíveis: vigência, valores, veículos, CNPJ, contatos,
  produtos, reajuste, faturamento.

Fontes do pré-preenchimento (só leitura):
- `mova."group"`: nome, razão social, CNPJ, endereço, contato, pró-rata, código;
- `mova.cliente_financeiro_vigencia` (backfill de set/2026, 1 linha por grupo):
  vigência, tempo, parcela, implantação e os dados de combustível do ROI;
- `mova.tracked_unit` ativos: quantidade de veículos de hoje.
`mova.contract*` é legado (5 clientes) e não é usado (memória do PM).
Em 06/10/2026, 170 das 199 linhas de `cliente_financeiro_vigencia` eram dado
fictício de teste (`registrado_por = 'teste'`, vigência 17/09–29/09/2026, parcela
800 ou 3.500). Só as 29 do backfill da planilha entram no pré-preenchimento; os
outros clientes começam só com o cadastro do grupo e a frota.

Gravação: armazenamento provisório `data/contratos.sqlite` (o banco é só
leitura). O grupo de um contrato novo fica como "grupo a criar": aparece aqui,
mas só existe no sistema atual quando a engenharia liberar a gravação.
SUPOSIÇÃO: o ROI da Início continua lendo `cliente_financeiro_vigencia`; passar
a ler daqui depois que o time completar os contratos é decisão do PM.
"""

import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.core.database import AsyncSessionLocalReplica
from app.middleware.auth import require_permission

router = APIRouter()

ARQUIVO = Path(__file__).resolve().parents[4] / "data" / "contratos.sqlite"
_trava = threading.Lock()
SYNC_S = 300
_ultimo_sync = 0.0

SEGMENTOS = ["carga", "urbano", "fretamento", "misto", "outro"]
PRODUTOS = ["Telemetria", "Videotelemetria", "Roteirização", "Contagem de passageiros", "Controle de combustível",
            "Manutenção", "Jornada e ponto", "Painel sinótico", "Checklist"]
REAJUSTES = ["IPCA", "IGP-M", "INPC", "Sem reajuste", "Outro"]
PAGAMENTOS = ["Boleto", "PIX", "Transferência", "Cartão", "Outro"]
#: Campos guardados no contrato (todos opcionais nos contratos de clientes existentes).
CAMPOS = [
    # cliente
    "nome_grupo", "razao_social", "nome_fantasia", "cnpj", "inscricao_estadual", "segmento",
    "endereco", "cidade", "uf", "cep", "codigo_cliente",
    # contatos
    "contato_nome", "contato_email", "contato_telefone", "financeiro_nome", "financeiro_email", "financeiro_telefone",
    # contrato
    "numero_contrato", "data_assinatura", "data_inicio", "data_fim", "tempo_meses", "renovacao_automatica",
    "aviso_previo_dias", "indice_reajuste", "mes_reajuste",
    # frota e serviço
    "qtd_veiculos", "produtos", "equipamento_comodato",
    # financeiro
    "valor_parcela", "valor_implantacao", "valor_total", "forma_pagamento", "dia_vencimento", "pro_rata",
    # ROI (mesmos campos de cliente_financeiro_vigencia)
    "gasto_medio_combustivel_mes", "custo_medio_combustivel_l", "reducao_estimada_pct",
    # comercial
    "vendedor", "observacoes",
]
#: Obrigatórios só no contrato novo (que cria o grupo).
OBRIG_NOVO = [("nome_grupo", "Nome do grupo"), ("razao_social", "Razão social"), ("cnpj", "CNPJ"),
              ("segmento", "Segmento"), ("data_inicio", "Início da vigência"), ("data_fim", "Fim da vigência"),
              ("qtd_veiculos", "Quantidade de veículos"), ("valor_parcela", "Valor da parcela mensal"),
              ("contato_nome", "Nome do contato"), ("contato_email", "E-mail do contato")]


# ------------------------------------------------------------- armazenamento

@contextmanager
def _con():
    """Conexão que grava e fecha ao sair (o `with sqlite3.connect()` só grava, não fecha)."""
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(ARQUIVO)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS contrato (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER UNIQUE,
            origem TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ativo', dados TEXT NOT NULL,
            motivo_status TEXT, criado_em TEXT, atualizado_em TEXT, autor INTEGER);
        CREATE TABLE IF NOT EXISTS historico (id INTEGER PRIMARY KEY AUTOINCREMENT, contrato_id INTEGER,
            acao TEXT, dados TEXT, motivo TEXT, autor INTEGER, em TEXT);
        """
    )
    try:
        with c:
            yield c
    finally:
        c.close()


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _so_ss(user):
    if not (getattr(user, "master", 0) or getattr(user, "is_super_admin", False)):
        raise HTTPException(403, "Só a SS acessa os contratos de todos os clientes.")


def _grupo_ok(user, group_id: int):
    if getattr(user, "master", 0) or getattr(user, "is_super_admin", False):
        return
    if group_id not in {g for g, _ in (getattr(user, "group_access", None) or [])}:
        raise HTTPException(403, "Sem acesso a este grupo.")


def _so_digitos(s: Any) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _num(v) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(".", "").replace(",", ".")) if isinstance(v, str) and "," in v else float(v)
    except (TypeError, ValueError):
        return None


def _data(v) -> Optional[date]:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def cnpj_valido(cnpj: str) -> bool:
    d = _so_digitos(cnpj)
    if len(d) != 14 or d == d[0] * 14:
        return False
    for n in (12, 13):
        pesos = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2][13 - n:]
        dv = sum(int(a) * b for a, b in zip(d[:n], pesos)) % 11
        if int(d[n]) != (0 if dv < 2 else 11 - dv):
            return False
    return True


def _f(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()[:10]
    if hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
        return float(v)
    return v


# ------------------------------------------------------- clientes existentes

SQL_EXISTENTES = """
SELECT g.id AS group_id, g.name AS nome_grupo, g.corporate_name AS razao_social, g.cnpj, g.address AS endereco,
       g.contact AS contato, g.pro_rata, g.client_cod AS codigo_cliente,
       (SELECT count(*) FROM mova.tracked_unit t WHERE t.group_id = g.id AND t.status = 1) AS qtd_veiculos,
       v.data_inicio_vigencia AS data_inicio, v.data_fim_vigencia AS data_fim, v.tempo_contrato_meses AS tempo_meses,
       v.valor_parcela_mensal AS valor_parcela, v.valor_implantacao, v.gasto_medio_combustivel_mes,
       v.custo_medio_combustivel_l, v.reducao_estimada_pct
FROM mova."group" g
LEFT JOIN LATERAL (SELECT * FROM mova.cliente_financeiro_vigencia x WHERE x.group_id = g.id
                   AND x.registrado_por IS DISTINCT FROM 'teste'
                   ORDER BY x.data_evento DESC NULLS LAST, x.id DESC LIMIT 1) v ON true
"""


def _pre_preenchido(r: dict) -> dict:
    d = {k: (v.strip() if isinstance(v, str) else _f(v)) for k, v in r.items() if k not in ("group_id", "contato")}
    contato = (r.get("contato") or "").strip()
    if contato:
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", contato)
        tel = re.search(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}", contato)
        d["contato_email"] = email.group(0) if email else None
        d["contato_telefone"] = tel.group(0) if tel else None
        d["observacoes"] = f"Contato no cadastro do grupo: {contato}"
    d["pro_rata"] = bool(r.get("pro_rata")) if r.get("pro_rata") is not None else None
    parc, meses, impl = _num(d.get("valor_parcela")), _num(d.get("tempo_meses")), _num(d.get("valor_implantacao")) or 0
    d["valor_total"] = round(parc * meses + impl, 2) if parc and meses else None
    return {k: v for k, v in d.items() if v not in (None, "")}


async def _sincronizar(forcar: bool = False) -> int:
    """Cria o contrato dos grupos que ainda não têm. Nunca mexe no que já foi editado."""
    global _ultimo_sync
    if not forcar and time.time() - _ultimo_sync < SYNC_S:
        return 0
    async with AsyncSessionLocalReplica() as db:
        rows = [dict(r) for r in (await db.execute(text(SQL_EXISTENTES))).mappings().all()]
    with _trava, _con() as c:
        tem = {r[0] for r in c.execute("SELECT group_id FROM contrato WHERE group_id IS NOT NULL")}
        novos = [r for r in rows if r["group_id"] not in tem]
        for r in novos:
            c.execute("INSERT INTO contrato (group_id, origem, status, dados, criado_em, atualizado_em) VALUES (?,?,?,?,?,?)",
                      (r["group_id"], "existente", "ativo", json.dumps(_pre_preenchido(r), default=str), _agora(), _agora()))
            cid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("INSERT INTO historico (contrato_id, acao, dados, em) VALUES (?,?,?,?)",
                      (cid, "pre_preenchido", "Criado a partir do cadastro do grupo e da vigência no banco.", _agora()))
    _ultimo_sync = time.time()
    return len(novos)


# ------------------------------------------------------------------ cálculo

def _calcular(d: dict) -> dict:
    """Valores derivados e situação (sem alterar o que foi digitado)."""
    ini, fim = _data(d.get("data_inicio")), _data(d.get("data_fim"))
    parc, impl, qtd = _num(d.get("valor_parcela")), _num(d.get("valor_implantacao")) or 0, _num(d.get("qtd_veiculos"))
    meses = _num(d.get("tempo_meses"))
    if not meses and ini and fim and fim >= ini:
        meses = (fim.year - ini.year) * 12 + fim.month - ini.month + (1 if fim.day >= ini.day else 0)
    total = _num(d.get("valor_total"))
    hoje = date.today()
    return {
        "tempo_meses_calc": meses,
        "valor_total_calc": total if total else (round(parc * meses + impl, 2) if parc and meses else None),
        "valor_por_veiculo": round(parc / qtd, 2) if parc and qtd else None,
        "dias_para_vencer": (fim - hoje).days if fim else None,
        "vencido": bool(fim and fim < hoje),
    }


def _pendencias(d: dict) -> list[str]:
    """Campos importantes ainda vazios (só informativo, não trava)."""
    return [rot for campo, rot in OBRIG_NOVO if d.get(campo) in (None, "", [])]


def _saida(row: sqlite3.Row, veiculos_hoje: Optional[int] = None) -> dict:
    d = json.loads(row["dados"])
    return {"id": row["id"], "group_id": row["group_id"], "origem": row["origem"], "status": row["status"],
            "motivo_status": row["motivo_status"], "criado_em": row["criado_em"], "atualizado_em": row["atualizado_em"],
            "dados": d, **_calcular(d), "pendencias": _pendencias(d), "veiculos_hoje": veiculos_hoje,
            "sem_veiculos": veiculos_hoje == 0,
            "grupo_a_criar": row["group_id"] is None}


def _validar_novo(d: dict, outros: list[dict]):
    for campo, rot in OBRIG_NOVO:
        if d.get(campo) in (None, "", []):
            raise HTTPException(422, {"message": f"{rot} é obrigatório no contrato de um cliente novo.", "field": campo})
    if not cnpj_valido(d["cnpj"]):
        raise HTTPException(422, {"message": "CNPJ inválido (confira os dígitos).", "field": "cnpj"})
    if d["segmento"] not in SEGMENTOS:
        raise HTTPException(422, {"message": "Escolha o segmento.", "field": "segmento"})
    ini, fim = _data(d["data_inicio"]), _data(d["data_fim"])
    if not ini:
        raise HTTPException(422, {"message": "Data de início inválida.", "field": "data_inicio"})
    if not fim or fim < ini:
        raise HTTPException(422, {"message": "O fim da vigência deve ser depois do início.", "field": "data_fim"})
    if (_num(d["qtd_veiculos"]) or 0) < 1:
        raise HTTPException(422, {"message": "Informe pelo menos 1 veículo.", "field": "qtd_veiculos"})
    if (_num(d["valor_parcela"]) or 0) <= 0:
        raise HTTPException(422, {"message": "O valor da parcela deve ser maior que zero.", "field": "valor_parcela"})
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(d["contato_email"])):
        raise HTTPException(422, {"message": "E-mail do contato inválido.", "field": "contato_email"})
    nome = str(d["nome_grupo"]).strip().lower()
    for o in outros:
        if str(o.get("nome_grupo") or "").strip().lower() == nome:
            raise HTTPException(422, {"message": "Já existe um cliente com este nome de grupo.", "field": "nome_grupo"})


def _limpar(dados: dict) -> dict:
    d = {k: v for k, v in dados.items() if k in CAMPOS}
    if "cnpj" in d and d["cnpj"]:
        n = _so_digitos(d["cnpj"])
        if len(n) == 14:
            d["cnpj"] = f"{n[:2]}.{n[2:5]}.{n[5:8]}/{n[8:12]}-{n[12:]}"
    return d


# ------------------------------------------------------------------ endpoints

@router.get("")
async def listar(user=Depends(require_permission("reports", "read"))):
    """Todos os contratos (só SS). Cria na hora o contrato dos grupos que ainda não têm."""
    _so_ss(user)
    criados = await _sincronizar()
    async with AsyncSessionLocalReplica() as db:
        hoje = {r[0]: r[1] for r in (await db.execute(text(
            "SELECT group_id, count(*) FROM mova.tracked_unit WHERE status = 1 GROUP BY 1"))).all()}
    with _trava, _con() as c:
        rows = list(c.execute("SELECT * FROM contrato ORDER BY id"))
    itens = [_saida(r, hoje.get(r["group_id"], 0) if r["group_id"] else None) for r in rows]
    itens.sort(key=lambda x: str(x["dados"].get("nome_grupo") or "").lower())
    return {
        "contratos": itens,
        "criados_agora": criados,
        "resumo": {
            "total": len(itens),
            "ativos": sum(1 for i in itens if i["status"] == "ativo"),
            "encerrados": sum(1 for i in itens if i["status"] == "encerrado"),
            "vencidos": sum(1 for i in itens if i["vencido"] and i["status"] == "ativo"),
            "a_completar": sum(1 for i in itens if i["pendencias"]),
            "grupos_a_criar": sum(1 for i in itens if i["grupo_a_criar"]),
            "sem_veiculos": sum(1 for i in itens if i["sem_veiculos"]),
            "receita_mensal": round(sum(_num(i["dados"].get("valor_parcela")) or 0 for i in itens if i["status"] == "ativo"), 2),
        },
        "opcoes": {"segmentos": SEGMENTOS, "produtos": PRODUTOS, "reajustes": REAJUSTES, "pagamentos": PAGAMENTOS},
    }


@router.get("/do-grupo")
async def do_grupo(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    """Contrato do próprio cliente (para a tela dele, só leitura)."""
    _grupo_ok(user, group_id)
    await _sincronizar()
    with _trava, _con() as c:
        r = c.execute("SELECT * FROM contrato WHERE group_id = ?", (group_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Este cliente ainda não tem contrato.")
    return _saida(r)


class Pedido(BaseModel):
    dados: dict
    motivo: Optional[str] = None


@router.post("")
async def criar(p: Pedido, user=Depends(require_permission("reports", "read"))):
    """Contrato de cliente novo: é o único jeito de criar um grupo novo."""
    _so_ss(user)
    d = _limpar(p.dados)
    with _trava, _con() as c:
        outros = [json.loads(r[0]) for r in c.execute("SELECT dados FROM contrato")]
    _validar_novo(d, outros)
    with _trava, _con() as c:
        c.execute("INSERT INTO contrato (group_id, origem, status, dados, criado_em, atualizado_em, autor) VALUES (NULL,?,?,?,?,?,?)",
                  ("novo", "ativo", json.dumps(d, default=str), _agora(), _agora(), getattr(user, "user_id", None)))
        cid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO historico (contrato_id, acao, dados, autor, em) VALUES (?,?,?,?,?)",
                  (cid, "criado", json.dumps(d, default=str), getattr(user, "user_id", None), _agora()))
    return {"id": cid, "aviso": "Contrato criado. O grupo fica como \"a criar\" até a engenharia liberar a gravação no sistema atual."}


@router.put("/{cid}")
async def editar(cid: int, p: Pedido, user=Depends(require_permission("reports", "read"))):
    """Edita o contrato. Cliente existente: sem trava. Cliente novo: mesmas regras da criação."""
    _so_ss(user)
    with _trava, _con() as c:
        r = c.execute("SELECT * FROM contrato WHERE id = ?", (cid,)).fetchone()
        outros = [json.loads(x[0]) for x in c.execute("SELECT dados FROM contrato WHERE id <> ?", (cid,))]
    if not r:
        raise HTTPException(404, "Contrato não encontrado.")
    d = {**json.loads(r["dados"]), **_limpar(p.dados)}
    d = {k: v for k, v in d.items() if v not in (None, "")}
    if r["origem"] == "novo":
        _validar_novo(d, outros)
    with _trava, _con() as c:
        c.execute("UPDATE contrato SET dados = ?, atualizado_em = ?, autor = ? WHERE id = ?",
                  (json.dumps(d, default=str), _agora(), getattr(user, "user_id", None), cid))
        c.execute("INSERT INTO historico (contrato_id, acao, dados, motivo, autor, em) VALUES (?,?,?,?,?,?)",
                  (cid, "editado", json.dumps(_limpar(p.dados), default=str), p.motivo, getattr(user, "user_id", None), _agora()))
        r = c.execute("SELECT * FROM contrato WHERE id = ?", (cid,)).fetchone()
    return _saida(r)


@router.post("/{cid}/status")
async def mudar_status(cid: int, p: Pedido, user=Depends(require_permission("reports", "read"))):
    """Encerrar, suspender ou reativar — sempre manual. `dados.status` = ativo | suspenso | encerrado."""
    _so_ss(user)
    novo = str(p.dados.get("status") or "")
    if novo not in ("ativo", "suspenso", "encerrado"):
        raise HTTPException(422, {"message": "Situação inválida.", "field": "status"})
    if novo != "ativo" and not (p.motivo or "").strip():
        raise HTTPException(422, {"message": "Informe o motivo.", "field": "motivo"})
    with _trava, _con() as c:
        if not c.execute("SELECT 1 FROM contrato WHERE id = ?", (cid,)).fetchone():
            raise HTTPException(404, "Contrato não encontrado.")
        c.execute("UPDATE contrato SET status = ?, motivo_status = ?, atualizado_em = ? WHERE id = ?", (novo, p.motivo, _agora(), cid))
        c.execute("INSERT INTO historico (contrato_id, acao, motivo, autor, em) VALUES (?,?,?,?,?)",
                  (cid, f"status:{novo}", p.motivo, getattr(user, "user_id", None), _agora()))
        r = c.execute("SELECT * FROM contrato WHERE id = ?", (cid,)).fetchone()
    return _saida(r)


@router.get("/{cid}/historico")
async def historico(cid: int, user=Depends(require_permission("reports", "read"))):
    _so_ss(user)
    with _trava, _con() as c:
        return {"data": [dict(r) for r in c.execute("SELECT * FROM historico WHERE contrato_id = ? ORDER BY id DESC", (cid,))]}
