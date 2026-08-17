"""
POI endpoints — pontos de interesse e cercas.

O serviço `ss-gateway-poi_fence` calcula entrada e saída, e `con_telemetry`
guarda o resultado em `start_poi_id`, `end_area_id` e nomes. Mas o cadastro em
si vive numa tabela que este repositório não mapeia.

Então em vez de adivinhar o nome dessa tabela, estes endpoints derivam os
pontos **do uso real**: quais POIs e áreas aparecem nas viagens e nas paradas de
linha. É menos completo que ler o cadastro — um ponto cadastrado e nunca
visitado não aparece — mas é correto, e não quebra se o nome da tabela for
outro.

Quando o cadastro for mapeado, estes endpoints passam a lê-lo e o contrato não
muda.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_read
from app.core.logging import get_logger
from app.middleware.auth import require_permission
from app.schemas.pois import PoiResponse, PoiVisitResponse

logger = get_logger(__name__)
router = APIRouter()


@router.get("/", response_model=list[PoiResponse])
async def list_pois(
    days: int = Query(30, ge=1, le=180, description="Janela de observação"),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    Pontos e áreas observados nas viagens, com a frequência de uso.

    Reúne origem e destino numa lista só: o mesmo ponto costuma aparecer nas
    duas pontas, e separá-los faria a contagem parecer metade do que é.
    """
    desde = datetime.now() - timedelta(days=days)

    filtro_nome = " AND nome ILIKE :search" if search else ""
    params: dict = {"desde": desde}
    if search:
        params["search"] = f"%{search}%"

    rows = (
        await db.execute(
            text(
                f"""
                WITH usos AS (
                    SELECT start_poi_id AS poi_id, start_poi_name AS nome,
                           'poi' AS tipo, start_lat AS lat, start_lon AS lon
                    FROM mova.con_telemetry
                    WHERE start_time >= :desde AND start_poi_id IS NOT NULL
                    UNION ALL
                    SELECT end_poi_id, end_poi_name, 'poi', end_lat, end_lon
                    FROM mova.con_telemetry
                    WHERE start_time >= :desde AND end_poi_id IS NOT NULL
                    UNION ALL
                    SELECT start_area_id, start_area_name, 'area', start_lat, start_lon
                    FROM mova.con_telemetry
                    WHERE start_time >= :desde AND start_area_id IS NOT NULL
                    UNION ALL
                    SELECT end_area_id, end_area_name, 'area', end_lat, end_lon
                    FROM mova.con_telemetry
                    WHERE start_time >= :desde AND end_area_id IS NOT NULL
                )
                SELECT
                    poi_id, nome, tipo,
                    COUNT(*) AS visits,
                    -- Média das coordenadas observadas: o ponto exato está no
                    -- cadastro, e a média das passagens é a melhor
                    -- aproximação disponível para desenhar no mapa.
                    ROUND(AVG(lat)::numeric, 6) AS latitude,
                    ROUND(AVG(lon)::numeric, 6) AS longitude
                FROM usos
                WHERE nome IS NOT NULL{filtro_nome}
                GROUP BY poi_id, nome, tipo
                ORDER BY visits DESC
                LIMIT 500
                """
            ),
            params,
        )
    ).mappings().all()

    return [
        PoiResponse(
            id=r["poi_id"],
            name=r["nome"],
            type=r["tipo"],
            visits=r["visits"],
            latitude=float(r["latitude"]) if r["latitude"] is not None else None,
            longitude=float(r["longitude"]) if r["longitude"] is not None else None,
            source="derivado das viagens",
        )
        for r in rows
    ]


@router.get("/{poi_id}/visits", response_model=list[PoiVisitResponse])
async def list_poi_visits(
    poi_id: int,
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    Passagens por um ponto.

    Serve para conferir cumprimento: quem passou, quando e a que velocidade.
    Entrada e saída aparecem como registros distintos porque a mesma viagem
    pode tocar o ponto duas vezes, e agrupá-las esconderia o tempo de
    permanência.
    """
    desde = datetime.now() - timedelta(days=days)

    rows = (
        await db.execute(
            text(
                """
                SELECT
                    t.trip_id, t.unit_id, tu.label AS vehicle_label,
                    tu.label2 AS vehicle_prefix, t.driver_name,
                    t.start_time AS moment, 'entrada' AS direction,
                    t.start_poi_distance AS distance_m
                FROM mova.con_telemetry t
                JOIN mova.tracked_unit tu ON tu.id = t.unit_id
                WHERE t.start_poi_id = :poi_id AND t.start_time >= :desde
                UNION ALL
                SELECT
                    t.trip_id, t.unit_id, tu.label, tu.label2, t.driver_name,
                    t.end_time, 'saida', t.end_poi_distance
                FROM mova.con_telemetry t
                JOIN mova.tracked_unit tu ON tu.id = t.unit_id
                WHERE t.end_poi_id = :poi_id AND t.start_time >= :desde
                ORDER BY moment DESC
                LIMIT :limit
                """
            ),
            {"poi_id": poi_id, "desde": desde, "limit": limit},
        )
    ).mappings().all()

    return [PoiVisitResponse(**r) for r in rows]
