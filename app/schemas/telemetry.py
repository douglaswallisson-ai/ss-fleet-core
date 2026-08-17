"""Pydantic schemas for Telemetry model."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class TelemetryBase(BaseModel):
    """Base Telemetry schema."""
    unit_id: int
    trip_id: int
    start_time: datetime
    end_time: Optional[datetime] = None
    unit_label: str
    device_id: int
    driver_id: Optional[int] = None
    driver_name: Optional[str] = None


class TelemetryResponse(BaseModel):
    """Schema for Telemetry response with most important trip metrics."""
    # Trip identification
    id: int
    trip_id: int
    trip_number: Optional[int] = None

    # Vehicle & Driver
    unit_id: int
    unit_label: str
    device_id: int
    driver_id: Optional[int] = None
    driver_name: Optional[str] = None

    # Time period
    start_time: str  # Formatted as 'YYYY-MM-DD HH24:MI:SS'
    end_time: Optional[str] = None  # Formatted as 'YYYY-MM-DD HH24:MI:SS'
    total_time: Optional[int] = None  # seconds
    time_moving: Optional[int] = None  # seconds
    time_stopped: Optional[int] = None  # seconds

    # Distance & Odometer
    start_odometer: Optional[int] = None
    end_odometer: Optional[int] = None
    distance_traveled: Optional[float] = None  # km

    # Location - Start
    start_lat: Optional[float] = None
    start_lon: Optional[float] = None
    start_poi_name: Optional[str] = None
    start_area_name: Optional[str] = None

    # Location - End
    end_lat: Optional[float] = None
    end_lon: Optional[float] = None
    end_poi_name: Optional[str] = None
    end_area_name: Optional[str] = None

    # Speed metrics
    max_speed: Optional[int] = None
    avg_speed: Optional[int] = None
    count_over_speed: Optional[int] = None
    time_over_speed: Optional[int] = None  # seconds

    # Fuel metrics
    fuel_used: Optional[float] = None  # liters
    efficiency_kml: Optional[float] = None  # km per liter

    # Driver behavior
    count_hard_acel: Optional[int] = None
    count_hard_brake: Optional[int] = None
    count_harsh_turn: Optional[int] = None

    # RPM metrics
    max_rpm_permitted: Optional[int] = None
    time_over_rpm: Optional[int] = None  # seconds

    # Engine
    time_stop_engine_on: Optional[int] = None  # seconds
    count_stop_engine_on: Optional[int] = None

    # --- Faixas de condução -------------------------------------------------
    # Estavam no banco desde sempre e o schema não as expunha. Sem elas não há
    # como avaliar condução por viagem, que é a base do programa de premiação.
    time_blue: Optional[int] = None
    count_blue: Optional[int] = None
    time_green: Optional[int] = None
    count_green: Optional[int] = None
    time_extra_eco: Optional[int] = None
    count_extra_eco: Optional[int] = None
    time_yellow: Optional[int] = None
    count_yellow: Optional[int] = None
    time_red: Optional[int] = None
    count_red: Optional[int] = None
    time_inercia: Optional[int] = None
    time_eco_roll: Optional[int] = None
    time_retarder: Optional[int] = None
    time_autopilot: Optional[int] = None
    time_banguela: Optional[int] = None
    count_banguela: Optional[int] = None
    time_low_speed: Optional[int] = None
    time_tolerancia: Optional[int] = None

    # Distância por modo de condução, não só tempo.
    distance_pulling: Optional[float] = None
    distance_simple_inertia: Optional[float] = None
    distance_retarder: Optional[float] = None
    distance_ecoroll: Optional[float] = None
    distance_autopilot: Optional[float] = None

    # --- Chuva --------------------------------------------------------------
    # O equipamento distingue pista molhada de seca. É o fator que mais
    # distorce comparação de consumo entre períodos, e nenhuma tela usava.
    time_raining: Optional[int] = None
    time_dry: Optional[int] = None

    # --- Velocidade por tipo de via e condição ------------------------------
    max_urban_permitted_speed: Optional[int] = None
    max_road_permitted_speed: Optional[int] = None
    max_road_rain_speed: Optional[int] = None
    urban_rain_speed: Optional[int] = None
    time_over_urban_speed: Optional[int] = None
    time_over_road_speed: Optional[int] = None
    count_over_urban_speed: Optional[int] = None
    count_over_road_speed: Optional[int] = None
    count_speed_violation_l2: Optional[int] = None
    count_speed_violation_l3: Optional[int] = None

    # --- Linha e jornada ----------------------------------------------------
    # Preenchidos quando o equipamento está configurado para transporte de
    # passageiros. `line_number` é inteiro; zero significa sem linha.
    line: Optional[str] = None
    line_number: Optional[int] = None
    trip_direction: Optional[int] = None
    journey_status: Optional[bool] = None
    journey_opening_date: Optional[datetime] = None

    # --- Ponto e cerca ------------------------------------------------------
    start_poi_id: Optional[int] = None
    start_poi_distance: Optional[float] = None
    start_area_id: Optional[int] = None
    end_poi_id: Optional[int] = None
    end_poi_distance: Optional[float] = None
    end_area_id: Optional[int] = None

    # --- Horímetro e combustível bruto --------------------------------------
    # O horímetro é o gatilho por horas da manutenção preventiva, que ficava
    # vazio no catálogo por falta deste campo.
    start_hourmeter: Optional[float] = None
    end_hourmeter: Optional[float] = None
    start_fuel: Optional[float] = None
    end_fuel: Optional[float] = None
    fuel_used_stopped: Optional[float] = None

    # --- Carga do motor, turbo e embreagem ----------------------------------
    time_engine_load_level1: Optional[int] = None
    time_engine_load_level2: Optional[int] = None
    time_engine_load_level3: Optional[int] = None
    time_over_turbo_pressure: Optional[int] = None
    time_under_turbo_pressure: Optional[int] = None
    count_clutch: Optional[int] = None
    count_cluth_excess: Optional[int] = None
    time_cluth_excess: Optional[int] = None
    count_stop_accel: Optional[int] = None
    time_stop_accel: Optional[int] = None
    time_stop_engine_on_productive: Optional[int] = None
    time_engine_off: Optional[int] = None
    reached_rpm: Optional[int] = None

    # --- Excesso de velocidade sob chuva ------------------------------------
    # Limite de pista molhada é menor que o de pista seca, e o equipamento
    # conta as duas violações separadamente. Somá-las esconderia justamente a
    # infração mais grave.
    time_over_urban_rain_speed: Optional[int] = None
    time_over_road_rain_speed: Optional[int] = None
    count_over_urban_rain_speed: Optional[int] = None
    count_over_road_rain_speed: Optional[int] = None
    reached_over_urban_speed: Optional[int] = None
    reached_over_road_speed: Optional[int] = None
    reached_over_urban_rain_speed: Optional[int] = None
    reached_over_road_rain_speed: Optional[int] = None

    # --- Estado da viagem ---------------------------------------------------
    trip_status: Optional[int] = None
    trip_opening_date: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TelemetryCompositeCursor(BaseModel):
    """Composite cursor for partition-optimized pagination (monthly partitions)."""
    start_time: str  # ISO format timestamp
    id: int

    def to_string(self) -> str:
        """Encode cursor as base64 string."""
        import base64
        import json
        cursor_dict = {"t": self.start_time, "i": self.id}
        return base64.urlsafe_b64encode(json.dumps(cursor_dict).encode()).decode()

    @classmethod
    def from_string(cls, cursor_str: str) -> "TelemetryCompositeCursor":
        """Decode cursor from base64 string."""
        import base64
        import json
        cursor_dict = json.loads(base64.urlsafe_b64decode(cursor_str.encode()).decode())
        return cls(start_time=cursor_dict["t"], id=cursor_dict["i"])


class TelemetryCursorResponse(BaseModel):
    """Cursor pagination response for telemetry data."""
    data: list[TelemetryResponse]
    next_cursor: Optional[str] = None  # Base64-encoded composite cursor
    has_more: bool
    total_returned: int
