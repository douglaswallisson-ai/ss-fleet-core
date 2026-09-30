"""Schemas das posições atuais da frota."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class PositionResponse(BaseModel):
    """Última posição conhecida de um veículo."""

    unit_id: int
    placa: Optional[str] = None
    prefixo: Optional[str] = None
    latitude: float
    longitude: float
    #: Velocidade instantânea da leitura, em km/h.
    speed: Optional[float] = None
    ignition: Optional[bool] = None
    address: Optional[str] = None
    #: Quando a leitura chegou. É o que distingue "parado" de "sem sinal".
    local_time: Optional[datetime] = None
    #: Odômetro em metros.
    odom: Optional[int] = None
    group_id: Optional[int] = None
    subgroup_id: Optional[int] = None
    #: Última leitura mais antiga que o limite de comunicação.
    sem_sinal: bool = False

    model_config = {"from_attributes": True}


class PositionsResponse(BaseModel):
    items: list[PositionResponse]
    total: int
    #: Minutos sem leitura a partir dos quais o veículo é dado como sem sinal.
    minutos_sem_sinal: int
    #: Janela consultada, em minutos.
    janela_minutos: int
