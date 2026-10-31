"""Painel Operacional do CCO. Regras em `app/core/painel_operacional.py`.

Fontes: km, litros e tempo de `mova.con_driver_h_km`; modelo de `tracked_unit` → `vehicle_model`; diesel de
`mova.precos_combustivel` (ANP, média dos estados). A qualidade do dado não tem resumo pronto no banco: é
calculada dia a dia de `mova.dev_status_30` em segundo plano, um dia por vez (~6 s), e guardada em
`data/cco.sqlite`; só leitura no banco. Com/sem pedal: significado a confirmar, não entra (ver o front).

Hoje: `mova.con_telemetry`, que recebe as viagens ao longo do dia. A rota `/hoje` é leve e fica em cache por
`CACHE_HOJE_S`: a tela busca com essa frequência, e o dado novo chega conforme os rastreadores mandam (1 a 2 min).
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
from app.core import combustivel as plaus, painel_operacional as cm
from app.core.database import AsyncSessionLocalReplica
from app.core.logging import get_logger
from app.middleware.auth import require_permission

router = APIRouter()
logger = get_logger(__name__)

FUSO = ZoneInfo("America/Sao_Paulo")
DIESEL_ID = 2
CACHE_FECHADO_S = 6 * 3600
CACHE_ABERTO_S = 30 * 60
CACHE_HOJE_S = 10
#: Altitude do rastreador, com o veículo andando. Zero é "não mandou"; acima disso é erro de GPS (10.859 leituras em 29/10).
ALTITUDE_MAX_M = 3000
CACHE_SINAIS_HOJE_S = 5 * 60
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
SQL_HOJE = f"""
SELECT ct.unit_id, tu.group_id, nullif(trim(vm.nome), '') AS modelo,
       sum(ct.distance_traveled) / 1000.0 AS km,
       sum(CASE WHEN ct.fuel_used > 0 AND ct.distance_traveled::float / ct.fuel_used BETWEEN {cm.KML_VIAGEM_MIN} AND {cm.KML_VIAGEM_MAX}
                THEN ct.distance_traveled ELSE 0 END) / 1000.0 AS km_comb,
       sum(CASE WHEN ct.fuel_used > 0 AND ct.distance_traveled::float / ct.fuel_used BETWEEN {cm.KML_VIAGEM_MIN} AND {cm.KML_VIAGEM_MAX}
                THEN ct.fuel_used ELSE 0 END) / 1000.0 AS litros,
       sum(ct.total_time) AS segundos
FROM mova.con_telemetry ct
JOIN mova.tracked_unit tu ON tu.id = ct.unit_id
LEFT JOIN mova.vehicle_model vm ON vm.id = tu.vehicle_model_id
WHERE ct.start_time >= :a AND ct.start_time < :b
GROUP BY 1, 2, 3
"""
SQL_DIA = """
SELECT d.unit_id, bool_or(d.can_rpm > 0 OR d.rpm > 0) AS rpm, bool_or(d.can_accel_pedal_percent > 0) AS pedal,
       bool_or(d.can_speed > 0) AS vel,
       sum(d.altitude) FILTER (WHERE d.speed > 5 AND d.altitude > 0 AND d.altitude <= :alt_max) AS alt_soma,
       count(*) FILTER (WHERE d.speed > 5 AND d.altitude > 0 AND d.altitude <= :alt_max) AS alt_n
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
    # Tabela à parte: a altitude entrou depois e o histórico dela é calculado de novo, sem mexer no que já estava pronto.
    c.execute("""CREATE TABLE IF NOT EXISTS altitude_dia (dia TEXT, unit_id INTEGER, soma REAL, n INTEGER,
                 PRIMARY KEY (dia, unit_id)) WITHOUT ROWID""")
    c.execute("CREATE TABLE IF NOT EXISTS altitude_dia_feito (dia TEXT PRIMARY KEY, em TEXT)")
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
        r = await _ler("SELECT dt, count(DISTINCT unit_id) AS n FROM mova.con_driver_h_km WHERE dt >= :a AND dt <= :b GROUP BY 1",
                       {"a": hoje - timedelta(days=40), "b": hoje})
        return cm.ultimo_dia_completo({x["dt"]: int(x["n"]) for x in r})
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
        feitos = {r[0] for r in c.execute("SELECT dia FROM altitude_dia_feito")}
    faltam = [d for d in dias if d.isoformat() not in feitos]
    _andamento.update(feitos=len(dias) - len(faltam), total=len(dias), erro=None)
    for d in faltam:
        rows = await _ler(SQL_DIA, {"a": datetime.combine(d, datetime.min.time()), "b": datetime.combine(d + timedelta(days=1), datetime.min.time()), "alt_max": ALTITUDE_MAX_M})
        with _con() as c:
            c.executemany("INSERT OR REPLACE INTO telemetria_dia VALUES (?, ?, ?, ?, ?)",
                          [(d.isoformat(), r["unit_id"], int(bool(r["rpm"])), int(bool(r["pedal"])), int(bool(r["vel"]))) for r in rows])
            c.executemany("INSERT OR REPLACE INTO altitude_dia VALUES (?, ?, ?, ?)",
                          [(d.isoformat(), r["unit_id"], float(r["alt_soma"]), int(r["alt_n"])) for r in rows if r["alt_n"]])
            em = datetime.now(FUSO).isoformat(timespec="seconds")
            c.execute("INSERT OR REPLACE INTO telemetria_dia_feito VALUES (?, ?)", (d.isoformat(), em))
            c.execute("INSERT OR REPLACE INTO altitude_dia_feito VALUES (?, ?)", (d.isoformat(), em))
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
            logger.error("painel_operacional_historico_falhou", error=str(e))

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


async def _diesel_atual() -> Optional[float]:
    async def ler():
        r = await _ler("""SELECT avg(preco) AS p FROM mova.precos_combustivel WHERE combustivel_id = :c AND data_referencia =
                          (SELECT max(data_referencia) FROM mova.precos_combustivel WHERE combustivel_id = :c)""", {"c": DIESEL_ID})
        return float(r[0]["p"]) if r and r[0]["p"] is not None else None
    return await _cacheado(("diesel_atual",), CACHE_ABERTO_S, ler)


def _primeiras_leituras() -> dict[int, date]:
    with _con() as c:
        return {r[0]: date.fromisoformat(r[1]) for r in c.execute("SELECT unit_id, min(dia) FROM telemetria_dia GROUP BY unit_id")}


def _ler_altitude(a: date, b: date, unidades: set[int]) -> Optional[int]:
    """Altitude média dos veículos, ponderada pelas leituras; None se faltar dia calculado ou ninguém mandar altitude."""
    ai, bi = a.isoformat(), b.isoformat()
    with _con() as c:
        feitos = c.execute("SELECT count(*) FROM altitude_dia_feito WHERE dia >= ? AND dia <= ?", (ai, bi)).fetchone()[0]
        if feitos < (b - a).days + 1:
            return None
        rows = c.execute("SELECT unit_id, sum(soma), sum(n) FROM altitude_dia WHERE dia >= ? AND dia <= ? GROUP BY unit_id", (ai, bi)).fetchall()
    return cm.altitude_media({u: (soma, n) for u, soma, n in rows if u in unidades})


@router.get("/hoje")
async def hoje_ao_vivo(group_id: Optional[int] = Query(None), user=Depends(require_permission("reports", "read"))):
    """Coluna de hoje: viagens do dia até agora. Cache curto e por toda a frota, para muitas telas não pesarem no banco."""
    grupos = _grupos_do_usuario(user, group_id)
    agora = datetime.now(FUSO)
    ini = datetime.combine(agora.date(), datetime.min.time())
    fim = ini + timedelta(days=1)

    async def ler_viagens():
        rows = await _ler(SQL_HOJE, {"a": ini, "b": fim})
        return {"lido_em": datetime.now(FUSO).isoformat(timespec="seconds"),
                "linhas": [{"unit_id": r["unit_id"], "group_id": r["group_id"], "modelo": r["modelo"], "km": float(r["km"] or 0),
                            "km_comb": float(r["km_comb"] or 0), "litros": float(r["litros"] or 0), "segundos": float(r["segundos"] or 0)} for r in rows]}

    async def ler_sinais():
        rows = await _ler(SQL_DIA, {"a": ini, "b": fim, "alt_max": ALTITUDE_MAX_M})
        return {r["unit_id"]: (int(bool(r["rpm"])), int(bool(r["pedal"])), int(bool(r["vel"])), float(r["alt_soma"] or 0), int(r["alt_n"] or 0)) for r in rows}

    async def ler_recentes():
        rows = await _ler("SELECT unit_id FROM mova.dev_status WHERE local_time >= :d AND local_time < :b",
                          {"d": agora.replace(tzinfo=None) - timedelta(hours=24), "b": fim})
        return {r["unit_id"] for r in rows}

    viagens = await _cacheado(("hoje", ini), CACHE_HOJE_S, ler_viagens)
    sinais = await _cacheado(("sinais_hoje", ini), CACHE_SINAIS_HOJE_S, ler_sinais)
    recentes = await _cacheado(("recentes", ini), CACHE_SINAIS_HOJE_S, ler_recentes)
    primeiras = await _cacheado(("primeiras", ini), CACHE_ABERTO_S, lambda: asyncio.to_thread(_primeiras_leituras))
    linhas = [r for r in viagens["linhas"] if grupos is None or r["group_id"] in grupos]
    todos = await _ativos()
    altitude = cm.altitude_media({u: (x[3], x[4]) for u, x in sinais.items() if grupos is None or todos.get(u) in grupos})
    celulas, f = cm.agregar(linhas, cm.KM_MINIMO_HOJE)
    velocidade = f.pop("velocidade")
    preco = await _diesel_atual()
    fora = await _fora_da_qualidade()
    ativos = {u for u, g in (await _ativos()).items() if (grupos is None or g in grupos) and u not in fora}
    rodaram = {r["unit_id"] for r in linhas if r["km"] > cm.KM_MIN_QUALIDADE and r["unit_id"] not in fora}
    return {
        "modelos": celulas, "frota": f, "velocidade": velocidade, "altitude": altitude, "diesel": round(preco, 2) if preco else None,
        "custo_km": round(preco / f["kml"], 2) if preco and f["kml"] and not f["insuficiente"] else None,
        "qualidade": cm.qualidade(rodaram, sinais, ativos, recentes, primeiras, agora.date()),
        "km_minimo": cm.KM_MINIMO_HOJE, "lido_em": viagens["lido_em"], "intervalo_s": CACHE_HOJE_S,
    }


@router.get("")
async def painel_operacional(group_id: Optional[int] = Query(None), meses: int = Query(6, ge=3, le=12),
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
    frota, diesel, custo, veloc, altitude = {}, {}, {}, {}, {}
    unidades = {u for u, g in ativos_todos.items() if grupos is None or g in grupos}
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
        altitude[k] = await asyncio.to_thread(_ler_altitude, a, b, unidades)
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
        "frota": frota, "diesel": diesel, "custo_km": custo, "velocidade": veloc, "altitude": altitude, "qualidade": q,
        "qualidade_andamento": dict(_andamento), "km_minimo": cm.KM_MINIMO,
        "ultimo_dado": ultimo.isoformat() if ultimo else None, "atualizado_em": agora.isoformat(timespec="minutes"),
        "fonte": "Telemetria SS (con_driver_h_km e dev_status_30); diesel: ANP, média dos estados no período.",
    }
