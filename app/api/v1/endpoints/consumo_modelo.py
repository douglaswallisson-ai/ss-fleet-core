"""Painel "Consumo por Modelo" do CCO. Regras em `app/core/consumo_modelo.py`.

Fontes: km, litros e tempo de `mova.con_driver_h_km`; modelo de `tracked_unit` → `vehicle_model`; diesel de
`mova.precos_combustivel` (ANP, média dos estados). A qualidade do dado não tem resumo pronto no banco: é
calculada dia a dia de `mova.dev_status_30` em segundo plano, um dia por vez (~6 s), e guardada em
`data/cco.sqlite`; só leitura no banco. Com/sem pedal: significado a confirmar, não entra (ver o front).
"""

import asyncio
import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.api.v1.endpoints.cco import ARQUIVO, _grupos_do_usuario
from app.core import combustivel as plaus, consumo_modelo as cm
from app.core.database import AsyncSessionLocalReplica
from app.core.logging import get_logger
from app.middleware.auth import require_permission

router = APIRouter()
logger = get_logger(__name__)

FUSO = ZoneInfo("America/Sao_Paulo")
DIESEL_ID = 2
CACHE_FECHADO_S = 6 * 3600
CACHE_ABERTO_S = 30 * 60
PAUSA_ENTRE_DIAS_S = 1.0
MESES_HISTORICO = 12
#: Passeio e Pickup ficam fora da qualidade do dado (PM, 31/10/2026): em muitos o rastreador não lê esses sinais, o
#: que é da instalação e não defeito (CECOTI: 35 "falhas" com eles, 19 sem eles). O consumo continua com todos.
CATEGORIAS_FORA_DA_QUALIDADE = (1, 2)

_cache: dict[tuple, tuple[float, object]] = {}
_tarefa: Optional[asyncio.Task] = None
_andamento = {"feitos": 0, "total": 0, "erro": None}

SQL_PERIODO = f"""
SELECT h.unit_id, h.group_id, nullif(trim(vm.nome), '') AS modelo,
       sum(h.distance_traveled_hist) / 1000.0 AS km,
       sum({plaus.km_com_combustivel_m("h")}) / 1000.0 AS km_comb,
       sum({plaus.litros_ml("h")}) / 1000.0 AS litros,
       sum(h.time_traveled_hist) AS segundos
FROM mova.con_driver_h_km h
LEFT JOIN mova.tracked_unit tu ON tu.id = h.unit_id
LEFT JOIN mova.vehicle_model vm ON vm.id = tu.vehicle_model_id
WHERE h.dt >= :a AND h.dt <= :b
GROUP BY 1, 2, 3
"""
SQL_DIA = """
SELECT d.unit_id, bool_or(d.can_rpm > 0 OR d.rpm > 0) AS rpm, bool_or(d.can_accel_pedal_percent > 0) AS pedal,
       bool_or(d.can_speed > 0) AS vel
FROM mova.dev_status_30 d
WHERE d.local_time >= :a AND d.local_time < :b AND d.unit_id IN (SELECT id FROM mova.tracked_unit WHERE status = 1)
GROUP BY 1
"""


async def _ler(sql: str, p: dict) -> list[dict]:
    async with AsyncSessionLocalReplica() as db:
        await db.execute(text("SET LOCAL statement_timeout = '120s'"))
        return [dict(r) for r in (await db.execute(text(sql), p)).mappings().all()]


def _con() -> sqlite3.Connection:
    c = sqlite3.connect(ARQUIVO, timeout=30)
    c.execute("""CREATE TABLE IF NOT EXISTS telemetria_dia (dia TEXT, unit_id INTEGER, rpm INTEGER, pedal INTEGER, vel INTEGER,
                 PRIMARY KEY (dia, unit_id)) WITHOUT ROWID""")
    c.execute("CREATE TABLE IF NOT EXISTS telemetria_dia_feito (dia TEXT PRIMARY KEY, em TEXT)")
    return c


async def _cacheado(chave: tuple, ttl: float, fn):
    hit = _cache.get(chave)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = await fn()
    _cache[chave] = (time.time(), v)
    return v


async def _linhas(a: date, b: date, fechado: bool) -> list[dict]:
    async def ler():
        rows = await _ler(SQL_PERIODO, {"a": a, "b": b})
        return [{"unit_id": r["unit_id"], "group_id": r["group_id"], "modelo": r["modelo"], "km": float(r["km"] or 0),
                 "km_comb": float(r["km_comb"] or 0), "litros": float(r["litros"] or 0), "segundos": float(r["segundos"] or 0)} for r in rows]
    return await _cacheado(("linhas", a, b), CACHE_FECHADO_S if fechado else CACHE_ABERTO_S, ler)


async def _diesel(a: date, b: date) -> Optional[float]:
    async def ler():
        r = await _ler("SELECT avg(preco) AS p FROM mova.precos_combustivel WHERE combustivel_id = :c AND data_referencia >= :a AND data_referencia <= :b",
                       {"c": DIESEL_ID, "a": a, "b": b})
        return float(r[0]["p"]) if r and r[0]["p"] is not None else None
    return await _cacheado(("diesel", a, b), CACHE_ABERTO_S, ler)


async def _ultimo_dado(hoje: date) -> Optional[date]:
    async def ler():
        r = await _ler("SELECT max(dt) AS m FROM mova.con_driver_h_km WHERE dt >= :a AND dt <= :b", {"a": hoje - timedelta(days=40), "b": hoje})
        return r[0]["m"] if r else None
    return await _cacheado(("ultimo", hoje), CACHE_ABERTO_S, ler)


async def _ativos() -> dict[int, int]:
    async def ler():
        return {r["id"]: r["group_id"] for r in await _ler("SELECT id, group_id FROM mova.tracked_unit WHERE status = 1", {})}
    return await _cacheado(("ativos",), CACHE_ABERTO_S, ler)


async def _fora_da_qualidade() -> set[int]:
    async def ler():
        rows = await _ler("SELECT id FROM mova.tracked_unit WHERE unit_category_id = ANY(:c)", {"c": list(CATEGORIAS_FORA_DA_QUALIDADE)})
        return {r["id"] for r in rows}
    return await _cacheado(("fora_qualidade",), CACHE_ABERTO_S, ler)


async def _calcular_historico(dias: list[date]) -> None:
    """Um dia por vez, do mais recente para o mais antigo: o painel se completa de trás para frente."""
    with _con() as c:
        feitos = {r[0] for r in c.execute("SELECT dia FROM telemetria_dia_feito")}
    faltam = [d for d in dias if d.isoformat() not in feitos]
    _andamento.update(feitos=len(dias) - len(faltam), total=len(dias), erro=None)
    for d in faltam:
        rows = await _ler(SQL_DIA, {"a": datetime.combine(d, datetime.min.time()), "b": datetime.combine(d + timedelta(days=1), datetime.min.time())})
        with _con() as c:
            c.executemany("INSERT OR REPLACE INTO telemetria_dia VALUES (?, ?, ?, ?, ?)",
                          [(d.isoformat(), r["unit_id"], int(bool(r["rpm"])), int(bool(r["pedal"])), int(bool(r["vel"]))) for r in rows])
            c.execute("INSERT OR REPLACE INTO telemetria_dia_feito VALUES (?, ?)", (d.isoformat(), datetime.now(FUSO).isoformat(timespec="seconds")))
        _andamento["feitos"] += 1
        await asyncio.sleep(PAUSA_ENTRE_DIAS_S)


def _iniciar_historico(hoje: date) -> None:
    global _tarefa
    if _tarefa and not _tarefa.done():
        return

    async def rodar():
        try:
            await _calcular_historico(cm.dias_do_historico(hoje, MESES_HISTORICO))
        except Exception as e:  # o painel segue sem a qualidade; o próximo acesso tenta de novo
            _andamento["erro"] = str(e)
            logger.error("consumo_modelo_historico_falhou", error=str(e))

    _tarefa = asyncio.create_task(rodar())


def _ler_qualidade(a: date, b: date) -> Optional[tuple[dict, set, dict]]:
    """Sinais do período, quem leu no último dia e a primeira leitura; None se faltar dia calculado."""
    ai, bi = a.isoformat(), b.isoformat()
    with _con() as c:
        feitos = c.execute("SELECT count(*) FROM telemetria_dia_feito WHERE dia >= ? AND dia <= ?", (ai, bi)).fetchone()[0]
        if feitos < (b - a).days + 1:
            return None
        sinais = {r[0]: (r[1], r[2], r[3]) for r in c.execute(
            "SELECT unit_id, max(rpm), max(pedal), max(vel) FROM telemetria_dia WHERE dia >= ? AND dia <= ? GROUP BY unit_id", (ai, bi))}
        fim = {r[0] for r in c.execute("SELECT unit_id FROM telemetria_dia WHERE dia = ?", (bi,))}
        prim = {r[0]: date.fromisoformat(r[1]) for r in c.execute("SELECT unit_id, min(dia) FROM telemetria_dia GROUP BY unit_id")}
    return sinais, fim, prim


@router.get("")
async def consumo_por_modelo(group_id: Optional[int] = Query(None), meses: int = Query(6, ge=3, le=12),
                             user=Depends(require_permission("reports", "read"))):
    """km/l por modelo, mês a mês; diesel, R$/km e velocidade; veículos com falha de dados de telemetria."""
    grupos = _grupos_do_usuario(user, group_id)
    agora = datetime.now(FUSO)
    hoje = agora.date()
    ultimo = await _ultimo_dado(hoje)
    colunas = cm.periodos(hoje, ultimo, meses)
    _iniciar_historico(hoje)
    ativos_todos = await _ativos()
    fora = await _fora_da_qualidade()
    ativos = {u for u, g in ativos_todos.items() if (grupos is None or g in grupos) and u not in fora}

    modelos: dict[str, dict] = {}
    frota, diesel, custo, veloc = {}, {}, {}, {}
    q = {k: {} for k in ("rpm", "pedal", "velocidade", "offline", "total")}
    for col in colunas:
        k, a, b = col["chave"], col["inicio"], col["fim"]
        linhas = await _linhas(a, b, fechado=col["tipo"] == "mes")
        if grupos is not None:
            linhas = [r for r in linhas if r["group_id"] in grupos]
        celulas, f = cm.agregar(linhas)
        for m, c in celulas.items():
            modelos.setdefault(m, {})[k] = c
        veloc[k] = f.pop("velocidade")
        frota[k] = f
        preco = await _diesel(a, b)
        diesel[k] = round(preco, 2) if preco else None
        custo[k] = round(preco / f["kml"], 2) if preco and f["kml"] and not f["insuficiente"] else None
        lido = await asyncio.to_thread(_ler_qualidade, a, b)
        if lido:
            rodaram = {r["unit_id"] for r in linhas if r["km"] > cm.KM_MIN_QUALIDADE and r["unit_id"] not in fora}
            res = cm.qualidade(rodaram, lido[0], ativos, lido[1], lido[2], b)
        else:
            res = {x: None for x in q}
        for x in q:
            q[x][k] = res[x]

    def peso(m: str) -> tuple:
        return (m == cm.SEM_MODELO, -sum(c["km"] for c in modelos[m].values()), m)

    return {
        "colunas": [{**c, "inicio": c["inicio"].isoformat(), "fim": c["fim"].isoformat()} for c in colunas],
        "modelos": [{"modelo": m, "celulas": modelos[m]} for m in sorted(modelos, key=peso)],
        "frota": frota, "diesel": diesel, "custo_km": custo, "velocidade": veloc, "qualidade": q,
        "qualidade_andamento": dict(_andamento), "km_minimo": cm.KM_MINIMO,
        "ultimo_dado": ultimo.isoformat() if ultimo else None, "atualizado_em": agora.isoformat(timespec="minutes"),
        "fonte": "Telemetria SS (con_driver_h_km e dev_status_30); diesel: ANP, média dos estados no período.",
    }
