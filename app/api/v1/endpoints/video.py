"""
Video endpoints — equipamentos e ocorrências.

O `ss-fleet-core` só tinha o vínculo veículo↔equipamento
(`/video-device-associations`). Faltava o que interessa ao operador: quais
veículos têm câmera, qual o estado de comunicação de cada uma, e quais
ocorrências aguardam tratativa.

As ocorrências de vídeo vêm de `vcms.vcms_history`, onde o serviço da câmera
grava cada alarme (antes vinham de `fleet_events`, vazia em produção — achado
de 06/10/2026). Separar num recurso próprio é decisão de produto: quem cuida de
segurança embarcada trabalha com uma fila diferente de quem cuida de alarme
operacional, e misturar as duas faz as duas serem ignoradas.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.camera import JOIN_TIPO, NOME_SQL, categoria_sql, severidade_video, tipo_video
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
                    d.serial_number AS device_serial,
                    d.imei    AS device_imei,
                    ultima.local_time AS last_communication,
                    ultima.latitude,
                    ultima.longitude,
                    ultima.speed,
                    ultima.ignition
                FROM vcms.vcms_unit_device v
                JOIN mova.tracked_unit tu ON tu.id = v.unit_id
                LEFT JOIN mova.device d   ON d.id = v.device_id
                -- Estado atual do veículo (uma linha por unidade). Buscar a última
                -- posição em dev_status_30 varria todas as partições: 4 minutos.
                LEFT JOIN mova.dev_status ultima ON ultima.unit_id = v.unit_id
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
    importa: obstrução da câmera e imagem com exceção não são problema do
    motorista, e misturá-las na mesma lista faz o gestor cobrar a pessoa errada.

    Fonte: `vcms.vcms_history` (somente leitura). DMS/ADAS pelo `alarm_source`,
    nome e gravidade pelo catálogo `vcms_alarm_type` (ver app/core/camera.py).
    "Tratada" é a verificação feita na plataforma atual (`verified` > 0, com
    quem e quando). Ocorrência sem veículo — câmera já desassociada — fica de
    fora: sem veículo não há como aplicar o escopo do usuário.
    """
    if not start_date:
        start_date = datetime.now() - timedelta(days=7)
    if not end_date:
        end_date = datetime.now()

    grupos_oc, subgrupos_oc = escopo_do_usuario(current_user)
    escopo_sql_oc, escopo_params_oc = clausula_escopo(grupos_oc, subgrupos_oc, alias="tu")

    params: dict = {"start": start_date, "end": end_date, "limit": limit, "offset": offset, **escopo_params_oc}
    filtro_veiculo = ""
    if vehicle_id is not None:
        filtro_veiculo = " AND h.unit_id = :vehicle_id"
        params["vehicle_id"] = vehicle_id

    # Filtros sobre a classificação ficam fora da CTE: a categoria é calculada nela.
    fora = ["TRUE"]
    if category in ("dms", "adas", "equipamento"):
        fora.append("oc.category = :category")
        params["category"] = category
    if only_pending:
        fora.append("NOT oc.acknowledged")
    where = " AND ".join(fora)

    base = f"""
        WITH oc AS (
            SELECT
                h.id, h.unit_id AS vehicle_id, h.local_time AS timestamp,
                h.latitude::float AS latitude, h.longitude::float AS longitude,
                {NOME_SQL} AS nome,
                {categoria_sql()} AS category,
                h.alarm_type, h.alarm_source, h.alarm_level, h.device_model_id, h.device,
                h.speed, NULLIF(TRIM(h.driver_name), '') AS motorista, NULLIF(TRIM(h.address), '') AS endereco,
                COALESCE(h.verified, 0) AS verified, COALESCE(h.verified, 0) > 0 AS acknowledged,
                h.user_verified AS acknowledged_by, h.date_verified AS acknowledged_at,
                h.images, h.videos,
                tu.label AS vehicle_label, tu.label2 AS vehicle_prefix
            FROM vcms.vcms_history h
            {JOIN_TIPO}
            JOIN mova.tracked_unit tu ON tu.id = h.unit_id
            WHERE h.local_time >= :start AND h.local_time <= :end{filtro_veiculo}{escopo_sql_oc}
        )
    """

    rows = (
        await db.execute(
            text(
                f"""{base}
                SELECT oc.*, COUNT(*) OVER () AS total_count
                FROM oc
                WHERE {where}
                ORDER BY oc.timestamp DESC, oc.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    ).mappings().all()

    resumo = (
        await db.execute(
            text(
                f"""{base}
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE NOT oc.acknowledged)          AS pending,
                    COUNT(*) FILTER (WHERE oc.category = 'dms')          AS dms,
                    COUNT(*) FILTER (WHERE oc.category = 'adas')         AS adas,
                    COUNT(*) FILTER (WHERE oc.category = 'equipamento')  AS equipment,
                    COUNT(DISTINCT oc.vehicle_id)                         AS vehicles
                FROM oc
                WHERE {where}
                """
            ),
            {k: v for k, v in params.items() if k not in ("limit", "offset")},
        )
    ).mappings().first()

    return VideoOccurrenceListResponse(
        items=[
            VideoOccurrenceResponse(
                id=r["id"],
                vehicle_id=r["vehicle_id"],
                vehicle_label=r["vehicle_label"],
                vehicle_prefix=r["vehicle_prefix"],
                event_type=tipo_video(r["nome"]),
                category=r["category"],
                severity=severidade_video(r["nome"]),
                timestamp=r["timestamp"],
                description=r["nome"],
                latitude=r["latitude"],
                longitude=r["longitude"],
                data={
                    "alarm_type": r["alarm_type"],
                    "alarm_source": r["alarm_source"],
                    "alarm_level": r["alarm_level"],
                    "device_model_id": r["device_model_id"],
                    "device": r["device"],
                    "speed": r["speed"],
                    "driver_name": r["motorista"],
                    "address": r["endereco"],
                    # 0 = não verificada; os demais códigos vêm da plataforma atual.
                    "verified": r["verified"],
                    "images": list(r["images"] or []),
                    "videos": list(r["videos"] or []),
                },
                # Sem vídeo baixado não há o que revisar.
                has_clip=bool(r["videos"]),
                acknowledged=r["acknowledged"],
                acknowledged_by=r["acknowledged_by"],
                acknowledged_at=r["acknowledged_at"],
            )
            for r in rows
        ],
        total=rows[0]["total_count"] if rows else 0,
        summary=VideoOccurrenceSummary(**resumo),
        limit=limit,
        offset=offset,
    )
