"""
Event model - Represents alerts and events from devices.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, Float, DateTime, ForeignKey, JSON, Text, Enum as SQLEnum, Boolean
import enum

from app.core.database import Base


class EventType(str, enum.Enum):
    """Event type enumeration."""
    IGNITION_ON = "ignition_on"
    IGNITION_OFF = "ignition_off"
    SPEED_EXCESS = "speed_excess"
    RPM_EXCESS = "rpm_excess"
    HARSH_BRAKE = "harsh_brake"
    HARSH_ACCEL = "harsh_accel"
    HARSH_CURVE = "harsh_curve"
    IDLE_ENGINE = "idle_engine"
    TEMPERATURE_HIGH = "temperature_high"
    GEOFENCE_ENTER = "geofence_enter"
    GEOFENCE_EXIT = "geofence_exit"
    DRIVER_LOGIN = "driver_login"
    DRIVER_LOGOUT = "driver_logout"
    DEVICE_OFFLINE = "device_offline"
    DEVICE_ONLINE = "device_online"
    CUSTOM = "custom"


class EventSeverity(str, enum.Enum):
    """Event severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Event(Base):
    """
    Event/Alert model for tracking vehicle and device events.

    Attributes:
        id: Primary key
        vehicle_id: Associated vehicle
        event_type: Type of event
        severity: Event severity level
        timestamp: Event timestamp
        latitude: Event location latitude
        longitude: Event location longitude
        description: Event description
        data: Additional event data JSON
        acknowledged: Whether event was acknowledged
        acknowledged_by: User who acknowledged
        acknowledged_at: Acknowledgement timestamp
        created_at: Record creation timestamp
    """

    __tablename__ = "fleet_events"

    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(Integer, nullable=False, index=True)  # References mova.tracked_unit.id (no FK)

    # Event classification
    event_type = Column(SQLEnum(EventType), nullable=False, index=True)
    severity = Column(SQLEnum(EventSeverity), default=EventSeverity.INFO, nullable=False)

    # Event details
    timestamp = Column(DateTime, nullable=False, index=True)
    description = Column(Text, nullable=True)

    # Location (optional)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    # Additional data
    data = Column(JSON, default=dict, nullable=False)

    # Acknowledgement
    acknowledged = Column(Boolean, default=False, nullable=False)
    acknowledged_by = Column(Integer, ForeignKey("mova.users.id"), nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships removed - mova.tracked_unit has no FK constraints
    # vehicle_id references mova.tracked_unit.id but without FK relationship

    def __repr__(self):
        return f"<Event(id={self.id}, type={self.event_type}, vehicle_id={self.vehicle_id})>"

    def acknowledge(self, user_id: int):
        """Mark event as acknowledged."""
        self.acknowledged = True
        self.acknowledged_by = user_id
        self.acknowledged_at = datetime.utcnow()
