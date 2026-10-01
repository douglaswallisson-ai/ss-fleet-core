"""
Séries para os relatórios gerenciais (Dashboard Start, Power BI).

Duas rotas, só leitura, sobre as tabelas consolidadas do BI:

- `/serie-diaria`: um registro por dia com km, km filtrado, litros, horas,
  eventos, horas sem condutor e as 13 faixas. O front soma, compara períodos
  e desenha a evolução — uma consulta só em vez de paginar dois relatórios.
- `/ocioso`: parado com motor ligado por veículo e por dia.

Regras (vault):
- Km filtrado = só linhas com combustível entre 0 e 500.000 mL, exclusivo
  (indicadores-dashboard-start, R1). Média km/l = km filtrado ÷ litros (R4).
- Combustível negativo vira 0 (Power BI, P2). Valores estimados (`*_estimated`)
  não entram: o Power BI não os usa, e misturá-los ao medido sem marca é o
  ponto que o vault registra como problema no Dashboard Start.
- `stop_engine_on` inclui o parado produtivo (R5/R6).
- `total_11` = as 11 faixas do Dashboard Start; `faixas_13` = as 13 do Power BI.
"""

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_read
from app.core.escopo import clausula_escopo, escopo_do_usuario
from app.middleware.auth import require_permission

router = APIRouter()

FAIXAS_SQL = """
    SUM(COALESCE(td.time_green, 0))                      AS verde,
    SUM(COALESCE(td.time_extra_eco, 0))                  AS extra_economica,
    SUM(COALESCE(td.time_inercia, 0))                    AS inercia,
    SUM(COALESCE(td.time_eco_roll, 0))                   AS eco_roll,
    SUM(COALESCE(td.time_low_speed, 0))                  AS baixa_velocidade,
    SUM(COALESCE(td.time_yellow, 0))                     AS amarela,
    SUM(COALESCE(td.time_red, 0))                        AS vermelha,
    SUM(COALESCE(td.time_blue, 0))                       AS batendo_transmissao,
    SUM(COALESCE(td.time_banguela, 0))                   AS movimento_sem_tracao,
    SUM(COALESCE(td.time_stop_accel, 0))                 AS parado_acelerando,
    SUM(COALESCE(td.time_stop_engine_on, 0))             AS parado_ocioso,
    SUM(COALESCE(td.time_stop_engine_on_productive, 0))  AS parado_produtivo,
    SUM(COALESCE(td.time_tolerancia, 0))                 AS tolerancia
"""

COLUNAS_FAIXA = [
    "verde", "extra_economica", "inercia", "eco_roll", "baixa_velocidade", "amarela", "vermelha",
    "batendo_transmissao", "movimento_sem_tracao", "parado_acelerando", "parado_ocioso",
    "parado_produtivo", "tolerancia",
]
#: As 11 do Dashboard Start: as 13 menos eco-roll e baixa velocidade.
COLUNAS_11 = [c for c in COLUNAS_FAIXA if c not in ("eco_roll", "baixa_velocidade")]


def _periodo(start_date: Optional[date], end_date: Optional[date], dias_padrao: int) -> tuple[date, date]:
    fim = end_date or (date.today() - timedelta(days=1))
    ini = start_date or (fim - timedelta(days=dias_padrao - 1))
    if fim < ini:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "end_date anterior a start_date")
    if (fim - ini).days > 366:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Período máximo de 366 dias")
    return ini, fim


def _escopo(user, alias: str, group_id: Optional[int]) -> tuple[str, dict]:
    grupos, subgrupos = escopo_do_usuario(user)
    sql, params = clausula_escopo(grupos, subgrupos, alias=alias)
    if group_id is not None:
        sql += f" AND {alias}.group_id = :group_id"
        params = {**params, "group_id": group_id}
    return sql, params


@router.get("/serie-diaria")
async def serie_diaria(
    start_date: Optional[date] = Query(None, description="Padrão: 30 dias até ontem"),
    end_date: Optional[date] = Query(None, description="Inclusivo. Padrão: ontem"),
    group_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    ini, fim = _periodo(start_date, end_date, 30)
    periodo = {"ini": ini, "fim": fim}

    esc_h, par_h = _escopo(current_user, "h", group_id)
    hist = {
        r["dia"]: dict(r)
        for r in (
            await db.execute(
                text(
                    f"""
                    SELECT h.dt AS dia,
                           SUM(h.distance_traveled_hist) / 1000.0 AS km,
                           SUM(CASE WHEN h.used_fuel_hist > 0 AND h.used_fuel_hist < 500000
                                    THEN h.distance_traveled_hist ELSE 0 END) / 1000.0 AS km_filtrado,
                           SUM(GREATEST(COALESCE(h.used_fuel_hist, 0), 0)) / 1000.0 AS litros,
                           SUM(h.time_traveled_hist) / 3600.0 AS horas,
                           SUM(CASE WHEN COALESCE(h.driver_id, 0) = 0 THEN h.time_traveled_hist ELSE 0 END)
                               / 3600.0 AS horas_sem_condutor,
                           SUM(COALESCE(h.count_acel_excess, 0)) AS aceleracao,
                           SUM(COALESCE(h.count_break_excess, 0)) AS freada,
                           SUM(COALESCE(h.count_clutch_excess, 0)) AS embreagem,
                           SUM(COALESCE(h.count_speed_excess, 0) + COALESCE(h.count_speed_excess_dry_l1, 0)
                             + COALESCE(h.count_speed_excess_dry_l2, 0) + COALESCE(h.count_speed_excess_dry_l3, 0))
                               AS velocidade,
                           COUNT(DISTINCT h.unit_id) AS veiculos,
                           COUNT(DISTINCT NULLIF(COALESCE(h.driver_id, 0), 0)) AS motoristas
                    FROM mova.con_driver_h_km h
                    WHERE h.dt >= :ini AND h.dt <= :fim {esc_h}
                    GROUP BY h.dt
                    """
                ),
                {**periodo, **par_h},
            )
        ).mappings()
    }

    esc_t, par_t = _escopo(current_user, "td", group_id)
    tele = {
        r["dia"]: dict(r)
        for r in (
            await db.execute(
                text(
                    f"""
                    SELECT td.day AS dia, {FAIXAS_SQL}
                    FROM mova.con_telemetry_day td
                    WHERE td.day >= :ini AND td.day <= :fim {esc_t}
                    GROUP BY td.day
                    """
                ),
                {**periodo, **par_t},
            )
        ).mappings()
    }

    esc_w, par_w = _escopo(current_user, "w", group_id)
    meta = (
        await db.execute(
            text(f"SELECT AVG(w.goal) FROM mova.weight_range w WHERE w.range_id = 0 {esc_w}"),
            par_w,
        )
    ).scalar()

    dias = []
    d = ini
    while d <= fim:
        h = hist.get(d, {})
        t = tele.get(d, {})
        faixas = {c: int(t.get(c) or 0) for c in COLUNAS_FAIXA}
        dias.append(
            {
                "dia": d.isoformat(),
                **{k: round(float(h.get(k) or 0), 3) for k in ("km", "km_filtrado", "litros", "horas", "horas_sem_condutor")},
                **{k: int(h.get(k) or 0) for k in ("aceleracao", "freada", "embreagem", "velocidade", "veiculos", "motoristas")},
                "faixas": faixas,
                "faixas_13": sum(faixas.values()),
                "total_11": sum(faixas[c] for c in COLUNAS_11),
                "stop_engine_on": faixas["parado_ocioso"] + faixas["parado_produtivo"],
            }
        )
        d += timedelta(days=1)

    return {
        "inicio": ini.isoformat(),
        "fim": fim.isoformat(),
        # Fração (0,15 = 15%), como o Dashboard Start lê. Sem meta: 15% (R7).
        "meta_parado": float(meta) if meta is not None else 0.15,
        "meta_parado_cadastrada": meta is not None,
        "dias": dias,
    }


@router.get("/ocioso")
async def ocioso(
    start_date: Optional[date] = Query(None, description="Padrão: 7 dias até ontem"),
    end_date: Optional[date] = Query(None),
    group_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """Parado com motor ligado (inclui produtivo) por veículo, com a série diária."""
    ini, fim = _periodo(start_date, end_date, 7)
    periodo = {"ini": ini, "fim": fim}

    esc_t, par_t = _escopo(current_user, "td", group_id)
    tele = (
        await db.execute(
            text(
                f"""
                SELECT td.unit_id, td.day AS dia,
                       SUM(COALESCE(td.time_stop_engine_on, 0) + COALESCE(td.time_stop_engine_on_productive, 0))
                           AS parado,
                       SUM(COALESCE(td.time_stop_engine_on, 0) + COALESCE(td.time_stop_engine_on_productive, 0)
                         + COALESCE(td.time_stop_accel, 0) + COALESCE(td.time_banguela, 0)
                         + COALESCE(td.time_blue, 0) + COALESCE(td.time_green, 0)
                         + COALESCE(td.time_extra_eco, 0) + COALESCE(td.time_yellow, 0)
                         + COALESCE(td.time_red, 0) + COALESCE(td.time_inercia, 0)
                         + COALESCE(td.time_tolerancia, 0)) AS total_11
                FROM mova.con_telemetry_day td
                WHERE td.day >= :ini AND td.day <= :fim {esc_t}
                GROUP BY td.unit_id, td.day
                """
            ),
            {**periodo, **par_t},
        )
    ).mappings().all()

    esc_h, par_h = _escopo(current_user, "h", group_id)
    hist = (
        await db.execute(
            text(
                f"""
                SELECT h.unit_id, COALESCE(h.driver_id, 0) AS driver_id,
                       SUM(GREATEST(COALESCE(h.used_fuel_hist, 0), 0)) / 1000.0 AS litros,
                       SUM(h.time_traveled_hist) AS segundos
                FROM mova.con_driver_h_km h
                WHERE h.dt >= :ini AND h.dt <= :fim {esc_h}
                GROUP BY h.unit_id, COALESCE(h.driver_id, 0)
                """
            ),
            {**periodo, **par_h},
        )
    ).mappings().all()

    n_dias = (fim - ini).days + 1
    por: dict[int, dict] = {}
    for r in tele:
        u = por.setdefault(r["unit_id"], {"parado": 0, "total_11": 0, "serie": [0] * n_dias, "litros": 0.0, "motoristas": {}})
        u["parado"] += int(r["parado"] or 0)
        u["total_11"] += int(r["total_11"] or 0)
        u["serie"][(r["dia"] - ini).days] += int(r["parado"] or 0)
    for r in hist:
        u = por.setdefault(r["unit_id"], {"parado": 0, "total_11": 0, "serie": [0] * n_dias, "litros": 0.0, "motoristas": {}})
        u["litros"] += float(r["litros"] or 0)
        if r["driver_id"]:
            u["motoristas"][r["driver_id"]] = u["motoristas"].get(r["driver_id"], 0) + int(r["segundos"] or 0)

    ids = list(por)
    rotulos, nomes = {}, {}
    if ids:
        rotulos = {
            r["id"]: r["rotulo"]
            for r in (
                await db.execute(
                    text(
                        "SELECT id, CONCAT_WS(' · ', NULLIF(TRIM(label2), ''), label) AS rotulo"
                        " FROM mova.tracked_unit WHERE id = ANY(:ids)"
                    ),
                    {"ids": ids},
                )
            ).mappings()
        }
        principais = [max(u["motoristas"], key=u["motoristas"].get) for u in por.values() if u["motoristas"]]
        if principais:
            nomes = {
                r["id"]: r["name"]
                for r in (
                    await db.execute(
                        text("SELECT id, name FROM mova.driver WHERE id = ANY(:ids)"), {"ids": principais}
                    )
                ).mappings()
            }

    veiculos = []
    for uid, u in por.items():
        if u["parado"] <= 0:
            continue
        principal = max(u["motoristas"], key=u["motoristas"].get) if u["motoristas"] else None
        veiculos.append(
            {
                "unit_id": uid,
                "rotulo": rotulos.get(uid) or str(uid),
                "motorista": nomes.get(principal) if principal else None,
                "segundos_parado": u["parado"],
                "pct_parado": round(u["parado"] / u["total_11"], 4) if u["total_11"] else None,
                "litros": round(u["litros"], 1),
                "serie": u["serie"],
            }
        )
    veiculos.sort(key=lambda v: -v["segundos_parado"])
    return {"inicio": ini.isoformat(), "fim": fim.isoformat(), "veiculos": veiculos}
