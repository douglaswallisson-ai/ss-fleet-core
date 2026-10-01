"""
Saúde da frota — cascata de decisão (T11 / DS-1511).

Mede **qualidade de sinal e comportamento de condução**, não manutenção. Cada
unidade é avaliada contra uma sequência de condições; a primeira que bate a
classifica, e quem não dispara nenhuma é saudável.

O indicador do cartão é a fração de unidades saudáveis sobre o total.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_read
from app.core.escopo import clausula_escopo, escopo_do_usuario
from app.middleware.auth import require_permission
from app.models.user import User
from app.schemas.fleet_health import FleetHealthResponse, UnidadeNaoSaudavel

router = APIRouter()


# ------------------------------------------------------------------ #
# Limiares da cascata                                                 #
# ------------------------------------------------------------------ #

#: Abaixo disto o veículo não tem operação suficiente para ser avaliado.
#:
#: Sem a porta de entrada, carro que ficou parado no pátio reprovaria em
#: várias condições ao mesmo tempo — e não há nada de errado com ele.
MIN_HORAS = 1
MIN_KM = 1

#: `pct_*` sobre a soma das faixas; `mh_*` sobre horas trabalhadas.
LIMITES = {
    "telemetria": 0.1,           # total_time / horas — cobertura do sinal
    "parado_acelerando": 0.1,
    "inercia_min": 0.06,
    "inercia_max": 0.47,
    "vermelha": 0.01,
    "amarela": 0.1,
    "batendo_transmissao": 0.1,
    "movimento_sem_tracao": 0.04,
    "tolerancia": 0.25,
    "nao_identificado": 0.60,
    "mh_velocidade": 10,
    "mh_embreagem": 30,
    "mh_aceleracao": 10,
    "mh_freada": 10,
}


@router.get("/", response_model=FleetHealthResponse)
async def get_fleet_health(
    group_id: Optional[int] = Query(None, description="Filtrar por empresa"),
    referencia: Optional[date] = Query(
        None,
        description="Dia a avaliar. Padrão: último dia com dado.",
    ),
    db: AsyncSession = Depends(get_db_read),
    current_user: User = Depends(require_permission("reports", "read")),
):
    grupos, subgrupos = escopo_do_usuario(current_user)
    escopo_sql, escopo_params = clausula_escopo(grupos, subgrupos, alias="dhk")

    # Período de referência: o último dia COM DADO, não a data de hoje.
    #
    # `con_driver_h_km` é consolidada em lote e nunca tem linha do dia
    # corrente. Usar CURRENT_DATE devolveria vazio todos os dias, e o cartão
    # mostraria frota sem dado numa operação que rodou normalmente.
    if referencia is None:
        referencia = (
            await db.execute(text("SELECT MAX(dt) FROM mova.con_driver_h_km"))
        ).scalar()

    if referencia is None:
        return FleetHealthResponse(
            referencia=None,
            total_unidades=0,
            unidades_saudaveis=0,
            unidades_nao_saudaveis=0,
            percentual_saudavel=None,
            por_motivo={},
            nao_saudaveis=[],
        )

    filtro_grupo = " AND dhk.group_id = :group_id" if group_id else ""
    params = {"ref": referencia, **escopo_params}
    if group_id:
        params["group_id"] = group_id

    linhas = (
        await db.execute(
            text(
                f"""
                SELECT
                    dhk.unit_id,
                    dhk.label,
                    SUM(dhk.time_traveled_hist)     AS horas_seg,
                    SUM(dhk.distance_traveled_hist) AS distancia_m,
                    SUM(td.total_time)              AS total_time,

                    -- Soma das 13 faixas. É o denominador dos percentuais:
                    -- `total_time` infla porque conta ignição ligada com
                    -- motor desligado, e todo indicador pareceria melhor.
                    SUM(
                        COALESCE(td.time_green, 0)
                      + COALESCE(td.time_stop_engine_on_productive, 0)
                      + COALESCE(td.time_low_speed, 0)
                      + COALESCE(td.time_eco_roll, 0)
                      + COALESCE(td.time_extra_eco, 0)
                      + COALESCE(td.time_inercia, 0)
                      + COALESCE(td.time_stop_accel, 0)
                      + COALESCE(td.time_blue, 0)
                      + COALESCE(td.time_yellow, 0)
                      + COALESCE(td.time_stop_engine_on, 0)
                      + COALESCE(td.time_banguela, 0)
                      + COALESCE(td.time_red, 0)
                      + COALESCE(td.time_tolerancia, 0)
                    ) AS faixas_total,

                    SUM(COALESCE(td.time_stop_accel, 0)) AS t_parado_acel,
                    SUM(COALESCE(td.time_inercia, 0))    AS t_inercia,
                    SUM(COALESCE(td.time_red, 0))        AS t_vermelha,
                    SUM(COALESCE(td.time_yellow, 0))     AS t_amarela,
                    -- `time_blue` é BATENDO TRANSMISSÃO, apesar do nome.
                    SUM(COALESCE(td.time_blue, 0))       AS t_batendo,
                    -- `time_banguela` é movimento sem tração.
                    SUM(COALESCE(td.time_banguela, 0))   AS t_sem_tracao,
                    SUM(COALESCE(td.time_tolerancia, 0)) AS t_tolerancia,

                    -- Contadores de con_driver_h_km (count_acel_excess etc.),
                    -- como na fórmula do agregador. Os de con_telemetry_day
                    -- (count_over_speed, count_hard_acel…) são outra contagem:
                    -- num dia da FERTRAN, 7.595 excessos de velocidade lá
                    -- contra 0 aqui, e embreagem 0 lá contra 29 aqui — a
                    -- cascata disparava "Velocidade Excessiva" sem motivo.
                    -- Velocidade = as quatro colunas, como `qtd_vel` do BI.
                    SUM(COALESCE(dhk.count_speed_excess, 0)
                      + COALESCE(dhk.count_speed_excess_dry_l1, 0)
                      + COALESCE(dhk.count_speed_excess_dry_l2, 0)
                      + COALESCE(dhk.count_speed_excess_dry_l3, 0)) AS c_velocidade,
                    SUM(COALESCE(dhk.count_clutch_excess, 0))      AS c_embreagem,
                    SUM(COALESCE(dhk.count_acel_excess, 0))        AS c_aceleracao,
                    SUM(COALESCE(dhk.count_break_excess, 0))       AS c_freada,

                    -- driver_id zero é sentinela de "sem condutor
                    -- identificado", não um motorista de id zero.
                    SUM(CASE WHEN dhk.driver_id = 0
                             THEN dhk.time_traveled_hist ELSE 0 END) AS seg_sem_condutor

                FROM mova.con_driver_h_km dhk
                JOIN mova.con_telemetry_day td
                       ON td.unit_id = dhk.unit_id
                      AND td.day = dhk.dt
                      -- COALESCE nos dois lados: uma tabela grava nulo e a
                      -- outra grava zero para "sem condutor".
                      AND COALESCE(td.driver_id, 0) = COALESCE(dhk.driver_id, 0)
                JOIN mova.tracked_unit tu ON tu.id = dhk.unit_id
                WHERE dhk.dt = :ref
                  {filtro_grupo}
                  {escopo_sql}
                GROUP BY dhk.unit_id, dhk.label
                """
            ),
            params,
        )
    ).mappings().all()

    nao_saudaveis: list[UnidadeNaoSaudavel] = []
    por_motivo: dict[str, int] = {}

    for r in linhas:
        horas = (r["horas_seg"] or 0) / 3600
        km = (r["distancia_m"] or 0) / 1000
        faixas = r["faixas_total"] or 0

        # Porta de entrada: sem operação não há o que reprovar.
        if horas <= MIN_HORAS or km <= MIN_KM:
            continue

        pct = lambda v: (v or 0) / faixas if faixas else 0  # noqa: E731
        mh = lambda v: (v or 0) / horas if horas else 0     # noqa: E731

        # A ordem importa: a primeira condição que bate classifica, e a
        # unidade não acumula motivos. A categoria 4 é avaliada por último,
        # apesar do número — a numeração não reflete a ordem.
        achado: Optional[tuple[int, str, float]] = None

        cobertura = (r["total_time"] or 0) / (r["horas_seg"] or 1)
        if cobertura <= LIMITES["telemetria"]:
            achado = (1, "Verificar Telemetria", cobertura)
        elif pct(r["t_parado_acel"]) > LIMITES["parado_acelerando"]:
            achado = (2, "Verificar Sinal de Velocidade", pct(r["t_parado_acel"]))
        elif not (LIMITES["inercia_min"] <= pct(r["t_inercia"]) <= LIMITES["inercia_max"]):
            achado = (3, "Verificar Inércia", pct(r["t_inercia"]))
        elif pct(r["t_vermelha"]) > LIMITES["vermelha"]:
            achado = (5, "Verificar Faixa Vermelha", pct(r["t_vermelha"]))
        elif pct(r["t_amarela"]) > LIMITES["amarela"]:
            achado = (6, "Verificar Faixa Amarela", pct(r["t_amarela"]))
        elif pct(r["t_batendo"]) > LIMITES["batendo_transmissao"]:
            achado = (7, "Verificar Faixa Batendo Transmissão", pct(r["t_batendo"]))
        elif pct(r["t_sem_tracao"]) > LIMITES["movimento_sem_tracao"]:
            achado = (8, "Verificar Faixa Mov. Sem Tração", pct(r["t_sem_tracao"]))
        elif horas > MIN_HORAS and km < MIN_KM:
            achado = (9, "Verificar Informação de Odômetro", km)
        elif mh(r["c_velocidade"]) > LIMITES["mh_velocidade"]:
            achado = (10, "Verificar Velocidade Excessiva", mh(r["c_velocidade"]))
        elif mh(r["c_embreagem"]) > LIMITES["mh_embreagem"]:
            achado = (11, "Verificar Embreagem Excessiva", mh(r["c_embreagem"]))
        elif mh(r["c_aceleracao"]) > LIMITES["mh_aceleracao"]:
            achado = (12, "Verificar Parâmetro Aceleração Brusca", mh(r["c_aceleracao"]))
        elif mh(r["c_freada"]) > LIMITES["mh_freada"]:
            achado = (13, "Verificar Parâmetro Freada Brusca", mh(r["c_freada"]))
        else:
            nao_ident = (r["seg_sem_condutor"] or 0) / (r["horas_seg"] or 1)
            if nao_ident > LIMITES["nao_identificado"]:
                achado = (14, f"% Não Informado Alto — {round(nao_ident * 100)}%", nao_ident)
            elif pct(r["t_tolerancia"]) > LIMITES["tolerancia"]:
                achado = (4, "Verificar Tolerância", pct(r["t_tolerancia"]))

        if achado:
            categoria, motivo, valor = achado
            nao_saudaveis.append(
                UnidadeNaoSaudavel(
                    unit_id=r["unit_id"],
                    label=r["label"],
                    categoria=categoria,
                    motivo=motivo,
                    valor=round(float(valor), 4),
                )
            )
            # O rótulo dinâmico da 14 não agrupa bem — usa o genérico.
            chave = "% Não Informado Alto" if categoria == 14 else motivo
            por_motivo[chave] = por_motivo.get(chave, 0) + 1

    total = len(linhas)
    ruins = len(nao_saudaveis)

    return FleetHealthResponse(
        referencia=referencia,
        total_unidades=total,
        unidades_saudaveis=total - ruins,
        unidades_nao_saudaveis=ruins,
        # Nulo sem unidade avaliável: zero seria lido como frota inteira com
        # problema, quando o caso é não haver o que avaliar.
        percentual_saudavel=round(((total - ruins) / total) * 100, 1) if total else None,
        por_motivo=dict(sorted(por_motivo.items(), key=lambda x: -x[1])),
        nao_saudaveis=sorted(nao_saudaveis, key=lambda u: u.categoria),
    )
