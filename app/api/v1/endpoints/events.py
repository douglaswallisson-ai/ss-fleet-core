"""
Events endpoints — alarmes e ocorrências.

A tabela `fleet_events` existe no modelo e nunca teve rota. O
`ss-worker-alarm-analyze` processa e grava; ninguém lê pela API.

O que diferencia este recurso de um relatório: evento tem **estado**. Ele é
reconhecido ou não, por alguém, em algum momento. Sem esse ciclo, alarme vira
lista que ninguém trata — e uma lista que ninguém trata deixa de ser lida.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_db_read
from app.core.logging import get_logger
from app.middleware.auth import require_permission
from app.models.user import User
from app.core.escopo import clausula_escopo, escopo_do_usuario
from app.schemas.events import (
    EventAcknowledge,
    EventListResponse,
    EventResponse,
    EventSummary,
)

logger = get_logger(__name__)
router = APIRouter()


@router.get("/", response_model=EventListResponse)
async def list_events(
    start_date: Optional[datetime] = Query(None, description="Padrão: 24 h atrás"),
    end_date: Optional[datetime] = Query(None),
    vehicle_id: Optional[int] = Query(None),
    severity: Optional[str] = Query(None, description="info, warning, critical"),
    event_type: Optional[str] = Query(None),
    only_pending: bool = Query(False, description="Somente não reconhecidos"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    Lista eventos com o resumo por severidade junto.

    O resumo vem na mesma resposta de propósito: a tela precisa dos contadores
    para os cartões do topo, e uma segunda chamada só para contar dobraria a
    carga de uma consulta que já varre o mesmo intervalo.
    """
    # Sem intervalo, as últimas 24 h. Abrir a tela e ver a tabela inteira seria
    # lento e inútil — evento antigo se consulta com filtro, não por acidente.
    if not start_date:
        start_date = datetime.now() - timedelta(hours=24)
    if not end_date:
        end_date = datetime.now()

    where = ["e.timestamp >= :start", "e.timestamp <= :end"]
    params: dict = {"start": start_date, "end": end_date, "limit": limit, "offset": offset}

    if vehicle_id is not None:
        where.append("e.vehicle_id = :vehicle_id")
        params["vehicle_id"] = vehicle_id
    if severity:
        where.append("e.severity = :severity")
        params["severity"] = severity
    if event_type:
        where.append("e.event_type = :event_type")
        params["event_type"] = event_type
    if only_pending:
        where.append("e.acknowledged IS NOT TRUE")

    # Escopo pelo veículo do evento. Sem isso, qualquer usuário autenticado
    # leria os alarmes de todos os clientes.
    grupos, subgrupos = escopo_do_usuario(current_user)
    escopo_sql, escopo_params = clausula_escopo(grupos, subgrupos, alias="tu")
    params.update(escopo_params)

    clausula = " AND ".join(where) + escopo_sql

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
                    COUNT(*) FILTER (WHERE e.severity = 'critical')      AS critical,
                    COUNT(*) FILTER (WHERE e.severity = 'warning')       AS warning,
                    COUNT(*) FILTER (WHERE e.severity = 'info')          AS info,
                    COUNT(*) FILTER (WHERE e.acknowledged IS NOT TRUE)   AS pending,
                    COUNT(DISTINCT e.vehicle_id)                          AS vehicles
                FROM mova.fleet_events e
                JOIN mova.tracked_unit tu ON tu.id = e.vehicle_id
                WHERE {clausula}
                """
            ),
            {k: v for k, v in params.items() if k not in ("limit", "offset")},
        )
    ).mappings().first()

    return EventListResponse(
        items=[EventResponse(**{k: v for k, v in r.items() if k != "total_count"}) for r in rows],
        total=rows[0]["total_count"] if rows else 0,
        summary=EventSummary(**resumo),
        limit=limit,
        offset=offset,
    )


@router.post("/{event_id}/acknowledge", response_model=EventResponse)
async def acknowledge_event(
    event_id: int,
    payload: EventAcknowledge,
    db: AsyncSession = Depends(get_db),
    # Reconhecer evento altera estado. Usa a permissão de relatório em vez de
    # criar um par novo: 'reports','update' não existe no catálogo de
    # permissões, e exigir uma permissão inexistente barraria todo mundo com
    # 403 — que foi exatamente o erro visto em produção com /groups.
    user: User = Depends(require_permission("reports", "read")),
):
    """
    Marca o evento como tratado.

    Registra quem tratou e quando — sem isso não há como responder "por que
    ninguém agiu neste alarme", que é a pergunta que aparece depois do
    acidente. A observação é opcional mas fica no campo `data`, preservando o
    que já estava lá.
    """
    atual = (
        await db.execute(
            text("SELECT id, acknowledged, data FROM mova.fleet_events WHERE id = :id"),
            {"id": event_id},
        )
    ).mappings().first()

    if not atual:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evento não encontrado")
    if atual["acknowledged"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Este evento já foi tratado")

    row = (
        await db.execute(
            text(
                """
                UPDATE mova.fleet_events
                   SET acknowledged = TRUE,
                       acknowledged_by = :user_id,
                       acknowledged_at = NOW(),
                       data = COALESCE(data, '{}'::jsonb) ||
                              jsonb_build_object('tratativa', :note)
                 WHERE id = :id
             RETURNING id, vehicle_id, event_type, severity, timestamp,
                       description, latitude, longitude, data,
                       acknowledged, acknowledged_by, acknowledged_at
                """
            ),
            {"id": event_id, "user_id": user.id, "note": payload.note or ""},
        )
    ).mappings().first()

    await db.commit()

    logger.info("event_acknowledged", event_id=event_id, user_id=user.id)
    return EventResponse(**row)
