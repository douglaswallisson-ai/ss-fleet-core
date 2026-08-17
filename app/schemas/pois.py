"""Schemas de pontos de interesse e cercas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class PoiResponse(BaseModel):
    id: Optional[int] = None
    name: str
    #: `poi` para ponto e `area` para cerca.
    type: str
    #: Quantas passagens no período observado.
    visits: int
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    #: Declarado porque é aproximação: a coordenada é média das passagens, não o
    #: cadastro. Muda quando a tabela de cadastro for mapeada.
    source: str


class PoiVisitResponse(BaseModel):
    trip_id: Optional[int] = None
    unit_id: Optional[int] = None
    vehicle_label: Optional[str] = None
    vehicle_prefix: Optional[str] = None
    driver_name: Optional[str] = None
    moment: Optional[datetime] = None
    #: entrada ou saida.
    direction: str
    #: Distância do ponto no momento do registro, em metros.
    distance_m: Optional[float] = None

    model_config = {"from_attributes": True}
