"""Schemas dos eventos de percurso."""

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel

#: Tipos derivados das posições. Não há tipo para cerca ou excesso de
#: velocidade porque esses vêm de outros serviços, não da posição bruta.
TrackingEventType = Literal[
    "ignicao_ligada",
    "ignicao_desligada",
    "parada",
    "ligado_parado",
    "retomada",
]


class TrackingEvent(BaseModel):
    id: int
    unit_id: int
    type: TrackingEventType
    timestamp: datetime
    latitude: float
    longitude: float
    speed: Optional[int] = None
    odometer: Optional[int] = None
    address: Optional[str] = None
    #: Duração do estado iniciado neste evento, em minutos.
    duration_min: Optional[int] = None

    model_config = {"from_attributes": True}


class TrackingSummary(BaseModel):
    first_ignition: Optional[datetime] = None
    last_ignition: Optional[datetime] = None
    minutes_on: int
    minutes_moving: int
    #: Motor ligado com o veículo parado — onde o combustível some sem rodar.
    minutes_idle: int
    stops: int
    distance_km: float
    max_speed: int
    #: Quantas posições foram analisadas para derivar os eventos. Serve para o
    #: usuário julgar a confiança: poucas posições significam falha de
    #: comunicação, não veículo parado.
    positions_analyzed: int


class TrackingResponse(BaseModel):
    unit_id: int
    operation_date: date
    summary: TrackingSummary
    events: list[TrackingEvent]
