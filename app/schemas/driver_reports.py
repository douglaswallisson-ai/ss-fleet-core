"""Pydantic schemas for Driver Performance Reports."""

from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel, Field


class DriverReportCompositeCursor(BaseModel):
    """Composite cursor for driver report pagination."""
    dt: str  # Date in YYYY-MM-DD format
    row_id: int

    def to_string(self) -> str:
        """Encode cursor as base64 string."""
        import base64
        import json
        cursor_dict = {"t": self.dt, "i": self.row_id}
        return base64.urlsafe_b64encode(json.dumps(cursor_dict).encode()).decode()

    @classmethod
    def from_string(cls, cursor_str: str) -> "DriverReportCompositeCursor":
        """Decode cursor from base64 string."""
        import base64
        import json

        # Fix padding if needed (base64 requires length to be multiple of 4)
        missing_padding = len(cursor_str) % 4
        if missing_padding:
            cursor_str += '=' * (4 - missing_padding)

        cursor_dict = json.loads(base64.urlsafe_b64decode(cursor_str.encode()).decode())
        return cls(dt=cursor_dict["t"], row_id=cursor_dict["i"])


class DriverKmFuelHoursResponse(BaseModel):
    """Schema for driver km/fuel/hours metrics response."""
    dt: date
    label: str
    unit_id: int
    group_id: int
    subgroup_id: int
    driver: Optional[str] = None
    driver_id: Optional[int] = None
    distance_traveled_hist: float = Field(..., description="Distance traveled in kilometers")
    used_fuel_hist: float = Field(..., description="Fuel consumed in liters")
    time_traveled_hist: float = Field(..., description="Time traveled in hours")
    distance_traveled_hist_filtrado: float = Field(..., description="Filtered distance (only when fuel > 0 and < 500L)")
    is_estimated: bool = Field(False, description="True if estimated data was used (when actual fuel = 0 or NULL)")

    model_config = {"from_attributes": True}


class DriverKmFuelHoursCursorResponse(BaseModel):
    """Cursor pagination response for driver performance metrics."""
    data: list[DriverKmFuelHoursResponse]
    next_cursor: Optional[str] = None
    has_more: bool
    total_returned: int


# ============================================================================
# Tempo de Faixa (RPM Band Time) Schemas
# ============================================================================

class RpmBandTimeResponse(BaseModel):
    """Schema for RPM band time metrics response."""
    day: date = Field(..., description="Date (YYYY-MM-DD)")
    unit_id: int = Field(..., description="Vehicle/unit ID")
    group_id: int = Field(..., description="Group ID")
    subgroup_id: int = Field(..., description="Subgroup ID")
    driver_id: Optional[int] = Field(None, description="Driver ID (can be NULL)")
    stop_engine_on: int = Field(..., description="Time stopped with engine on including productive time (seconds)")
    parado_acelerando: int = Field(0, description="time_stop_accel (seconds)")
    movimento_sem_tracao: int = Field(0, description="time_banguela (seconds)")
    blue: int = Field(..., description="time_blue: batendo transmissão (seconds)")
    green: int = Field(..., description="time_green only, without extra eco (seconds)")
    extra_economica: int = Field(0, description="time_extra_eco (seconds)")
    yellow: int = Field(..., description="Time in yellow RPM band (seconds)")
    red: int = Field(..., description="Time in red RPM band (seconds)")
    inercia: int = Field(..., description="Time in inertia/coasting (seconds)")
    tolerancia: int = Field(0, description="time_tolerancia (seconds)")
    total_time: int = Field(..., description="Sum of the 11 columns above (seconds)")

    model_config = {"from_attributes": True}


class RpmBandTimeCursorResponse(BaseModel):
    """Cursor pagination response for RPM band time metrics."""
    data: list[RpmBandTimeResponse]
    next_cursor: Optional[str] = None
    has_more: bool
    total_returned: int


# ============================================================================
# Mapa de Calor (Heatmap) Schemas
# ============================================================================

class HeatmapResponse(BaseModel):
    """Schema for heatmap event counts response."""
    data_hora: str = Field(..., description="Date and hour (YYYY-MM-DD HH:00)")
    unit_id: int = Field(..., description="Vehicle/unit ID")
    label: str = Field(..., description="Vehicle plate/label")
    label2: Optional[str] = Field(None, description="Secondary label")
    group_id: int = Field(..., description="Group ID")
    subgroup_id: int = Field(..., description="Subgroup ID")
    driver_id: Optional[int] = Field(None, description="Driver ID (can be NULL)")
    driver_name: Optional[str] = Field(None, description="Driver name")
    faixa_amarela: int = Field(..., description="Yellow band events count")
    faixa_vermelha: int = Field(..., description="Red band events count")
    batendo_transmissao: int = Field(..., description="Transmission hitting events count")
    parado_acelerando: int = Field(..., description="Stopped accelerating events count")
    excesso_velocidade: int = Field(..., description="Speeding events count")

    model_config = {"from_attributes": True}


class HeatmapCursorResponse(BaseModel):
    """Cursor pagination response for heatmap metrics."""
    data: list[HeatmapResponse]
    next_cursor: Optional[str] = None
    has_more: bool
    total_returned: int


# ============================================================================
# Metas e Pesos (Weight Range / Goals) Schemas
# ============================================================================

class WeightRangeCursor(BaseModel):
    """Cursor for weight range pagination."""
    row_id: int

    def to_string(self) -> str:
        """Encode cursor as base64 string."""
        import base64
        import json
        cursor_dict = {"i": self.row_id}
        return base64.urlsafe_b64encode(json.dumps(cursor_dict).encode()).decode()

    @classmethod
    def from_string(cls, cursor_str: str) -> "WeightRangeCursor":
        """Decode cursor from base64 string."""
        import base64
        import json

        # Fix padding if needed (base64 requires length to be multiple of 4)
        missing_padding = len(cursor_str) % 4
        if missing_padding:
            cursor_str += '=' * (4 - missing_padding)

        cursor_dict = json.loads(base64.urlsafe_b64decode(cursor_str.encode()).decode())
        return cls(row_id=cursor_dict["i"])


class WeightRangeResponse(BaseModel):
    """Schema for weight range (goals and weights) response."""
    range_id: int = Field(..., description="Range identifier (1-6 for RPM bands, etc.)")
    group_id: int = Field(..., description="Group ID")
    subgroup_id: int = Field(..., description="Subgroup ID")
    weight: float = Field(..., description="Average weight for this range")
    goal: float = Field(..., description="Average goal/target for this range")

    model_config = {"from_attributes": True}


class WeightRangeCursorResponse(BaseModel):
    """Cursor pagination response for weight range metrics."""
    data: list[WeightRangeResponse]
    next_cursor: Optional[str] = None
    has_more: bool
    total_returned: int
