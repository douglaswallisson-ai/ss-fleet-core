"""Quadro de multas. Regras em `app/core/multas.py`; dados oficiais em `app/core/multas_efrotas.py`.

As multas e a auditoria ficam em `data/cco.sqlite` (o banco de produção é só leitura). Veículo, motorista e telemetria
vêm do banco, só leitura. Nome e dados do motorista e o relatório exigem a permissão `drivers.read`.
"""

import asyncio
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel

from app.api.v1.endpoints.cco import ARQUIVO, _grupos_do_usuario, _ler
from app.core import geocodificador, multas as mu, multas_efrotas, multas_exemplos
from app.core.logging import get_logger
from app.middleware.auth import require_permission

router = APIRouter()
logger = get_logger(__name__)

FUSO = ZoneInfo("America/Sao_Paulo")
SINCRONIZAR_A_CADA_S = 3600
#: A leitura do rastreador mais próxima da autuação só vale até esta distância no tempo.
JANELA_TELEMETRIA_MIN = 10
BOLETO_MAX_BYTES = 5 * 1024 * 1024
_trava = threading.Lock()
_tarefa: Optional[asyncio.Task] = None
_ultima_tentativa = 0.0


def _con() -> sqlite3.Connection:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(ARQUIVO)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS multa (
            id INTEGER PRIMARY KEY, ait TEXT UNIQUE NOT NULL, placa TEXT NOT NULL, unit_id INTEGER, group_id INTEGER,
            veiculo TEXT, motorista TEXT, driver_id INTEGER, uf TEXT, orgao TEXT, infracao TEXT, local TEXT, valor REAL,
            autuada_em TEXT, prazo_recurso TEXT, prazo_pagamento TEXT, status_oficial TEXT NOT NULL, coluna TEXT NOT NULL,
            pontos INTEGER, codigo_barras TEXT, pix TEXT);
        CREATE TABLE IF NOT EXISTS multa_boleto (multa_id INTEGER PRIMARY KEY, nome TEXT, pdf BLOB NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_multa_grupo ON multa (group_id);
        CREATE TABLE IF NOT EXISTS multa_auditoria (
            id INTEGER PRIMARY KEY, multa_id INTEGER NOT NULL, tipo TEXT NOT NULL, origem TEXT NOT NULL, usuario_id INTEGER,
            em TEXT NOT NULL, de TEXT, para TEXT);
        CREATE INDEX IF NOT EXISTS ix_multa_auditoria ON multa_auditoria (multa_id);
        CREATE TABLE IF NOT EXISTS multa_sincronizacao (id INTEGER PRIMARY KEY CHECK (id = 1), situacao TEXT, ultima_em TEXT, tentativa_em TEXT);
        CREATE TABLE IF NOT EXISTS multa_aviso_visto (usuario_id INTEGER PRIMARY KEY, ultimo_id INTEGER NOT NULL);
    """)
    return c


def _agora() -> str:
    return datetime.now(FUSO).isoformat(timespec="seconds")


def _auditar(c, multa_id: int, tipo: str, origem: str, usuario_id: Optional[int], de: Optional[str], para: Optional[str]) -> None:
    c.execute("INSERT INTO multa_auditoria (multa_id, tipo, origem, usuario_id, em, de, para) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (multa_id, tipo, origem, usuario_id, _agora(), de, para))


def _so_placa(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


async def _veiculos_por_placa(placas: set[str]) -> dict[str, dict]:
    if not placas:
        return {}
    rows = await _ler("""
        SELECT tu.id AS unit_id, tu.group_id, tu.label,
               nullif(trim(concat_ws(' ', vf.nome, vm.nome)), '') AS veiculo
        FROM mova.tracked_unit tu
        LEFT JOIN mova.vehicle_model vm ON vm.id = tu.vehicle_model_id
        LEFT JOIN mova.vehicle_manufacturer vf ON vf.id = vm.manufacturer_id
        WHERE tu.status = 1 AND regexp_replace(upper(tu.label), '[^A-Z0-9]', '', 'g') = ANY(:p)""", {"p": list(placas)})
    return {_so_placa(r["label"]): r for r in rows}


async def _leitura_do_instante(unit_id: int, quando: str) -> Optional[dict]:
    """Leitura do rastreador mais próxima do horário da multa."""
    t = datetime.fromisoformat(quando).replace(tzinfo=None)
    rows = await _ler("""
        SELECT d.local_time, d.latitude, d.longitude, d.address, d.speed, d.can_speed, coalesce(nullif(d.can_rpm, 0), d.rpm) AS rpm,
               fx.name AS faixa, nullif(nullif(trim(d.driver_name), ''), 'Não Informado') AS motorista, nullif(d.driver_id, 0) AS driver_id
        FROM mova.dev_status_30 d
        LEFT JOIN mova.faixas fx ON fx.id = d.faixa
        WHERE d.unit_id = :u AND d.local_time >= :a AND d.local_time <= :b
        ORDER BY abs(extract(epoch FROM d.local_time - :t)) LIMIT 1""",
                      {"u": unit_id, "t": t, "a": t - timedelta(minutes=JANELA_TELEMETRIA_MIN), "b": t + timedelta(minutes=JANELA_TELEMETRIA_MIN)})
    return rows[0] if rows else None


def aplicar_sincronizacao(registros: list[mu.RegistroOficial], veiculos: dict[str, dict], motoristas: dict[str, dict]) -> dict:
    """Grava o que veio do órgão. O status oficial prevalece e toda mudança de coluna entra na auditoria."""
    recusados = novos = movidos = 0
    with _trava, _con() as c:
        for r in registros:
            motivo = mu.validar(r)
            if motivo:
                recusados += 1
                logger.warning("multa_recusada", motivo=motivo)
                continue
            antes = c.execute("SELECT id, coluna, status_oficial FROM multa WHERE ait = ?", (r.ait,)).fetchone()
            coluna = mu.coluna_ao_sincronizar(antes["coluna"] if antes else None, antes["status_oficial"] if antes else None, r.status)
            v = veiculos.get(_so_placa(r.placa)) or {}
            dados = (r.placa, v.get("unit_id"), v.get("group_id"), v.get("veiculo"), r.uf, r.orgao, r.infracao, r.local, r.valor,
                     r.autuada_em, r.prazo_recurso, r.prazo_pagamento, r.status, coluna, r.pontos, r.codigo_barras, r.pix)
            if antes:
                c.execute("""UPDATE multa SET placa=?, unit_id=?, group_id=?, veiculo=?, uf=?, orgao=?, infracao=?, local=?, valor=?,
                             autuada_em=?, prazo_recurso=?, prazo_pagamento=?, status_oficial=?, coluna=?, pontos=?, codigo_barras=?, pix=? WHERE id=?""", (*dados, antes["id"]))
                if antes["coluna"] != coluna:
                    movidos += 1
                    _auditar(c, antes["id"], "movimentacao", "oficial", None, antes["coluna"], coluna)
            else:
                m = motoristas.get(r.ait) or {}
                cur = c.execute("""INSERT INTO multa (placa, unit_id, group_id, veiculo, uf, orgao, infracao, local, valor, autuada_em,
                                   prazo_recurso, prazo_pagamento, status_oficial, coluna, pontos, codigo_barras, pix, ait, motorista, driver_id)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (*dados, r.ait, m.get("motorista"), m.get("driver_id")))
                novos += 1
                _auditar(c, cur.lastrowid, "movimentacao", "oficial", None, None, coluna)
    return {"novos": novos, "movidos": movidos, "recusados": recusados}


def _gravar_situacao(situacao: str, ok: bool) -> None:
    agora = _agora()
    with _trava, _con() as c:
        c.execute("""INSERT INTO multa_sincronizacao (id, situacao, ultima_em, tentativa_em) VALUES (1, ?, ?, ?)
                     ON CONFLICT (id) DO UPDATE SET situacao = excluded.situacao, tentativa_em = excluded.tentativa_em,
                     ultima_em = coalesce(excluded.ultima_em, ultima_em)""", (situacao, agora if ok else None, agora))


def _apagar_exemplos() -> None:
    with _trava, _con() as c:
        ids = f"SELECT id FROM multa WHERE ait LIKE '{multas_exemplos.PREFIXO}%'"
        c.execute(f"DELETE FROM multa_auditoria WHERE multa_id IN ({ids})")
        c.execute(f"DELETE FROM multa_boleto WHERE multa_id IN ({ids})")
        c.execute(f"DELETE FROM multa WHERE ait LIKE '{multas_exemplos.PREFIXO}%'")


async def sincronizar() -> None:
    """Se falhar, os últimos dados ficam e a tela avisa."""
    try:
        registros = await multas_efrotas.buscar()
        veiculos = await _veiculos_por_placa({_so_placa(r.placa) for r in registros})
        with _con() as c:
            conhecidas = {x[0] for x in c.execute("SELECT ait FROM multa")}
        motoristas = {}
        for r in registros:
            v = veiculos.get(_so_placa(r.placa))
            if v and r.ait not in conhecidas and not mu.validar(r):
                motoristas[r.ait] = await _leitura_do_instante(v["unit_id"], r.autuada_em) or {}
        await asyncio.to_thread(_apagar_exemplos)
        await asyncio.to_thread(aplicar_sincronizacao, registros, veiculos, motoristas)
        _gravar_situacao("ok", True)
    except multas_efrotas.IntegracaoNaoConfigurada:
        await asyncio.to_thread(aplicar_sincronizacao, multas_exemplos.registros(datetime.now(FUSO).date()), {}, multas_exemplos.motoristas())
        _gravar_situacao("nao_configurada", False)
    except Exception as e:
        logger.error("multas_sincronizacao_falhou", error=str(e))
        _gravar_situacao("falha", False)


def _iniciar_sincronizacao() -> None:
    global _tarefa, _ultima_tentativa
    if (_tarefa and not _tarefa.done()) or time.time() - _ultima_tentativa < SINCRONIZAR_A_CADA_S:
        return
    _ultima_tentativa = time.time()
    _tarefa = asyncio.create_task(sincronizar())


def _pode_dados_pessoais(user) -> bool:
    return user.has_permission("drivers", "read")


def _exemplo(m: dict) -> bool:
    return m["ait"].startswith(multas_exemplos.PREFIXO)


def _visivel(user, grupos: Optional[list[int]], m) -> bool:
    """Exemplo é só para a SS, em qualquer empresa escolhida; cliente nunca vê."""
    if m["ait"].startswith(multas_exemplos.PREFIXO):
        return bool(getattr(user, "master", 0) or getattr(user, "is_super_admin", False))
    return grupos is None or m["group_id"] in grupos


SQL_MULTA = "SELECT m.*, EXISTS (SELECT 1 FROM multa_boleto b WHERE b.multa_id = m.id) AS tem_boleto FROM multa m"


def _card(m: dict, pessoais: bool, hoje) -> dict:
    out = {k: m[k] for k in ("id", "ait", "placa", "veiculo", "uf", "orgao", "infracao", "local", "valor", "autuada_em",
                             "prazo_recurso", "prazo_pagamento", "status_oficial", "coluna", "pontos", "codigo_barras", "pix")}
    out["tem_boleto"] = bool(m["tem_boleto"])
    out["exemplo"] = _exemplo(m)
    out["motorista"] = (m["motorista"] or "Não identificado") if pessoais else None
    out["destaque_recurso"] = mu.prazo_em_destaque(m["prazo_recurso"], m["coluna"], hoje)
    out["destaque_pagamento"] = mu.prazo_em_destaque(m["prazo_pagamento"], m["coluna"], hoje)
    return out


def _multa_acessivel(user, multa_id: int) -> dict:
    with _con() as c:
        r = c.execute(SQL_MULTA + " WHERE m.id = ?", (multa_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Multa não encontrada.")
    if not _visivel(user, _grupos_do_usuario(user, None), r):
        raise HTTPException(403, "Sem acesso a esta multa.")
    return dict(r)


@router.get("")
async def quadro(group_id: Optional[int] = Query(None), user=Depends(require_permission("reports", "read"))):
    """Colunas com quantidade, valor e cards; situação da sincronização; mudanças oficiais ainda não vistas pelo usuário."""
    grupos = _grupos_do_usuario(user, group_id)
    _iniciar_sincronizacao()
    pessoais = _pode_dados_pessoais(user)
    hoje = datetime.now(FUSO).date()
    with _con() as c:
        rows = [dict(r) for r in c.execute(SQL_MULTA) if _visivel(user, grupos, r)]
        sync = c.execute("SELECT situacao, ultima_em, tentativa_em FROM multa_sincronizacao WHERE id = 1").fetchone()
        visto = c.execute("SELECT ultimo_id FROM multa_aviso_visto WHERE usuario_id = ?", (user.user_id,)).fetchone()
        avisos = c.execute("""SELECT a.id, a.multa_id, a.em, a.de, a.para FROM multa_auditoria a WHERE a.origem = 'oficial' AND a.de IS NOT NULL
                              AND a.id > ? ORDER BY a.id DESC""", (visto["ultimo_id"] if visto else 0,)).fetchall()
    por_id = {m["id"]: m for m in rows}
    return {
        "colunas": mu.montar_quadro([_card(m, pessoais, hoje) for m in rows]),
        "sincronizacao": dict(sync) if sync else {"situacao": "nao_configurada", "ultima_em": None, "tentativa_em": None},
        "avisos": [{**dict(a), "ait": por_id[a["multa_id"]]["ait"], "placa": por_id[a["multa_id"]]["placa"]} for a in avisos if a["multa_id"] in por_id],
        "pode_dados_pessoais": pessoais,
        "aviso_so_oficial": mu.AVISO_SO_OFICIAL,
    }


class Mover(BaseModel):
    para: str


@router.post("/{multa_id}/mover")
async def mover(multa_id: int, corpo: Mover, user=Depends(require_permission("reports", "read"))):
    m = _multa_acessivel(user, multa_id)
    motivo = mu.recusa_movimento(m["coluna"], corpo.para)
    if motivo:
        raise HTTPException(409, motivo)
    with _trava, _con() as c:
        c.execute("UPDATE multa SET coluna = ? WHERE id = ?", (corpo.para, multa_id))
        _auditar(c, multa_id, "movimentacao", "usuario", user.user_id, m["coluna"], corpo.para)
    return {"coluna": corpo.para}


class AvisosVistos(BaseModel):
    ultimo_id: int


@router.post("/avisos/visto")
async def marcar_avisos_vistos(corpo: AvisosVistos, user=Depends(require_permission("reports", "read"))):
    with _trava, _con() as c:
        c.execute("""INSERT INTO multa_aviso_visto (usuario_id, ultimo_id) VALUES (?, ?)
                     ON CONFLICT (usuario_id) DO UPDATE SET ultimo_id = max(ultimo_id, excluded.ultimo_id)""", (user.user_id, corpo.ultimo_id))
    return {"ok": True}


async def _detalhe(m: dict, pessoais: bool) -> dict:
    if _exemplo(m):
        leitura = multas_exemplos.leitura(m["ait"], m["autuada_em"])
    else:
        leitura = await _leitura_do_instante(m["unit_id"], m["autuada_em"]) if m["unit_id"] and m["autuada_em"] else None
    aproximado = None
    if m["local"]:
        g = await geocodificador.localizar(f'{m["local"]}, {m["uf"]}, Brasil' if m["uf"] else m["local"])
        if g.get("lat") is not None:
            aproximado = {"lat": g["lat"], "lng": g["lng"], "precisao": g.get("precisao")}
    motorista = None
    if pessoais:
        driver_id = m["driver_id"] or (leitura or {}).get("driver_id")
        nome = m["motorista"] or (leitura or {}).get("motorista")
        motorista = {"nome": nome or "Não identificado"}
        if driver_id:
            r = await _ler("""SELECT name AS nome, matricula, phone AS telefone, cnh_category AS cnh_categoria, cnh_validate AS cnh_validade
                              FROM mova.driver WHERE id = :d""", {"d": driver_id})
            if r:
                motorista = {**r[0], "nome": r[0]["nome"] or motorista["nome"]}
    with _con() as c:
        auditoria = [dict(a) for a in c.execute("SELECT * FROM multa_auditoria WHERE multa_id = ? ORDER BY id DESC", (m["id"],))]
    card = _card(m, pessoais, datetime.now(FUSO).date())
    if leitura and isinstance(leitura["local_time"], datetime):
        leitura = {**leitura, "local_time": leitura["local_time"].isoformat()}
    if leitura:
        leitura = {k: v for k, v in leitura.items() if k not in ("motorista", "driver_id")}
    return {"multa": card, "motorista": motorista, "telemetria": leitura, "local_aproximado": aproximado, "auditoria": auditoria,
            "pode_dados_pessoais": pessoais, "janela_telemetria_min": JANELA_TELEMETRIA_MIN}


@router.get("/{multa_id}")
async def detalhe(multa_id: int, user=Depends(require_permission("reports", "read"))):
    return await _detalhe(_multa_acessivel(user, multa_id), _pode_dados_pessoais(user))


@router.post("/{multa_id}/relatorio")
async def relatorio(multa_id: int, user=Depends(require_permission("drivers", "read"))):
    """Dados do relatório de contestação; a geração fica na auditoria antes de a tela montar o PDF."""
    m = _multa_acessivel(user, multa_id)
    with _trava, _con() as c:
        _auditar(c, multa_id, "relatorio", "usuario", user.user_id, None, None)
    return {**await _detalhe(m, True), "gerado_em": _agora()}


@router.post("/{multa_id}/boleto")
async def anexar_boleto(multa_id: int, arquivo: UploadFile = File(...), user=Depends(require_permission("reports", "read"))):
    _multa_acessivel(user, multa_id)
    pdf = await arquivo.read(BOLETO_MAX_BYTES + 1)
    if len(pdf) > BOLETO_MAX_BYTES:
        raise HTTPException(413, "O boleto pode ter até 5 MB.")
    if not pdf.startswith(b"%PDF"):
        raise HTTPException(415, "Anexe o boleto em PDF.")
    with _trava, _con() as c:
        c.execute("INSERT OR REPLACE INTO multa_boleto (multa_id, nome, pdf) VALUES (?, ?, ?)", (multa_id, arquivo.filename, pdf))
        _auditar(c, multa_id, "boleto", "usuario", user.user_id, None, None)
    return {"ok": True}


@router.get("/{multa_id}/boleto")
async def baixar_boleto(multa_id: int, user=Depends(require_permission("reports", "read"))):
    _multa_acessivel(user, multa_id)
    with _con() as c:
        r = c.execute("SELECT pdf FROM multa_boleto WHERE multa_id = ?", (multa_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Esta multa não tem boleto anexado.")
    return Response(r["pdf"], media_type="application/pdf")
