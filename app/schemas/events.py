"""Schemas de eventos e alarmes."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class EventResponse(BaseModel):
    id: int
    vehicle_id: Optional[int] = None
    #: Rótulos do veículo vêm junto para a tela não precisar de outra consulta.
    vehicle_label: Optional[str] = None
    vehicle_prefix: Optional[str] = None
    event_type: Optional[str] = None
    severity: Optional[str] = None
    timestamp: Optional[datetime] = None
    description: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    #: Carga livre do evento, específica de cada tipo.
    data: Optional[dict[str, Any]] = None
    acknowledged: Optional[bool] = None
    acknowledged_by: Optional[int] = None
    acknowledged_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class EventSummary(BaseModel):
    """Contadores do mesmo intervalo consultado."""

    total: int
    critical: int
    warning: int
    info: int
    #: Aguardando tratativa. É o número que define se o time está em dia.
    pending: int
    vehicles: int


class EventListResponse(BaseModel):
    items: list[EventResponse]
    total: int
    summary: EventSummary
    limit: int
    offset: int


class EventAcknowledge(BaseModel):
    #: O que foi feito. Opcional, mas é o que dá sentido ao registro depois.
    note: Optional[str] = Field(None, max_length=1000)
