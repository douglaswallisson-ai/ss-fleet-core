"""
Relatórios de frota que o sistema atual tem e a plataforma nova ainda não tinha.

Cada um segue o código do sistema atual (plataforma_web, lido em 04/10/2026) e
as notas do vault (Relatorios/), com as correções que o vault aponta:

- Paradas e Deslocamentos (reportstopandtrip, 406 usuários) e o Consolidado
  (reportstopandtripcon): `mova.con_stop`; `total_km` está em METROS.
  Correção: deslocamento com média acima de 200 km/h é salto de odômetro
  (34% do km de setembro/2026) — aparece marcado e fica fora das somas.
- Paradas em POI (reportstoppoi): parada (`move_stop = 0`) que termina num POI.
- Passagem por POI (reportpasspoi): posições a até X m de um POI. Aqui as
  posições seguidas viram uma passagem (entrada, saída, permanência), em vez
  de uma linha por posição.
- Cercas (reportcerca): eventos 60 (entrou) e 61 (saiu). Correção: o sistema
  atual só pareia a PRIMEIRA entrada e a primeira saída de cada veículo no
  período; aqui cada entrada pareia com a saída seguinte do veículo. A saída
  (61) chega sem `area_id` (conferido na VTR, 04/10/2026): a cerca é a da
  entrada.
- Distância por Período / por Semana (reportkmtotal, reportkm): maior − menor
  odômetro com ignição ligada. Correção do vault: só leituras com
  `odom_quality_flag` 'ok' (ou vazia) — derruba de 16 para 1 os dias absurdos.
  Dia acima de 2.000 km ainda é descartado e listado.
- Horímetro por Período (reporthourmetertotal): maior − menor horímetro, em
  MINUTOS. Correção: leituras soltas (ex.: 71 milhões de horas na JTP) faziam
  "maior − menor" explodir; aqui soma só o avanço entre leituras seguidas que
  cabe no tempo entre elas (+5 min de folga). Dia acima de 24 h fica fora.
  Usa o horímetro do CAN e, sem ele, o do equipamento — nunca os dois somados.
"""

import time
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.core.database import AsyncSessionLocalReplica
from app.middleware.auth import require_permission

router = APIRouter()

_CACHE: dict[tuple, tuple[float, dict]] = {}
CACHE_S = 300
KMH_SALTO = 200
KM_DIA_MAX = 2000
LINHAS_MAX = 5000


def _grupo_ok(user, group_id: int):
    if getattr(user, "master", 0) or getattr(user, "is_super_admin", False):
        return
    if group_id not in {g for g, _ in (getattr(user, "group_access", None) or [])}:
        raise HTTPException(403, "Sem acesso a este grupo.")


def _f(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, timedelta):
        return round(v.total_seconds())
    if hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
        return float(v)
    return v


async def _ler(sql: str, p: dict) -> list[dict]:
    async with AsyncSessionLocalReplica() as db:
        await db.execute(text("SET LOCAL statement_timeout = '90s'"))
        return [{k: _f(v) for k, v in r.items()} for r in (await db.execute(text(sql), p)).mappings().all()]


def _params(user, group_id: int, inicio: date, fim: date, max_dias: int, unit_id: Optional[int] = None) -> dict:
    _grupo_ok(user, group_id)
    if fim < inicio:
        raise HTTPException(422, "O fim do período deve ser depois do início.")
    if (fim - inicio).days + 1 > max_dias:
        raise HTTPException(422, f"Período de no máximo {max_dias} dias.")
    return {"g": group_id, "u": unit_id, "ini": datetime.combine(inicio, datetime.min.time()),
            "fim": datetime.combine(fim + timedelta(days=1), datetime.min.time())}


def _cache(k, fn):
    hit = _CACHE.get(k)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    return None


UNIDADES = "SELECT id, label, label2 FROM mova.tracked_unit WHERE group_id = :g AND status = 1 AND (CAST(:u AS int) IS NULL OR id = :u)"


@router.get("/veiculos")
async def veiculos(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    return {"data": await _ler("SELECT id, label AS placa, label2 AS prefixo FROM mova.tracked_unit WHERE group_id = :g AND status = 1 ORDER BY label",
                               {"g": group_id})}


# ------------------------------------------------- paradas e deslocamentos

@router.get("/paradas-deslocamentos")
async def paradas_deslocamentos(group_id: int = Query(...), inicio: date = Query(...), fim: date = Query(...),
                                unit_id: Optional[int] = Query(None), tipo: Optional[str] = Query(None, pattern="^(parada|deslocamento)$"),
                                min_minutos: int = Query(0, ge=0, le=1440),
                                user=Depends(require_permission("reports", "read"))):
    p = _params(user, group_id, inicio, fim, 31, unit_id)
    k = ("pd", group_id, inicio, fim, unit_id, tipo, min_minutos)
    if (hit := _cache(k, None)) is not None:
        return hit
    p.update({"min_s": min_minutos * 60, "tipo": tipo, "salto": KMH_SALTO})
    base = """FROM mova.con_stop c JOIN mova.tracked_unit tu ON tu.id = c.unit_id
        WHERE c.group_id = :g AND c.initial_time >= :ini AND c.initial_time < :fim
          AND (CAST(:u AS int) IS NULL OR c.unit_id = :u)
          AND coalesce(c.total_time, 0) >= :min_s
          AND (CAST(:tipo AS text) IS NULL OR (CAST(:tipo AS text) = 'parada') = (c.move_stop = 0))"""
    salto = "(c.move_stop <> 0 AND c.total_time > 0 AND c.total_km / 1000.0 / (c.total_time / 3600.0) > :salto)"
    linhas = await _ler(f"""SELECT c.id, c.unit_id, tu.label AS placa, tu.label2 AS prefixo,
            CASE WHEN c.move_stop = 0 THEN 'parada' ELSE 'deslocamento' END AS tipo,
            c.initial_time AS inicio, c.final_time AS fim, c.total_time AS duracao_s, c.time_ign_on AS ligado_s,
            round(coalesce(c.total_km, 0) / 1000.0, 2) AS km, c.avg_spd AS vel_media, c.max_spd AS vel_max,
            c.initial_address AS endereco_inicio, c.final_address AS endereco_fim,
            coalesce(c.initial_poi_name, c.initial_area_name) AS local_inicio, coalesce(c.final_poi_name, c.final_area_name) AS local_fim,
            c.driver_name AS motorista, {salto} AS salto_odometro
        {base} ORDER BY c.initial_time DESC LIMIT {LINHAS_MAX + 1}""", p)
    consolidado = await _ler(f"""SELECT c.unit_id, max(tu.label) AS placa, max(tu.label2) AS prefixo,
            count(*) FILTER (WHERE c.move_stop = 0) AS paradas,
            coalesce(sum(c.total_time) FILTER (WHERE c.move_stop = 0), 0) AS parado_s,
            coalesce(sum(c.time_ign_on) FILTER (WHERE c.move_stop = 0), 0) AS parado_ligado_s,
            count(*) FILTER (WHERE c.move_stop <> 0) AS deslocamentos,
            coalesce(sum(c.total_time) FILTER (WHERE c.move_stop <> 0 AND NOT {salto}), 0) AS movimento_s,
            round(coalesce(sum(c.total_km) FILTER (WHERE c.move_stop <> 0 AND NOT {salto}), 0) / 1000.0, 1) AS km,
            max(c.max_spd) FILTER (WHERE c.move_stop <> 0 AND NOT {salto}) AS vel_max,
            count(*) FILTER (WHERE {salto}) AS saltos,
            round(coalesce(sum(c.total_km) FILTER (WHERE {salto}), 0) / 1000.0, 1) AS km_descartado
        {base} GROUP BY c.unit_id ORDER BY max(tu.label)""", p)
    r = {"linhas": linhas[:LINHAS_MAX], "cortado": len(linhas) > LINHAS_MAX, "consolidado": consolidado}
    _CACHE[k] = (time.time(), r)
    return r


# ------------------------------------------------------------ paradas em POI

@router.get("/paradas-poi")
async def paradas_poi(group_id: int = Query(...), inicio: date = Query(...), fim: date = Query(...),
                      unit_id: Optional[int] = Query(None), min_minutos: int = Query(0, ge=0, le=1440),
                      user=Depends(require_permission("reports", "read"))):
    p = _params(user, group_id, inicio, fim, 31, unit_id)
    k = ("ppoi", group_id, inicio, fim, unit_id, min_minutos)
    if (hit := _cache(k, None)) is not None:
        return hit
    p["min_s"] = min_minutos * 60
    base = """FROM mova.con_stop c JOIN mova.tracked_unit tu ON tu.id = c.unit_id JOIN mova.poi po ON po.id = c.final_poi_id
        WHERE c.group_id = :g AND c.move_stop = 0 AND c.initial_time >= :ini AND c.initial_time < :fim
          AND (CAST(:u AS int) IS NULL OR c.unit_id = :u) AND coalesce(c.total_time, 0) >= :min_s"""
    linhas = await _ler(f"""SELECT c.id, tu.label AS placa, tu.label2 AS prefixo, c.driver_name AS motorista, po.id AS poi_id, po.name AS ponto,
            c.initial_time AS inicio, c.final_time AS fim, c.total_time AS duracao_s, c.time_ign_on AS ligado_s,
            c.final_poi_distance AS distancia_m, c.initial_address AS endereco
        {base} ORDER BY c.initial_time DESC LIMIT {LINHAS_MAX + 1}""", p)
    por_ponto = await _ler(f"""SELECT po.id AS poi_id, po.name AS ponto, count(*) AS paradas, count(DISTINCT c.unit_id) AS veiculos,
            coalesce(sum(c.total_time), 0) AS total_s, round(avg(c.total_time)) AS media_s, max(c.total_time) AS maior_s
        {base} GROUP BY 1, 2 ORDER BY 3 DESC""", p)
    r = {"linhas": linhas[:LINHAS_MAX], "cortado": len(linhas) > LINHAS_MAX, "por_ponto": por_ponto}
    _CACHE[k] = (time.time(), r)
    return r


# ---------------------------------------------------------- passagem por POI

@router.get("/passagem-poi")
async def passagem_poi(group_id: int = Query(...), inicio: date = Query(...), fim: date = Query(...),
                       unit_id: Optional[int] = Query(None), distancia_m: int = Query(100, ge=10, le=5000),
                       user=Depends(require_permission("reports", "read"))):
    p = _params(user, group_id, inicio, fim, 7, unit_id)
    k = ("pass", group_id, inicio, fim, unit_id, distancia_m)
    if (hit := _cache(k, None)) is not None:
        return hit
    p["dist"] = distancia_m
    # Posições seguidas no mesmo ponto (até 10 min entre elas) formam uma passagem.
    linhas = await _ler(f"""WITH un AS ({UNIDADES}),
        pos AS (SELECT d.unit_id, d.poi_id, d.local_time, d.poi_distance, d.speed, d.driver_name,
                       CASE WHEN d.local_time - lag(d.local_time) OVER w > interval '10 minutes'
                                 OR lag(d.poi_id) OVER w IS DISTINCT FROM d.poi_id THEN 1 ELSE 0 END AS novo
                FROM mova.dev_status_30 d
                WHERE d.unit_id IN (SELECT id FROM un) AND d.local_time >= :ini AND d.local_time < :fim
                  AND d.poi_id IS NOT NULL AND d.poi_distance <= :dist
                WINDOW w AS (PARTITION BY d.unit_id ORDER BY d.local_time)),
        ilha AS (SELECT *, sum(novo) OVER (PARTITION BY unit_id ORDER BY local_time) AS n FROM pos)
        SELECT i.unit_id, max(un.label) AS placa, max(un.label2) AS prefixo, i.poi_id, max(po.name) AS ponto,
               min(i.local_time) AS entrada, max(i.local_time) AS saida,
               extract(epoch FROM max(i.local_time) - min(i.local_time))::int AS permanencia_s,
               min(i.poi_distance) AS menor_distancia_m, max(i.speed) AS vel_max, max(i.driver_name) AS motorista
        FROM ilha i JOIN un ON un.id = i.unit_id LEFT JOIN mova.poi po ON po.id = i.poi_id
        GROUP BY i.unit_id, i.poi_id, i.n ORDER BY min(i.local_time) DESC LIMIT {LINHAS_MAX + 1}""", p)
    por_ponto: dict[int, dict] = {}
    for l in linhas[:LINHAS_MAX]:
        a = por_ponto.setdefault(l["poi_id"], {"poi_id": l["poi_id"], "ponto": l["ponto"], "passagens": 0, "veiculos": set()})
        a["passagens"] += 1
        a["veiculos"].add(l["unit_id"])
    resumo = sorted(({**a, "veiculos": len(a["veiculos"])} for a in por_ponto.values()), key=lambda a: -a["passagens"])
    r = {"linhas": linhas[:LINHAS_MAX], "cortado": len(linhas) > LINHAS_MAX, "por_ponto": resumo}
    _CACHE[k] = (time.time(), r)
    return r


# -------------------------------------------------------------------- cercas

@router.get("/cercas")
async def cercas(group_id: int = Query(...), inicio: date = Query(...), fim: date = Query(...),
                 unit_id: Optional[int] = Query(None), user=Depends(require_permission("reports", "read"))):
    p = _params(user, group_id, inicio, fim, 31, unit_id)
    k = ("cerca", group_id, inicio, fim, unit_id)
    if (hit := _cache(k, None)) is not None:
        return hit
    linhas = await _ler(f"""WITH un AS ({UNIDADES}),
        ev AS (SELECT d.unit_id, d.area_id, d.area_name, d.tracker_event_id AS ev, d.local_time, d.driver_name,
                      lead(d.tracker_event_id) OVER w AS prox_ev, lead(d.local_time) OVER w AS prox_t
               FROM mova.dev_status_30 d
               WHERE d.unit_id IN (SELECT id FROM un) AND d.local_time >= :ini AND d.local_time < :fim AND d.tracker_event_id IN (60, 61)
               WINDOW w AS (PARTITION BY d.unit_id ORDER BY d.local_time))
        SELECT ev.unit_id, un.label AS placa, un.label2 AS prefixo, ev.area_id, coalesce(ev.area_name, ce.name, 'Cerca ' || ev.area_id) AS cerca,
               ev.local_time AS entrada, CASE WHEN ev.prox_ev = 61 THEN ev.prox_t END AS saida,
               CASE WHEN ev.prox_ev = 61 THEN extract(epoch FROM ev.prox_t - ev.local_time)::int END AS permanencia_s,
               ev.driver_name AS motorista
        FROM ev JOIN un ON un.id = ev.unit_id LEFT JOIN mova.cerca ce ON ce.id = ev.area_id
        WHERE ev.ev = 60 ORDER BY ev.local_time DESC LIMIT {LINHAS_MAX + 1}""", p)
    # Saída sem entrada no período: o veículo já estava dentro quando o período começou.
    saidas_soltas = (await _ler(f"""WITH un AS ({UNIDADES}),
        ev AS (SELECT d.tracker_event_id AS ev, lag(d.tracker_event_id) OVER (PARTITION BY d.unit_id ORDER BY d.local_time) AS ant
               FROM mova.dev_status_30 d WHERE d.unit_id IN (SELECT id FROM un) AND d.local_time >= :ini AND d.local_time < :fim
                 AND d.tracker_event_id IN (60, 61))
        SELECT count(*) AS n FROM ev WHERE ev = 61 AND ant IS DISTINCT FROM 60""", p))[0]["n"]
    r = {"linhas": linhas[:LINHAS_MAX], "cortado": len(linhas) > LINHAS_MAX, "saidas_sem_entrada": saidas_soltas}
    _CACHE[k] = (time.time(), r)
    return r


# --------------------------------------------- distância e horímetro por dia

@router.get("/distancia-horimetro")
async def distancia_horimetro(group_id: int = Query(...), inicio: date = Query(...), fim: date = Query(...),
                              unit_id: Optional[int] = Query(None), user=Depends(require_permission("reports", "read"))):
    p = _params(user, group_id, inicio, fim, 31, unit_id)
    k = ("dist", group_id, inicio, fim, unit_id)
    if (hit := _cache(k, None)) is not None:
        return hit
    dias = await _ler(f"""WITH un AS ({UNIDADES})
        SELECT d.unit_id, max(un.label) AS placa, max(un.label2) AS prefixo, d.local_time::date AS dia,
               (max(d.odom_total) FILTER (WHERE d.ignition AND coalesce(d.odom_quality_flag, 'ok') = 'ok')
                - min(d.odom_total) FILTER (WHERE d.ignition AND coalesce(d.odom_quality_flag, 'ok') = 'ok')) / 1000.0 AS km,
               count(*) FILTER (WHERE coalesce(d.odom_quality_flag, 'ok') <> 'ok') AS leituras_corrigidas,
               sum(CASE WHEN d.dc > 0 AND d.dc <= d.gap_min + 5 THEN d.dc END) AS min_can,
               sum(CASE WHEN d.de > 0 AND d.de <= d.gap_min + 5 THEN d.de END) AS min_eq,
               count(*) FILTER (WHERE d.dc > d.gap_min + 5 OR d.de > d.gap_min + 5) AS saltos_horimetro,
               min(d.local_time) AS primeira, max(d.local_time) AS ultima
        FROM (SELECT x.*, extract(epoch FROM x.local_time - lag(x.local_time) OVER w) / 60 AS gap_min,
                     x.can_engine_hourmeter - lag(x.can_engine_hourmeter) OVER w AS dc,
                     x.hourmeter - lag(x.hourmeter) OVER w AS de
              FROM mova.dev_status_30 x
              WHERE x.unit_id IN (SELECT id FROM un) AND x.local_time >= :ini AND x.local_time < :fim
              WINDOW w AS (PARTITION BY x.unit_id ORDER BY x.local_time)) d JOIN un ON un.id = d.unit_id
        GROUP BY d.unit_id, d.local_time::date ORDER BY 2, 4""", p)
    descartes = []
    for d in dias:
        km = d.pop("km")
        mins = d.pop("min_can")
        mins = mins if mins is not None else d.pop("min_eq")
        d.pop("min_eq", None)
        d["km"] = round(km, 1) if km is not None and 0 <= km <= KM_DIA_MAX else (None if km is None else 0)
        d["horas"] = round(mins / 60, 2) if mins is not None and 0 <= mins <= 1440 else None
        if km is not None and km > KM_DIA_MAX:
            descartes.append({"placa": d["placa"], "dia": d["dia"], "motivo": f"distância de {round(km):,} km no dia".replace(",", ".")})
            d["km"] = None
        if mins is not None and mins > 1440:
            descartes.append({"placa": d["placa"], "dia": d["dia"], "motivo": f"horímetro de {round(mins / 60):,} h no dia".replace(",", ".")})
    r = {"dias": dias, "descartes": descartes}
    _CACHE[k] = (time.time(), r)
    return r
