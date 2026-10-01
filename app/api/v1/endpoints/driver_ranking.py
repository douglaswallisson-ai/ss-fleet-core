"""
Ranking de motoristas pela Pontuação do Power BI "Indicadores de Condução 5.0".

A fórmula vem do vault (Regras-de-Negocio/indicadores-power-bi, P6, e o
dicionário de medidas do .pbix): nenhuma parte foi deduzida aqui.

    Pontuação = TRUNC(
        Σ (% de cada faixa × peso da faixa)            -- 13 termos, inclui turbo
      + aceleração/h × peso 15 + freada/h × peso 15    -- aceleração usa o peso da freada
      + velocidade/h × peso 13 + embreagem/h × peso 14
      + (horas × peso 18 + km × peso 18) ÷ meses       -- 0 para o não identificado
    , 2)

Pontos que parecem errados e estão preservados de propósito, porque é assim que
o cliente vê no BI: a aceleração usa o peso da freada (15, não 16); a distância
usa o peso de horas (18, não 19); a tolerância não entra.

Fontes, as mesmas do BI: `con_telemetry_day` (faixas) e `con_driver_h_km`
(km, horas, combustível e eventos). Denominador das faixas = soma das 13
faixas, não `total_time`.
"""

import math
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_read
from app.core.escopo import clausula_escopo, escopo_do_usuario
from app.middleware.auth import require_permission
from app.schemas.driver_ranking import (
    EventosPorHora,
    EventosQtd,
    FaixasMotorista,
    MotoristaRanking,
    RankingMotoristasResponse,
    ResumoRanking,
)

router = APIRouter()

#: Faixas da Pontuação: (chave na resposta, coluna-soma na consulta, range_id).
FAIXAS = [
    ("amarela", "am", 6),
    ("batendo_transmissao", "bl", 3),
    ("extra_economica", "ex", 5),
    ("inercia", "ine", 10),
    ("eco_roll", "er", 22),
    ("movimento_sem_tracao", "mst", 2),
    ("parado_acelerando", "sa", 9),
    ("parado_ligado", "pl", 0),
    ("verde", "g", 4),
    ("baixa_velocidade", "ls", 23),
    ("vermelha", "ve", 7),
]

#: As 13 faixas do denominador — inclui tolerância, que fica fora da nota.
DENOMINADOR = ["g", "ex", "ine", "sa", "bl", "am", "pl", "mst", "ve", "tol", "ls", "er"]


def _estrelas(nota: Optional[float]) -> int:
    if nota is None:
        return 0
    for corte, estrelas in ((90, 5), (80, 4), (70, 3), (60, 2), (50, 1)):
        if nota >= corte:
            return estrelas
    return 0


def _meses(inicio: date, fim: date) -> int:
    """Meses de calendário tocados pelo período — o `DISTINCTCOUNT(Mês)` do BI."""
    return (fim.year - inicio.year) * 12 + (fim.month - inicio.month) + 1


def _pct(parte: float, total: float) -> Optional[float]:
    return round(100 * parte / total, 1) if total > 0 else None


@router.get("/", response_model=RankingMotoristasResponse)
async def ranking_motoristas(
    start_date: Optional[date] = Query(None, description="Padrão: 30 dias antes de ontem"),
    end_date: Optional[date] = Query(None, description="Inclusivo. Padrão: ontem"),
    group_id: Optional[int] = Query(None, description="Restringe a uma empresa, dentro do escopo"),
    unit_id: Optional[int] = Query(None, description="Restringe a um veículo"),
    subgroup_id: Optional[int] = Query(None, description="Restringe a uma garagem"),
    por: str = Query(
        "motorista",
        pattern="^(motorista|veiculo)$",
        description="motorista (RANK do BI) ou veiculo (RANK POR PLACA). Com veiculo, driver_id na resposta é o id do veículo.",
    ),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    Pontuação, estrelas e posição de cada motorista (ou veículo) no período.

    O padrão termina **ontem**: `con_driver_h_km` nunca tem linha do dia
    corrente, e incluir hoje só puxaria as médias para baixo.

    O motorista não identificado (`driver_id` 0 ou nulo — no BI, o 7777) não
    entra no ranking; as horas dele aparecem no resumo, como no painel.
    """
    fim = end_date or (date.today() - timedelta(days=1))
    inicio = start_date or (fim - timedelta(days=30))
    if fim < inicio:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "end_date anterior a start_date")
    if (fim - inicio).days > 366:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Período máximo de 366 dias")

    grupos, subgrupos = escopo_do_usuario(current_user)

    def filtros(alias: str) -> tuple[str, dict]:
        sql, params = clausula_escopo(grupos, subgrupos, alias=alias)
        if group_id is not None:
            sql += f" AND {alias}.group_id = :group_id"
            params = {**params, "group_id": group_id}
        if subgroup_id is not None:
            sql += f" AND {alias}.subgroup_id = :subgroup_id"
            params = {**params, "subgroup_id": subgroup_id}
        if unit_id is not None:
            sql += f" AND {alias}.unit_id = :unit_id"
            params = {**params, "unit_id": unit_id}
        return sql, params

    # Coluna de agrupamento. Valor fixo de duas opções validadas no Query,
    # nunca texto do usuário — por isso pode entrar no SQL.
    chave = "unit_id" if por == "veiculo" else "driver_id"

    periodo = {"ini": inicio, "fim": fim}

    esc_h, par_h = filtros("h")
    hist = (
        await db.execute(
            text(
                f"""
                SELECT COALESCE(h.{chave}, 0) AS driver_id,
                       SUM(h.time_traveled_hist) / 3600.0 AS horas,
                       SUM(h.distance_traveled_hist) / 1000.0 AS km,
                       -- Combustível negativo vira 0 na carga do BI.
                       SUM(GREATEST(h.used_fuel_hist, 0)) / 1000.0 AS litros,
                       SUM(CASE WHEN h.used_fuel_hist > 0 THEN h.distance_traveled_hist ELSE 0 END)
                           / 1000.0 AS km_com_combustivel,
                       SUM(COALESCE(h.count_acel_excess, 0)) AS acel,
                       SUM(COALESCE(h.count_break_excess, 0)) AS freada,
                       SUM(COALESCE(h.count_clutch_excess, 0)) AS embreagem,
                       SUM(COALESCE(h.count_speed_excess, 0) + COALESCE(h.count_speed_excess_dry_l1, 0)
                         + COALESCE(h.count_speed_excess_dry_l2, 0) + COALESCE(h.count_speed_excess_dry_l3, 0))
                           AS velocidade,
                       SUM(COALESCE(h.count_speed_excess_wet_l1, 0) + COALESCE(h.count_speed_excess_wet_l2, 0)
                         + COALESCE(h.count_speed_excess_wet_l3, 0)) AS velocidade_chuva
                FROM mova.con_driver_h_km h
                WHERE h.dt >= :ini AND h.dt <= :fim {esc_h}
                GROUP BY 1
                """
            ),
            {**periodo, **par_h},
        )
    ).mappings().all()

    esc_t, par_t = filtros("t")
    tele = {
        r["driver_id"]: r
        for r in (
            await db.execute(
                text(
                    f"""
                    SELECT COALESCE(t.{chave}, 0) AS driver_id,
                           SUM(COALESCE(t.time_green, 0)) AS g,
                           SUM(COALESCE(t.time_extra_eco, 0)) AS ex,
                           SUM(COALESCE(t.time_inercia, 0)) AS ine,
                           SUM(COALESCE(t.time_stop_accel, 0)) AS sa,
                           SUM(COALESCE(t.time_blue, 0)) AS bl,
                           SUM(COALESCE(t.time_yellow, 0)) AS am,
                           SUM(COALESCE(t.time_stop_engine_on, 0)
                             + COALESCE(t.time_stop_engine_on_productive, 0)) AS pl,
                           SUM(COALESCE(t.time_stop_engine_on_productive, 0)) AS pp,
                           SUM(COALESCE(t.time_banguela, 0)) AS mst,
                           SUM(COALESCE(t.time_red, 0)) AS ve,
                           SUM(COALESCE(t.time_tolerancia, 0)) AS tol,
                           SUM(COALESCE(t.time_low_speed, 0)) AS ls,
                           SUM(COALESCE(t.time_eco_roll, 0)) AS er,
                           -- Turbo negativo ou acima de 10 h vira 0 na carga do BI.
                           SUM(CASE WHEN t.time_over_turbo_pressure BETWEEN 0 AND 36000
                                    THEN t.time_over_turbo_pressure ELSE 0 END) AS tov,
                           SUM(CASE WHEN t.time_under_turbo_pressure BETWEEN 0 AND 36000
                                    THEN t.time_under_turbo_pressure ELSE 0 END) AS tun
                    FROM mova.con_telemetry_day t
                    WHERE t.day >= :ini AND t.day <= :fim {esc_t}
                    GROUP BY 1
                    """
                ),
                {**periodo, **par_t},
            )
        ).mappings()
    }

    esc_w, par_w = filtros("w")
    pesos = {
        r["range_id"]: float(r["peso"])
        for r in (
            await db.execute(
                text(
                    f"""
                    -- Peso -1 vira -2 na carga do BI (P2). A média é por faixa,
                    -- sobre os subgrupos do escopo, como a medida "Ponto <faixa>".
                    SELECT w.range_id, AVG(CASE WHEN w.weight = -1 THEN -2 ELSE w.weight END) AS peso
                    FROM mova.weight_range w
                    WHERE w.weight IS NOT NULL {esc_w}
                    GROUP BY w.range_id
                    """
                ),
                par_w,
            )
        ).mappings()
    }
    peso = lambda rid: pesos.get(rid, 0.0)  # noqa: E731

    ids = [r["driver_id"] for r in hist if r["driver_id"]]
    cadastro = {}
    if ids and por == "veiculo":
        cadastro = {
            r["id"]: r
            for r in (
                await db.execute(
                    text(
                        "SELECT id, CONCAT_WS(' · ', NULLIF(TRIM(label2), ''), label) AS name"
                        " FROM mova.tracked_unit WHERE id = ANY(:ids)"
                    ),
                    {"ids": ids},
                )
            ).mappings()
        }
    elif ids:
        cadastro = {
            r["id"]: r
            for r in (
                await db.execute(
                    text(
                        "SELECT id, name, cnh_validate, cnh, cnh_category"
                        " FROM mova.driver WHERE id = ANY(:ids)"
                    ),
                    {"ids": ids},
                )
            ).mappings()
        }

    meses = _meses(inicio, fim)
    linhas: list[MotoristaRanking] = []
    horas_total = km_total = horas_nao_identificado = 0.0

    for h in hist:
        horas = float(h["horas"] or 0)
        km = float(h["km"] or 0)
        horas_total += horas
        km_total += km
        if not h["driver_id"]:
            horas_nao_identificado += horas
            continue

        t = tele.get(h["driver_id"])
        fx = sum(float(t[c] or 0) for c in DENOMINADOR) if t else 0.0
        litros = float(h["litros"] or 0)
        por_hora = (lambda n: float(n or 0) / horas) if horas > 0 else (lambda n: 0.0)

        if fx > 0:
            parte_faixas = sum(float(t[col] or 0) / fx * peso(rid) for _, col, rid in FAIXAS)
            parte_faixas += float(t["tov"] or 0) / fx * peso(11)
            abaixo = float(t["tun"] or 0) / fx
            # "% abaixoTurbo" vale 0 fora de (0; 1,1].
            parte_faixas += (abaixo if 0 < abaixo <= 1.1 else 0) * peso(12)
            parte_eventos = (
                (por_hora(h["acel"]) + por_hora(h["freada"])) * peso(15)
                + por_hora(h["velocidade"]) * peso(13)
                + por_hora(h["embreagem"]) * peso(14)
            )
            parte_volume = (horas * peso(18) + km * peso(18)) / meses
            bruto = parte_faixas + parte_eventos + parte_volume
            pontuacao = math.trunc(bruto * 100) / 100
        else:
            pontuacao = None

        cad = cadastro.get(h["driver_id"]) or {}
        linhas.append(
            MotoristaRanking(
                driver_id=h["driver_id"],
                nome=cad.get("name"),
                cnh_validade=cad.get("cnh_validate"),
                cnh_numero=(cad.get("cnh") or "").strip() or None,
                cnh_categoria=(cad.get("cnh_category") or "").strip() or None,
                km=round(km, 1),
                horas=round(horas, 1),
                litros=round(litros, 1),
                kml=round(float(h["km_com_combustivel"] or 0) / litros, 2) if litros > 0 else None,
                pontuacao=pontuacao,
                estrelas=_estrelas(pontuacao),
                faixas=FaixasMotorista(
                    **{chave: _pct(float(t[col] or 0), fx) for chave, col, _ in FAIXAS} if t else {},
                    tolerancia=_pct(float(t["tol"] or 0), fx) if t else None,
                    parado_produtivo=_pct(float(t["pp"] or 0), fx) if t else None,
                ),
                eventos_por_hora=EventosPorHora(
                    aceleracao_brusca=round(por_hora(h["acel"]), 2),
                    freada_brusca=round(por_hora(h["freada"]), 2),
                    velocidade_excessiva=round(por_hora(h["velocidade"]), 2),
                    embreagem=round(por_hora(h["embreagem"]), 2),
                ),
                eventos=EventosQtd(
                    aceleracao_brusca=int(h["acel"] or 0),
                    freada_brusca=int(h["freada"] or 0),
                    velocidade_excessiva=int(h["velocidade"] or 0),
                    velocidade_chuva=int(h["velocidade_chuva"] or 0),
                    embreagem=int(h["embreagem"] or 0),
                ),
                sem_faixas=fx <= 0,
            )
        )

    # Posição densa, maior nota primeiro — o RANKX(..., DESC, Dense) do BI.
    # Quem não tem nota fica no fim, sem posição.
    linhas.sort(key=lambda m: (m.pontuacao is None, -(m.pontuacao or 0), -m.km))
    posicao, anterior = 0, None
    for m in linhas:
        if m.pontuacao is None:
            continue
        if m.pontuacao != anterior:
            posicao += 1
            anterior = m.pontuacao
        m.posicao = posicao

    notas = [m.pontuacao for m in linhas if m.pontuacao is not None]
    return RankingMotoristasResponse(
        inicio=inicio,
        fim=fim,
        resumo=ResumoRanking(
            motoristas=len(linhas),
            km_total=round(km_total, 1),
            horas_total=round(horas_total, 1),
            nota_media=round(sum(notas) / len(notas), 2) if notas else None,
            pct_horas_nao_identificado=_pct(horas_nao_identificado, horas_total),
            pesos_cadastrados=bool(pesos),
        ),
        motoristas=linhas,
    )
