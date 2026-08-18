"""
Video endpoints — equipamentos e ocorrências.

O `ss-fleet-core` só tinha o vínculo veículo↔equipamento
(`/video-device-associations`). Faltava o que interessa ao operador: quais
veículos têm câmera, qual o estado de comunicação de cada uma, e quais
ocorrências aguardam tratativa.

As ocorrências de vídeo vivem em `fleet_events`, filtradas pelos tipos que a
câmera gera. Separar num recurso próprio é decisão de produto: quem cuida de
segurança embarcada trabalha com uma fila diferente de quem cuida de alarme
operacional, e misturar as duas faz as duas serem ignoradas.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_read
from app.core.logging import get_logger
from app.middleware.auth import require_permission
from app.core.escopo import clausula_escopo, escopo_do_usuario
from app.schemas.video import (
    VideoDeviceResponse,
    VideoOccurrenceListResponse,
    VideoOccurrenceResponse,
    VideoOccurrenceSummary,
)

logger = get_logger(__name__)
router = APIRouter()

#: Tipos gerados por câmera embarcada. A lista fica aqui, e não no banco,
#: porque é classificação de produto: o mesmo `event_type` pode ser tratado
#: como vídeo numa operação e como alarme comum em outra.
TIPOS_DMS = (
    "distracao", "olhos_fechados", "bocejo", "fadiga", "celular",
    "fumando", "sem_rosto",
)
TIPOS_ADAS = (
    "colisao", "risco_colisao", "proximidade_dianteira",
    "curva_brusca", "freada_brusca", "aceleracao_brusca",
)
TIPOS_EQUIPAMENTO = (
    "calibracao_anormal", "desconexao_eletrica", "baixa_voltagem", "falha_gravacao",
)
TIPOS_VIDEO = TIPOS_DMS + TIPOS_ADAS + TIPOS_EQUIPAMENTO

#: Sem comunicação além disto, o equipamento é tratado como offline. Vinte
#: minutos porque o intervalo normal de posição é bem menor, e uma janela curta
#: geraria falso alarme a cada perda momentânea de sinal.
MINUTOS_OFFLINE = 20


@router.get("/devices", response_model=list[VideoDeviceResponse])
async def list_video_devices(
    only_offline: bool = Query(False),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    Equipamentos de vídeo instalados, com o estado de comunicação.

    O estado vem da última posição do veículo em `dev_status_30`, não do
    equipamento em si: o DVR não reporta separadamente, então a comunicação do
    rastreador é a melhor evidência disponível de que o carro está transmitindo.
    Isso é aproximação, e está declarado no campo `status_source`.
    """
    grupos, subgrupos = escopo_do_usuario(current_user)
    escopo_sql, escopo_params = clausula_escopo(grupos, subgrupos, alias="tu")

    limite = datetime.now() - timedelta(minutes=MINUTOS_OFFLINE)

    rows = (
        await db.execute(
            text(
                f"""
                SELECT
                    v.id, v.unit_id, v.device_id, v.association_date,
                    v.release_date, v.status,
                    tu.label  AS vehicle_label,
                    tu.label2 AS vehicle_prefix,
                    d.serial  AS device_serial,
                    d.imei    AS device_imei,
                    ultima.local_time AS last_communication,
                    ultima.latitude,
                    ultima.longitude,
                    ultima.speed,
                    ultima.ignition
                FROM mova.vcms_unit_device v
                JOIN mova.tracked_unit tu ON tu.id = v.unit_id
                LEFT JOIN mova.device d   ON d.id = v.device_id
                LEFT JOIN LATERAL (
                    SELECT local_time, latitude, longitude, speed, ignition
                    FROM mova.dev_status_30 s
                    WHERE s.unit_id = v.unit_id
                    ORDER BY s.local_time DESC
                    LIMIT 1
                ) ultima ON TRUE
                WHERE v.status = 1
                  AND v.release_date IS NULL{escopo_sql}
                ORDER BY tu.label2 NULLS LAST, tu.label
                """
            ),
            escopo_params,
        )
    ).mappings().all()

    saida = []
    for r in rows:
        online = bool(r["last_communication"] and r["last_communication"] >= limite)
        if only_offline and online:
            continue
        saida.append(
            VideoDeviceResponse(
                **r,
                online=online,
                status_source="ultima posicao do rastreador",
            )
        )

    return saida


@router.get("/occurrences", response_model=VideoOccurrenceListResponse)
async def list_video_occurrences(
    start_date: Optional[datetime] = Query(None, description="Padrão: 7 dias atrás"),
    end_date: Optional[datetime] = Query(None),
    vehicle_id: Optional[int] = Query(None),
    category: Optional[str] = Query(None, description="dms, adas ou equipamento"),
    only_pending: bool = Query(False),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    Ocorrências de vídeo, com a fila de tratativa.

    A categoria separa o que é comportamento do motorista (DMS), o que é risco
    de condução (ADAS) e o que é falha do próprio equipamento. A terceira
    importa: calibração anormal e baixa voltagem não são problema do motorista,
    e misturá-las na mesma lista faz o gestor cobrar a pessoa errada.
    """
    if not start_date:
        start_date = datetime.now() - timedelta(days=7)
    if not end_date:
        end_date = datetime.now()

    grupos_oc, subgrupos_oc = escopo_do_usuario(current_user)

    tipos = {
        "dms": TIPOS_DMS,
        "adas": TIPOS_ADAS,
        "equipamento": TIPOS_EQUIPAMENTO,
    }.get(category or "", TIPOS_VIDEO)

    where = [
        "e.timestamp >= :start",
        "e.timestamp <= :end",
        "e.event_type = ANY(:tipos)",
    ]
    params: dict = {
        "start": start_date, "end": end_date, "tipos": list(tipos),
        "limit": limit, "offset": offset,
    }

    if vehicle_id is not None:
        where.append("e.vehicle_id = :vehicle_id")
        params["vehicle_id"] = vehicle_id
    if only_pending:
        where.append("e.acknowledged IS NOT TRUE")

    escopo_sql_oc, escopo_params_oc = clausula_escopo(grupos_oc, subgrupos_oc, alias="tu")
    params.update(escopo_params_oc)

    clausula = " AND ".join(where) + escopo_sql_oc

    rows = (
        await db.execute(
            text(
                f"""
                SELECT
                    e.id, e.vehicle_id, e.event_type, e.severity, e.timestamp,
                    e.description, e.latitude, e.longitude, e.data,
                    e.acknowledged, e.acknowledged_by, e.acknowledged_at,
                    tu.label  AS vehicle_label,
                    tu.label2 AS vehicle_prefix,
                    COUNT(*) OVER () AS total_count
                FROM mova.fleet_events e
                JOIN mova.tracked_unit tu ON tu.id = e.vehicle_id
                WHERE {clausula}
                ORDER BY e.timestamp DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    ).mappings().all()

    resumo = (
        await db.execute(
            text(
                f"""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE e.acknowledged IS NOT TRUE) AS pending,
                    COUNT(*) FILTER (WHERE e.event_type = ANY(:dms))   AS dms,
                    COUNT(*) FILTER (WHERE e.event_type = ANY(:adas))  AS adas,
                    COUNT(*) FILTER (WHERE e.event_type = ANY(:equip)) AS equipment,
                    COUNT(DISTINCT e.vehicle_id) AS vehicles
                FROM mova.fleet_events e
                JOIN mova.tracked_unit tu ON tu.id = e.vehicle_id
                WHERE {clausula}
                """
            ),
            {
                **{k: v for k, v in params.items() if k not in ("limit", "offset")},
                "dms": list(TIPOS_DMS),
                "adas": list(TIPOS_ADAS),
                "equip": list(TIPOS_EQUIPAMENTO),
            },
        )
    ).mappings().first()

    def categoria(tipo: str | None) -> str:
        if tipo in TIPOS_DMS:
            return "dms"
        if tipo in TIPOS_ADAS:
            return "adas"
        if tipo in TIPOS_EQUIPAMENTO:
            return "equipamento"
        return "outro"

    return VideoOccurrenceListResponse(
        items=[
            VideoOccurrenceResponse(
                **{k: v for k, v in r.items() if k != "total_count"},
                category=categoria(r["event_type"]),
                # O clipe é referenciado na carga do evento pelo worker de
                # download; sem ele, não há mídia para exibir.
                has_clip=bool((r["data"] or {}).get("clip_url") or (r["data"] or {}).get("media_id")),
            )
            for r in rows
        ],
        total=rows[0]["total_count"] if rows else 0,
        summary=VideoOccurrenceSummary(**resumo),
        limit=limit,
        offset=offset,
    )
