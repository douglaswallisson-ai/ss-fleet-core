"""
Relevo das rotas: perfil de elevação do trajeto e subida acumulada por
veículo, motorista e dia, cruzada com o consumo. Só leitura.

A elevação sai do mapa SRTM (`app/core/relevo.py`), não do equipamento: só
39% da frota grava altitude, e o mapa vale igual para todos.

Posições: `mova.dev_status_30` (partição diária por `local_time`), lidas pelo
índice (unit_id, local_time). O cálculo de cada veículo em cada dia vai para o
Redis: dia passado não muda, então só é feito uma vez.
"""

import asyncio
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text

from app.core import combustivel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.bi import TIPOS_EVENTO, _veiculos
from app.api.v1.endpoints.gerencial import Filtros, _periodo
from app.core import relevo
from app.core.database import AsyncSessionLocalReplica, get_db_read
import json

from app.core.redis import async_redis_client
from app.middleware.auth import require_permission

router = APIRouter()

VERSAO = "v1"
#: Pontos do gráfico do trajeto; o cálculo usa todos.
MAX_PONTOS_GRAFICO = 900
#: Leituras de dias em paralelo, cada uma numa conexão.
PARALELO = 4

SQL_POSICOES = """
    SELECT d.unit_id, d.local_time, d.latitude::float AS lat, d.longitude::float AS lon,
           d.speed, d.altitude, COALESCE(d.driver_id, 0) AS driver_id, d.tracker_event_id
    FROM mova.dev_status_30 d
    WHERE d.unit_id = ANY(CAST(:ids AS integer[]))
      AND d.local_time >= :ini AND d.local_time < :fim
      AND d.gps AND d.latitude <> 0 AND d.longitude <> 0
      {extra}
    ORDER BY d.unit_id, d.local_time
"""


# O perfil de um veículo traz mais colunas (RPM, motorista, endereço) para o
# gráfico e para a caixa de detalhe do evento; o resumo da frota usa a leve acima.
SQL_TRAJETO = """
    SELECT d.unit_id, d.local_time, d.latitude::float AS lat, d.longitude::float AS lon,
           d.speed, d.altitude, COALESCE(d.driver_id, 0) AS driver_id, d.tracker_event_id,
           COALESCE(d.can_rpm, d.rpm)::float AS rpm, NULLIF(NULLIF(TRIM(d.driver_name), ''), 'NULL') AS motorista,
           NULLIF(NULLIF(TRIM(d.address), ''), 'NULL') AS endereco, d.can_fuel_level_percent::float AS combustivel
    FROM mova.dev_status_30 d
    WHERE d.unit_id = ANY(CAST(:ids AS integer[]))
      AND d.local_time >= :ini AND d.local_time < :fim
      AND d.gps AND d.latitude <> 0 AND d.longitude <> 0
    ORDER BY d.unit_id, d.local_time
"""


def _sem_mapa():
    if not relevo.mapa_disponivel():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Mapa de relevo não instalado neste servidor (rode scripts/baixar_relevo.py).",
        )


def _r(v: Optional[float], casas: int = 1) -> Optional[float]:
    return None if v is None else round(v, casas)


@router.get("/trajeto")
async def trajeto(
    unit_id: int = Query(...),
    dia: date = Query(...),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """Perfil de elevação do dia de um veículo: altitude × distância, com a velocidade."""
    _sem_mapa()
    f = Filtros(group_id=None, subgroup_id=None, unit_id=unit_id, driver_id=None)
    if not await _veiculos(db, current_user, f):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Veículo não encontrado")
    ini = datetime.combine(dia, datetime.min.time())
    linhas = (
        await db.execute(text(SQL_TRAJETO), {"ids": [unit_id], "ini": ini, "fim": ini + timedelta(days=1)})
    ).mappings().all()
    if not linhas:
        return {"unit_id": unit_id, "dia": dia.isoformat(), "pontos": [], "resumo": None, "altitude_do_equipamento": False}

    # O perfil usa só o veículo andando: parado, o GPS oscila no lugar e
    # inventaria subidas de alguns metros.
    andando = [r for r in linhas if (r["speed"] or 0) > 3]
    serie, res = relevo.acumular((r["lat"], r["lon"]) for r in andando)
    elevs = [e for _, e in serie if e is not None]
    tem_gps = sum(1 for r in andando if r["altitude"]) > len(andando) * 0.5

    passo = max(1, math.ceil(len(andando) / MAX_PONTOS_GRAFICO))
    pontos = [
        {
            "hora": r["local_time"].strftime("%H:%M"),
            "km": round(km, 2),
            "elevacao": _r(e),
            "altitude_gps": r["altitude"] if tem_gps and r["altitude"] else None,
            "velocidade": r["speed"],
            "rpm": round(r["rpm"]) if r["rpm"] else None,
            "lat": r["lat"],
            "lon": r["lon"],
        }
        for i, (r, (km, e)) in enumerate(zip(andando, serie))
        if i % passo == 0 or i == len(andando) - 1
    ]
    km_med = res["km_medido"] or 0

    # Eventos de condução no ponto exato em que aconteceram: a própria
    # posição traz o evento (tracker_event_id). Não depende do heatmap, que
    # chega com atraso. O km é o do último ponto em movimento até ali.
    tipo_de = {c: tp for tp, cs in TIPOS_EVENTO.items() for c in cs}
    km_por_hora = [(r["local_time"], km) for r, (km, _e) in zip(andando, serie)]
    nomes = {
        int(a): b
        for a, b in (
            await db.execute(
                text("SELECT id, name FROM mova.tracker_event WHERE id = ANY(CAST(:ids AS integer[]))"),
                {"ids": list({r["tracker_event_id"] for r in linhas if r["tracker_event_id"] in tipo_de})},
            )
        ).all()
    } if any(r["tracker_event_id"] in tipo_de for r in linhas) else {}
    eventos = []
    i = 0
    for r in linhas:
        while i + 1 < len(km_por_hora) and km_por_hora[i + 1][0] <= r["local_time"]:
            i += 1
        cod = r["tracker_event_id"]
        if cod not in tipo_de:
            continue
        eventos.append(
            {
                "hora": r["local_time"].strftime("%H:%M:%S"),
                "em": r["local_time"].isoformat(),
                "tipo": tipo_de[cod],
                "cod": cod,
                "evento": nomes.get(cod),
                "lat": r["lat"],
                "lon": r["lon"],
                "velocidade": r["speed"],
                "rpm": round(r["rpm"]) if r["rpm"] else None,
                "motorista": r["motorista"],
                "driver_id": r["driver_id"],
                "endereco": r["endereco"],
                "combustivel": r["combustivel"],
                "altitude_gps": r["altitude"] or None,
                "elevacao": _r(relevo.elevacao(r["lat"], r["lon"])),
                "km": round(km_por_hora[i][1], 2) if km_por_hora else None,
            }
        )

    return {
        "eventos": eventos,
        "unit_id": unit_id,
        "dia": dia.isoformat(),
        "altitude_do_equipamento": tem_gps,
        "pontos": pontos,
        "resumo": {
            "km": round(res["km"], 1),
            "subida_m": round(res["subida_m"]),
            "descida_m": round(res["descida_m"]),
            "subida_por_100km": round(100 * res["subida_m"] / km_med, 1) if km_med else None,
            "pct_aclive": round(100 * res["km_aclive"] / km_med, 1) if km_med else None,
            "pct_declive": round(100 * res["km_declive"] / km_med, 1) if km_med else None,
            "elevacao_min": _r(min(elevs)) if elevs else None,
            "elevacao_max": _r(max(elevs)) if elevs else None,
        },
    }


async def _calcular_dia(dia: date, ids: list[int]) -> dict[int, dict]:
    """Relevo de cada veículo no dia, separado por motorista."""
    ini = datetime.combine(dia, datetime.min.time())
    async with AsyncSessionLocalReplica() as s:
        linhas = (
            await s.execute(
                text(SQL_POSICOES.format(extra="AND d.speed > 3")),
                {"ids": ids, "ini": ini, "fim": ini + timedelta(days=1)},
            )
        ).all()
    por_unidade: dict[int, list] = defaultdict(list)
    for u, _t, lat, lon, _v, _a, drv, _ev in linhas:
        por_unidade[u].append((lat, lon, drv))

    saida: dict[int, dict] = {u: {} for u in ids}
    for u, pts in por_unidade.items():
        acc: dict[int, list[float]] = defaultdict(lambda: [0.0] * 6)
        ant = None
        for lat, lon, drv in pts:
            e = relevo.elevacao(lat, lon)
            if ant is not None:
                d = relevo.distancia_km(ant[0], ant[1], lat, lon)
                a = acc[drv]
                a[0] += d
                if e is not None and ant[2] is not None and 0 < d <= relevo.MAX_TRECHO_KM:
                    dz = e - ant[2]
                    a[1] += d
                    if dz > 0:
                        a[2] += dz
                    else:
                        a[3] -= dz
                    incl = dz / (d * 1000)
                    if incl >= relevo.ACLIVE:
                        a[4] += d
                    elif incl <= -relevo.ACLIVE:
                        a[5] += d
            ant = (lat, lon, e)
        saida[u] = {str(k): [round(x, 3) for x in v] for k, v in acc.items()}
    return saida


async def _cache_ler(chaves: list[str]) -> list[Optional[dict]]:
    """Uma ida só ao Redis; fora do ar, segue sem cache em vez de esperar."""
    try:
        brutos = await asyncio.wait_for(async_redis_client.mget(chaves), timeout=3)
        return [json.loads(b) if b else None for b in brutos]
    except Exception:
        return [None] * len(chaves)


async def _cache_gravar(itens: dict[str, dict], ttl: int):
    try:
        pipe = async_redis_client.pipeline()
        for k, v in itens.items():
            pipe.setex(k, ttl, json.dumps(v))
        await asyncio.wait_for(pipe.execute(), timeout=5)
    except Exception:
        pass


#: Cálculos por (veículo, dia) também em memória: com o Redis fora do ar, o
#: cálculo em segundo plano não se perde.
_MEMORIA: dict[str, dict] = {}
_MEMORIA_MAX = 300_000
#: Cálculos em andamento: chave da consulta → [dias feitos, dias total, tarefa].
_TAREFAS: dict[str, list] = {}


def _chave(u: int, d: date) -> str:
    return f"relevo:{VERSAO}:{u}:{d.isoformat()}"


async def _ler_calculados(ids: list[int], ini: date, fim: date):
    dias = [ini + timedelta(days=i) for i in range((fim - ini).days + 1)]
    pares = [(u, d) for d in dias for u in ids]
    falta_mem = [(u, d) for u, d in pares if _chave(u, d) not in _MEMORIA]
    do_redis = dict(zip(falta_mem, await _cache_ler([_chave(u, d) for u, d in falta_mem]))) if falta_mem else {}
    resultado: dict[tuple[int, str], dict] = {}
    faltando: dict[date, list[int]] = defaultdict(list)
    for u, d in pares:
        # Veículo que não rodou no dia tem resultado {} — vazio, mas calculado.
        # Com `or`, o {} contava como "falta calcular": o cálculo recomeçava
        # para sempre e a tela ficava indo e voltando de 0 a 67%.
        k = _chave(u, d)
        c = _MEMORIA[k] if k in _MEMORIA else do_redis.get((u, d))
        if c is None:
            faltando[d].append(u)
        else:
            resultado[(u, d.isoformat())] = c
    return resultado, dict(faltando)


async def _calcular_faltando(faltando: dict[date, list[int]], progresso: Optional[list] = None):
    hoje = date.today()
    sem = asyncio.Semaphore(PARALELO)

    async def um(d: date, us: list[int]):
        async with sem:
            calc = await _calcular_dia(d, us)
        if len(_MEMORIA) > _MEMORIA_MAX:
            _MEMORIA.clear()
        for u, v in calc.items():
            _MEMORIA[_chave(u, d)] = v
        # Dia passado não muda; o de hoje ainda recebe posições.
        await _cache_gravar({_chave(u, d): v for u, v in calc.items()}, 600 if d >= hoje else 60 * 86400)
        if progresso is not None:
            progresso[0] += 1

    await asyncio.gather(*(um(d, us) for d, us in faltando.items()))


def _metricas(a: list[float]) -> dict:
    km, km_med, sub, desc, kac, kdc = a
    return {
        # Km entre posições próximas, onde o relevo foi medido. A soma de
        # todas as distâncias inclui saltos do GPS e inflava o número.
        "km": round(km_med, 1),
        "km_posicoes": round(km, 1),
        "subida_m": round(sub),
        "descida_m": round(desc),
        "subida_por_100km": round(100 * sub / km_med, 1) if km_med > 1 else None,
        "pct_aclive": round(100 * kac / km_med, 1) if km_med > 1 else None,
        "pct_declive": round(100 * kdc / km_med, 1) if km_med > 1 else None,
    }


def _somar(dst: list[float], src: list[float]):
    for i, v in enumerate(src):
        dst[i] += v


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 5:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if not sx or not sy:
        return None
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy), 3)


@router.get("/resumo")
async def resumo(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    f: Filtros = Depends(),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    Relevo do período por veículo, motorista e dia, com o km/l de cada um.

    `subida_por_100km` é a medida principal: metros subidos a cada 100 km.
    Compara rotas de tamanhos diferentes — uma rota plana fica perto de 100,
    uma de serra passa de 1.000.
    """
    _sem_mapa()
    ini, fim = _periodo(start_date, end_date, 30)
    if (fim - ini).days > 92:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Período máximo de 93 dias para o relevo")
    ids = await _veiculos(db, current_user, f)
    vazio = {"inicio": ini.isoformat(), "fim": fim.isoformat(), "calculando": False, "progresso": 1, "totais": None, "por_veiculo": [], "por_motorista": [], "por_dia": [], "correlacao_kml": None}
    if not ids:
        return vazio

    dados, faltando = await _ler_calculados(ids, ini, fim)
    if faltando:
        # Até 2 dias a calcular: faz na hora. Mais que isso (a primeira vez de
        # um mês leva minutos), calcula em segundo plano e a tela acompanha.
        if len(faltando) <= 2:
            await _calcular_faltando(faltando)
            dados, faltando = await _ler_calculados(ids, ini, fim)
        else:
            chave = f"{hash(tuple(sorted(ids)))}:{ini}:{fim}"
            t_ = _TAREFAS.get(chave)
            if t_ is None or t_[2].done():
                prog = [0, len(faltando)]
                prog.append(asyncio.create_task(_calcular_faltando(faltando, prog)))
                _TAREFAS[chave] = t_ = prog
            total_dias = (fim - ini).days + 1
            feitos = total_dias - t_[1] + t_[0]
            return {**vazio, "calculando": True, "progresso": round(feitos / total_dias, 3)}

    por_unidade: dict[int, list[float]] = defaultdict(lambda: [0.0] * 6)
    por_motorista: dict[int, list[float]] = defaultdict(lambda: [0.0] * 6)
    por_dia: dict[str, list[float]] = defaultdict(lambda: [0.0] * 6)
    total = [0.0] * 6
    for (u, d), por_drv in dados.items():
        for drv, v in por_drv.items():
            drv_i = int(drv)
            if f.driver_id is not None and drv_i != f.driver_id:
                continue
            _somar(por_unidade[u], v)
            _somar(por_motorista[drv_i], v)
            _somar(por_dia[d], v)
            _somar(total, v)

    # Consumo do período, para o cruzamento (mesma regra do ranking: km com
    # combustível ÷ litros, combustível negativo vira 0).
    esc, par = f.sql(current_user, "h")
    cons = (
        await db.execute(
            text(
                f"""
                SELECT h.unit_id, COALESCE(h.driver_id, 0) AS driver_id, MAX(h.label) AS placa, MAX(h.driver) AS motorista,
                       SUM({combustivel.litros_ml()}) / 1000.0 AS litros,
                       SUM({combustivel.km_com_combustivel_m()}) / 1000.0 AS km_comb
                FROM mova.con_driver_h_km h
                WHERE h.dt >= :ini AND h.dt <= :fim {esc}
                GROUP BY 1, 2
                """
            ),
            {**par, "ini": ini, "fim": fim},
        )
    ).mappings().all()
    kml_u: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
    kml_m: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
    placas: dict[int, str] = {}
    nomes: dict[int, str] = {}
    for r in cons:
        lt, km = float(r["litros"] or 0), float(r["km_comb"] or 0)
        kml_u[r["unit_id"]][0] += km
        kml_u[r["unit_id"]][1] += lt
        kml_m[r["driver_id"]][0] += km
        kml_m[r["driver_id"]][1] += lt
        if r["placa"]:
            placas[r["unit_id"]] = r["placa"]
        if r["motorista"] and r["driver_id"]:
            nomes[r["driver_id"]] = r["motorista"]
    faltam = [u for u in por_unidade if u not in placas]
    if faltam:
        for u, lbl in (await db.execute(text("SELECT id, label FROM mova.tracked_unit WHERE id = ANY(:ids)"), {"ids": faltam})).all():
            placas[u] = lbl

    def kml(par_: Optional[list[float]]) -> Optional[float]:
        return round(par_[0] / par_[1], 2) if par_ and par_[1] > 0 else None

    veiculos = [
        {"unit_id": u, "placa": placas.get(u, str(u)), **_metricas(a), "kml": kml(kml_u.get(u))}
        for u, a in por_unidade.items()
        if a[1] > 0
    ]
    veiculos.sort(key=lambda x: -(x["subida_por_100km"] or 0))
    motoristas = [
        {"driver_id": d, "nome": nomes.get(d) if d else "NÃO IDENTIFICADO", **_metricas(a), "kml": kml(kml_m.get(d))}
        for d, a in por_motorista.items()
        if a[1] > 0
    ]
    motoristas.sort(key=lambda x: -(x["subida_por_100km"] or 0))
    # Correlação só com quem rodou o bastante para o km/l significar algo.
    base = [(v["subida_por_100km"], v["kml"]) for v in veiculos if v["km"] >= 200 and v["kml"] and v["subida_por_100km"] is not None]
    return {
        "inicio": ini.isoformat(),
        "fim": fim.isoformat(),
        "calculando": False,
        "progresso": 1,
        "totais": {**_metricas(total), "veiculos": len(veiculos), "kml": kml([sum(x[0] for x in kml_u.values()), sum(x[1] for x in kml_u.values())])},
        "por_veiculo": veiculos,
        "por_motorista": motoristas,
        "por_dia": [{"dia": d, **_metricas(a)} for d, a in sorted(por_dia.items())],
        "correlacao_kml": _pearson([b[0] for b in base], [b[1] for b in base]),
        "fonte": "Mapa de relevo SRTM (NASA), ~90 m; posições com o veículo andando",
    }
