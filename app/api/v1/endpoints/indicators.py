"""
Indicators endpoints — indicadores consolidados do setor.

CPK, IPK e MKBF são o vocabulário de quem gere frota de ônibus, e nenhum
existia calculado. Os insumos estão em `con_telemetry` (km, combustível, tempo)
e em `fleet_events` (falhas) — faltava a consolidação.

O que **não** dá para calcular aqui, e por isso não é devolvido: custo. Não há
tabela de custo operacional no banco, então CPK e custo por passageiro ficam de
fora. Devolver um número inventado seria pior que a ausência: ele apareceria num
relatório de diretoria como se fosse medido.
"""

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_read
from app.core.logging import get_logger
from app.middleware.auth import require_permission
from app.core.escopo import clausula_escopo, escopo_do_usuario
from app.schemas.indicators import IndicatorPeriod, IndicatorsResponse

logger = get_logger(__name__)
router = APIRouter()


@router.get("/", response_model=IndicatorsResponse)
async def get_indicators(
    start_date: date = Query(..., description="Início do período"),
    end_date: date = Query(..., description="Fim do período"),
    group_id: Optional[int] = Query(None),
    compare_previous: bool = Query(True, description="Traz o período anterior de mesma duração"),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    Consolidado do período, com o anterior para comparação.

    O período anterior tem a **mesma duração**, não o mês calendário anterior:
    comparar 30 dias com 28 distorceria todos os totais, e é o erro mais comum
    em relatório de frota.
    """
    inicio = datetime.combine(start_date, datetime.min.time())
    fim = datetime.combine(end_date, datetime.max.time())
    duracao = fim - inicio

    # O filtro por grupo é opcional e vem da tela; o escopo do usuário é
    # obrigatório e vem do token. Sem o segundo, qualquer autenticado veria o
    # consolidado de todos os clientes.
    grupos, subgrupos = escopo_do_usuario(current_user)

    atual = await _consolidar(db, inicio, fim, group_id, grupos, subgrupos)

    anterior = None
    if compare_previous:
        anterior = await _consolidar(db, inicio - duracao, inicio, group_id, grupos, subgrupos)

    logger.info(
        "indicators_calculated",
        start=str(start_date), end=str(end_date), group_id=group_id,
    )

    return IndicatorsResponse(
        period_start=start_date,
        period_end=end_date,
        current=atual,
        previous=anterior,
        # Declarado na resposta para a tela não precisar adivinhar por que
        # faltam campos de custo.
        unavailable=["cpk", "cost_per_passenger", "ipk"],
        unavailable_reason=(
            "Custo operacional e contagem de passageiros não existem no banco. "
            "CPK, custo por passageiro e IPK dependem desses insumos."
        ),
    )


async def _consolidar(
    db: AsyncSession,
    inicio: datetime,
    fim: datetime,
    group_id: Optional[int],
    grupos: list[int],
    subgrupos: list[int],
) -> IndicatorPeriod:
    """
    Agrega telemetria e falhas do intervalo.

    A distância vem da soma de `distance_traveled` por viagem, e não da
    diferença de odômetro entre a primeira e a última: troca de equipamento ou
    reset de odômetro produziria saltos absurdos.
    """
    filtro_grupo = " AND tu.group_id = :group_id" if group_id else ""
    escopo_sql, escopo_params = clausula_escopo(grupos, subgrupos, alias="tu")
    filtro_grupo += escopo_sql

    params: dict = {"inicio": inicio, "fim": fim, **escopo_params}
    if group_id:
        params["group_id"] = group_id

    tel = (
        await db.execute(
            text(
                f"""
                SELECT
                    COUNT(*)                                   AS trips,
                    COUNT(DISTINCT t.unit_id)                  AS active_vehicles,
                    COUNT(DISTINCT t.driver_id)                AS active_drivers,
                    COALESCE(SUM(t.distance_traveled), 0)      AS distance_km,
                    COALESCE(SUM(t.fuel_used), 0)              AS fuel_liters,
                    COALESCE(SUM(t.total_time), 0)             AS total_seconds,
                    COALESCE(SUM(t.time_moving), 0)            AS moving_seconds,
                    COALESCE(SUM(t.time_stop_engine_on), 0)    AS idle_seconds,
                    COALESCE(SUM(t.time_green + t.time_extra_eco), 0) AS green_seconds,
                    COALESCE(SUM(t.time_raining), 0)           AS rain_seconds,
                    COALESCE(SUM(t.count_hard_brake), 0)       AS hard_brakes,
                    COALESCE(SUM(t.count_hard_acel), 0)        AS hard_accelerations,
                    COALESCE(SUM(t.count_speed_violation_l2 + t.count_speed_violation_l3), 0)
                                                                AS speed_violations
                FROM mova.con_telemetry t
                JOIN mova.tracked_unit tu ON tu.id = t.unit_id
                WHERE t.start_time >= :inicio
                  AND t.start_time <= :fim{filtro_grupo}
                """
            ),
            params,
        )
    ).mappings().first()

    falhas = (
        await db.execute(
            text(
                f"""
                SELECT COUNT(*) AS failures
                FROM mova.fleet_events e
                JOIN mova.tracked_unit tu ON tu.id = e.vehicle_id
                WHERE e.timestamp >= :inicio
                  AND e.timestamp <= :fim
                  AND e.severity = 'CRITICAL'{filtro_grupo}
                """
            ),
            params,
        )
    ).mappings().first()

    km = float(tel["distance_km"] or 0)
    litros = float(tel["fuel_liters"] or 0)
    n_falhas = int(falhas["failures"] or 0)
    total_s = int(tel["total_seconds"] or 0)
    movimento_s = int(tel["moving_seconds"] or 0)

    return IndicatorPeriod(
        trips=tel["trips"],
        active_vehicles=tel["active_vehicles"],
        active_drivers=tel["active_drivers"],
        distance_km=round(km, 1),
        fuel_liters=round(litros, 1),
        # Consumo: quilômetros por litro. Zero litros significa frota elétrica
        # ou dado ausente — devolver zero é melhor que dividir por zero.
        kml=round(km / litros, 2) if litros else 0.0,
        # MKBF: quilômetros médios entre falhas. Sem falha no período, o
        # indicador é a própria quilometragem, não infinito.
        mkbf=round(km / n_falhas, 0) if n_falhas else round(km, 0),
        failures=n_falhas,
        total_hours=round(total_s / 3600, 1),
        moving_hours=round(movimento_s / 3600, 1),
        idle_hours=round(int(tel["idle_seconds"] or 0) / 3600, 1),
        idle_pct=round(int(tel["idle_seconds"] or 0) / total_s * 100, 1) if total_s else 0.0,
        green_band_pct=round(int(tel["green_seconds"] or 0) / movimento_s * 100, 1) if movimento_s else 0.0,
        rain_pct=round(int(tel["rain_seconds"] or 0) / total_s * 100, 1) if total_s else 0.0,
        hard_brakes=tel["hard_brakes"],
        hard_accelerations=tel["hard_accelerations"],
        speed_violations=tel["speed_violations"],
        # Score normalizado: eventos por 100 km. Sem normalizar, quem roda mais
        # sempre parece pior.
        events_per_100km=round(
            (int(tel["hard_brakes"] or 0) + int(tel["hard_accelerations"] or 0) + int(tel["speed_violations"] or 0))
            / km * 100,
            2,
        )
        if km
        else 0.0,
    )
