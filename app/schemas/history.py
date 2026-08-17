"""Pydantic schemas for History model."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class HistoryBase(BaseModel):
    """Base History schema."""
    unit_id: int
    local_time: datetime
    time_write: Optional[datetime] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    ignition: Optional[bool] = None
    speed: Optional[float] = None
    odom: Optional[int] = None
    odom_total: Optional[int] = None
    rpm: Optional[int] = None
    address: Optional[str] = None


class HistoryResponse(BaseModel):
    """Schema for History response with vehicle info (simple format)."""
    id: int
    label: str
    label2: Optional[str] = None
    obs: Optional[str] = None
    local_time: str  # Formatted as 'YYYY-MM-DD HH24:MI:SS'
    time_write: Optional[str] = None  # Formatted as 'YYYY-MM-DD HH24:MI:SS'
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    ign: Optional[bool] = None
    speed: Optional[float] = None
    odom: Optional[int] = None
    rpm: Optional[int] = None
    address: Optional[str] = None

    model_config = {"from_attributes": True}


# ============================================================================
# Detailed History Response (Full CAN Bus Data)
# ============================================================================

class SubgroupInfo(BaseModel):
    """Subgroup information."""
    id: Optional[int] = None
    name: Optional[str] = None


class GroupInfo(BaseModel):
    """Group information with nested subgroup."""
    id: Optional[int] = None
    name: Optional[str] = None
    subgroup: Optional[SubgroupInfo] = None


class UnitInfo(BaseModel):
    """Vehicle/Unit information."""
    id: int
    label: str
    label2: Optional[str] = None
    obs: Optional[str] = None
    group: Optional[GroupInfo] = None


class DriverInfo(BaseModel):
    """Driver information."""
    id: Optional[int] = None
    name: Optional[str] = None
    login: Optional[str] = None
    group: Optional[GroupInfo] = None


class PoiInfo(BaseModel):
    """Point of Interest information."""
    id: Optional[int] = None
    name: Optional[str] = None
    distance: Optional[float] = None


class CercaInfo(BaseModel):
    """Geofence/Area information."""
    id: Optional[int] = None
    name: Optional[str] = None


class DeviceInfo(BaseModel):
    """Device information."""
    id: Optional[int] = None
    identifier: Optional[str] = None
    model: Optional[str] = None


class EventInfo(BaseModel):
    """Tracker event information."""
    id: Optional[int] = None
    name: Optional[str] = None


class ElectricalData(BaseModel):
    """Electrical system data."""
    voltage: Optional[float] = None
    battery: Optional[float] = None
    can_control_module_voltage: Optional[float] = None


class LocationData(BaseModel):
    """Detailed location data."""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[int] = None
    direction: Optional[int] = None
    gps: Optional[bool] = None


class EngineData(BaseModel):
    """Engine CAN Bus data."""
    rpm: Optional[int] = None
    can_rpm: Optional[int] = None
    can_accel_pedal_percent: Optional[int] = None
    can_engine_torque_percent: Optional[int] = None
    can_retarder_torque: Optional[int] = None
    can_engine_oil_pressure: Optional[int] = None
    can_turbo_charger_pressure: Optional[int] = None


class SpeedDistanceData(BaseModel):
    """Speed and distance data."""
    speed: Optional[int] = None
    can_speed: Optional[int] = None
    odom: Optional[int] = None
    can_total_odometer: Optional[int] = None


class FuelData(BaseModel):
    """Fuel system data."""
    can_fuel_level_percent: Optional[int] = None
    can_def_level_percent: Optional[int] = None
    can_total_used_fuel: Optional[int] = None


class TemperatureData(BaseModel):
    """Temperature data."""
    can_engine_coolant_temp: Optional[int] = None
    can_engine_coolant_level: Optional[int] = None
    external_sensor_temperature: Optional[float] = None


class TransmissionData(BaseModel):
    """Transmission data."""
    can_gear: Optional[int] = None
    faixa: Optional[int] = None


class PneumaticData(BaseModel):
    """Pneumatic system data."""
    can_pneumatic_system1_pressure: Optional[int] = None
    can_pneumatic_system2_pressure: Optional[int] = None


class HourmeterData(BaseModel):
    """Hourmeter data."""
    hourmeter: Optional[int] = None
    can_engine_hourmeter: Optional[int] = None


class StatusFlagsData(BaseModel):
    """Status flags and inputs."""
    ignition: Optional[bool] = None
    in5: Optional[int] = None
    in6: Optional[int] = None
    in7: Optional[int] = None
    in8: Optional[int] = None
    can_cruise_control_state: Optional[int] = None
    can_break_pedal_state: Optional[int] = None
    can_parking_brake_state: Optional[int] = None
    can_retarder_in_use: Optional[int] = None
    connection: Optional[int] = None


class HistoryDetailedResponse(BaseModel):
    """Detailed History response with full CAN Bus data."""
    id: int = Field(..., description="Record ID (BigInt)")
    unit: UnitInfo = Field(..., description="Vehicle information")
    driver: Optional[DriverInfo] = Field(None, description="Driver information (null if no driver)")
    local_time: str = Field(..., description="Local timestamp (YYYY-MM-DD HH:MI:SS)")
    time_write: Optional[str] = Field(None, description="Server write timestamp")
    latitude: Optional[float] = Field(None, description="GPS latitude")
    longitude: Optional[float] = Field(None, description="GPS longitude")
    ign: Optional[bool] = Field(None, description="Ignition status")
    speed: Optional[int] = Field(None, description="Speed (km/h)")
    odom: Optional[int] = Field(None, description="Odometer (meters)")
    rpm: Optional[int] = Field(None, description="Engine RPM")
    address: Optional[str] = Field(None, description="Geocoded address")
    poi: Optional[PoiInfo] = Field(None, description="Point of Interest")
    cerca: Optional[CercaInfo] = Field(None, description="Geofence/Area")
    device: Optional[DeviceInfo] = Field(None, description="Tracking device")
    event: Optional[EventInfo] = Field(None, description="Tracker event")
    electrical_data: Optional[ElectricalData] = Field(None, description="Electrical system")
    location: Optional[LocationData] = Field(None, description="Detailed location")
    engine: Optional[EngineData] = Field(None, description="Engine CAN data")
    speed_distance: Optional[SpeedDistanceData] = Field(None, description="Speed/Distance CAN data")
    fuel: Optional[FuelData] = Field(None, description="Fuel system CAN data")
    temperature: Optional[TemperatureData] = Field(None, description="Temperature CAN data")
    transmission: Optional[TransmissionData] = Field(None, description="Transmission CAN data")
    pneumatic: Optional[PneumaticData] = Field(None, description="Pneumatic system CAN data")
    hourmeter: Optional[HourmeterData] = Field(None, description="Hourmeter data")
    status_flags: Optional[StatusFlagsData] = Field(None, description="Status flags and inputs")

    model_config = {"from_attributes": True}


class HistoryDetailedCursorResponse(BaseModel):
    """Cursor pagination response for detailed history."""
    data: list[HistoryDetailedResponse]
    next_cursor: Optional[str] = None
    has_more: bool
    total_returned: int


class CompositeCursor(BaseModel):
    """Composite cursor for partition-optimized pagination."""
    local_time: str  # ISO format timestamp
    id: int

    def to_string(self) -> str:
        """Encode cursor as base64 string."""
        import base64
        import json
        cursor_dict = {"t": self.local_time, "i": self.id}
        return base64.urlsafe_b64encode(json.dumps(cursor_dict).encode()).decode()

    @classmethod
    def from_string(cls, cursor_str: str) -> "CompositeCursor":
        """Decode cursor from base64 string."""
        import base64
        import json
        cursor_dict = json.loads(base64.urlsafe_b64decode(cursor_str.encode()).decode())
        return cls(local_time=cursor_dict["t"], id=cursor_dict["i"])


class HistoryCursorResponse(BaseModel):
    """Cursor pagination response with partition-optimized cursor."""
    data: list[HistoryResponse]
    next_cursor: Optional[str] = None  # Base64-encoded composite cursor
    has_more: bool
    total_returned: int


