"""Schemas dos indicadores consolidados."""

from datetime import date
from typing import Optional

from pydantic import BaseModel


class IndicatorPeriod(BaseModel):
    """Consolidado de um intervalo."""

    trips: int
    active_vehicles: int
    active_drivers: int
    distance_km: float
    fuel_liters: float
    #: Quilômetros por litro.
    kml: float
    #: Quilômetros médios entre falhas críticas.
    mkbf: float
    failures: int
    total_hours: float
    moving_hours: float
    #: Motor ligado com veículo parado — combustível consumido sem rodar.
    idle_hours: float
    idle_pct: float
    #: Tempo na faixa econômica sobre o tempo em movimento.
    green_band_pct: float
    #: Fração do tempo sob chuva. Distorce comparação de consumo entre períodos.
    rain_pct: float
    hard_brakes: int
    hard_accelerations: int
    speed_violations: int
    #: Eventos por 100 km. Sem normalizar, quem roda mais sempre parece pior.
    events_per_100km: float


class IndicatorsResponse(BaseModel):
    period_start: date
    period_end: date
    current: IndicatorPeriod
    #: Mesma duração do período atual, não o mês calendário anterior.
    previous: Optional[IndicatorPeriod] = None
    #: Indicadores que o banco não permite calcular hoje.
    unavailable: list[str]
    unavailable_reason: str
