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
