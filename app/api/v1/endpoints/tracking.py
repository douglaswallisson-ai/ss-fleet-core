"""
Tracking endpoints — eventos de percurso.

O banco guarda posição a cada poucos segundos em `dev_status_30`, mas ninguém
guarda **evento**: quando o veículo ligou, quando parou, quanto tempo ficou
parado com o motor ligado. Sem isso o mapa mostra onde o carro está e não como
chegou ali — e não há como conferir jornada, justificar consumo nem
reconstituir um sinistro.

Este endpoint deriva os eventos das posições, em SQL. A alternativa seria
mandar milhares de posições para o navegador e processar lá; para um dia de um
veículo isso passa de 10 mil registros, e o front travaria antes de desenhar a
primeira linha.
"""

from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_read
from app.core.logging import get_logger
from app.middleware.auth import require_permission
from app.schemas.tracking import (
    TrackingEvent,
    TrackingResponse,
    TrackingSummary,
)

logger = get_logger(__name__)
router = APIRouter()

#: Abaixo disto o veículo é considerado parado. Não é zero porque o GPS oscila
#: alguns km/h mesmo com o carro imóvel, e cada oscilação viraria um evento.
LIMIAR_MOVIMENTO_KMH = 3

#: Parada menor que isto é ruído de semáforo, não evento operacional.
PARADA_MINIMA_SEGUNDOS = 120


@router.get("/", response_model=TrackingResponse)
async def get_tracking(
    unit_id: int = Query(..., description="Veículo"),
    operation_date: date = Query(..., description="Data de operação (AAAA-MM-DD)"),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    Eventos de percurso de um veículo num dia.

    A derivação usa funções de janela sobre `dev_status_30`: comparando cada
    posição com a anterior, uma mudança de estado (ignição, movimento) marca um
    evento. Fazer isso no banco evita trafegar dezenas de milhares de posições
    só para descobrir meia dúzia de transições.
    """
    inicio = datetime.combine(operation_date, datetime.min.time())
    fim = inicio + timedelta(days=1)

    sql = """
        WITH posicoes AS (
            SELECT
                id, unit_id, local_time, latitude, longitude,
                ignition, speed, odom, address,
                LAG(ignition)  OVER (ORDER BY local_time) AS ign_anterior,
                LAG(speed)     OVER (ORDER BY local_time) AS vel_anterior,
                LEAD(local_time) OVER (ORDER BY local_time) AS proximo_instante
            FROM mova.dev_status_30
            WHERE unit_id = :unit_id
              AND local_time >= :inicio
              AND local_time <  :fim
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL
        ),
        transicoes AS (
            SELECT
                *,
                CASE
                    -- A ordem importa: ignição tem precedência sobre movimento,
                    -- senão desligar o motor em movimento viraria "parada".
                    WHEN ignition IS TRUE  AND (ign_anterior IS FALSE OR ign_anterior IS NULL)
                        THEN 'ignicao_ligada'
                    WHEN ignition IS FALSE AND ign_anterior IS TRUE
                        THEN 'ignicao_desligada'
                    WHEN COALESCE(speed, 0) >  :limiar
                     AND COALESCE(vel_anterior, 0) <= :limiar
                        THEN 'retomada'
                    WHEN COALESCE(speed, 0) <= :limiar
                     AND COALESCE(vel_anterior, 0) >  :limiar
                        THEN 'parada'
                    ELSE NULL
                END AS tipo,
                EXTRACT(EPOCH FROM (proximo_instante - local_time)) AS segundos_ate_proximo
            FROM posicoes
        )
        SELECT
            id, unit_id, local_time, latitude, longitude,
            ignition, speed, odom, address, tipo,
            segundos_ate_proximo
        FROM transicoes
        WHERE tipo IS NOT NULL
        ORDER BY local_time
    """

    rows = (
        await db.execute(
            text(sql),
            {"unit_id": unit_id, "inicio": inicio, "fim": fim, "limiar": LIMIAR_MOVIMENTO_KMH},
        )
    ).mappings().all()

    if not rows:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Nenhuma posição registrada para este veículo nesta data.",
        )

    eventos: list[TrackingEvent] = []
    for i, r in enumerate(rows):
        tipo = r["tipo"]
        duracao = None

        # Duração do estado que começa aqui: até a próxima transição.
        if i + 1 < len(rows):
            duracao = int(
                (rows[i + 1]["local_time"] - r["local_time"]).total_seconds() / 60
            )

        # Parada muito curta é semáforo, não evento operacional — vira ruído no
        # relatório e esconde as paradas que importam.
        if tipo == "parada" and duracao is not None and duracao * 60 < PARADA_MINIMA_SEGUNDOS:
            continue

        # Parado com ignição ligada é o caso que mais custa combustível, e por
        # isso ganha tipo próprio em vez de virar só "parada".
        if tipo == "parada" and r["ignition"] is True:
            tipo = "ligado_parado"

        eventos.append(
            TrackingEvent(
                id=r["id"],
                unit_id=r["unit_id"],
                type=tipo,
                timestamp=r["local_time"],
                latitude=float(r["latitude"]),
                longitude=float(r["longitude"]),
                speed=r["speed"],
                odometer=r["odom"],
                address=r["address"],
                duration_min=duracao,
            )
        )

    resumo = _resumir(eventos, rows)

    logger.info(
        "tracking_derived",
        unit_id=unit_id, date=str(operation_date),
        positions=len(rows), events=len(eventos),
    )

    return TrackingResponse(
        unit_id=unit_id,
        operation_date=operation_date,
        summary=resumo,
        events=eventos,
    )


def _resumir(eventos: list[TrackingEvent], posicoes) -> TrackingSummary:
    """
    Consolida o dia.

    Tempo ligado vem da diferença entre a primeira e a última ignição, não da
    soma dos trechos: se o equipamento perder comunicação no meio, somar
    trechos subestimaria o total.
    """
    ligadas = [e for e in eventos if e.type == "ignicao_ligada"]
    desligadas = [e for e in eventos if e.type == "ignicao_desligada"]

    primeira = ligadas[0].timestamp if ligadas else None
    ultima = desligadas[-1].timestamp if desligadas else None

    minutos_ligado = (
        int((ultima - primeira).total_seconds() / 60) if primeira and ultima else 0
    )
    minutos_ocioso = sum(
        e.duration_min or 0 for e in eventos if e.type == "ligado_parado"
    )
    minutos_parado = sum(e.duration_min or 0 for e in eventos if e.type == "parada")

    odometros = [p["odom"] for p in posicoes if p["odom"] is not None]
    velocidades = [p["speed"] for p in posicoes if p["speed"] is not None]

    return TrackingSummary(
        first_ignition=primeira,
        last_ignition=ultima,
        minutes_on=minutos_ligado,
        minutes_moving=max(0, minutos_ligado - minutos_ocioso - minutos_parado),
        minutes_idle=minutos_ocioso,
        stops=sum(1 for e in eventos if e.type in ("parada", "ligado_parado")),
        distance_km=round((max(odometros) - min(odometros)) / 1000, 1) if len(odometros) > 1 else 0.0,
        max_speed=max(velocidades) if velocidades else 0,
        positions_analyzed=len(posicoes),
    )
