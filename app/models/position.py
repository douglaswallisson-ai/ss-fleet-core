"""
Position model - Represents GPS position data.
Optimized for time-series queries with proper indexing.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, Float, DateTime, JSON, Index, Boolean

from app.core.database import Base


class Position(Base):
    """
    GPS position model - optimized for time-series data.

    Uses composite indexes for efficient time-series queries.

    Attributes:
        id: Primary key
        vehicle_id: Associated vehicle
        device_id: Device that reported position
        timestamp: Position timestamp (used for hypertable partitioning)
        latitude: GPS latitude
        longitude: GPS longitude
        altitude: Altitude in meters
        speed: Speed in km/h
        heading: Heading/direction in degrees (0-360)
        accuracy: GPS accuracy in meters
        satellites: Number of satellites
        odometer: Odometer reading in km
        additional_data: JSON for telemetry data (RPM, fuel, etc.)
        created_at: Record creation timestamp
    """

    __tablename__ = "fleet_positions"

    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(Integer, nullable=False, index=True)  # References mova.tracked_unit.id (no FK)

    # Timestamp - CRITICAL for TimescaleDB hypertable
    # This will be the time column for partitioning
    timestamp = Column(DateTime, nullable=False, index=True)

    # GPS coordinates
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    altitude = Column(Float, nullable=True)

    # Movement data
    speed = Column(Float, default=0.0, nullable=False)  # km/h
    heading = Column(Integer, nullable=True)  # 0-360 degrees

    # GPS quality
    accuracy = Column(Float, nullable=True)  # meters
    satellites = Column(Integer, nullable=True)

    # Vehicle data
    odometer = Column(Float, nullable=True)  # km
    ignition = Column(Boolean, nullable=True)

    # Additional telemetry (CAN bus data, etc.)
    # Example: {"rpm": 1500, "fuel_level": 75, "temperature": 90}
    additional_data = Column(JSON, default=dict, nullable=False)

    # Record metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships removed - mova.tracked_unit has no FK constraints
    # vehicle_id references mova.tracked_unit.id but without FK relationship

    # Composite index for efficient queries
    __table_args__ = (
        Index('ix_fleet_positions_vehicle_timestamp', 'vehicle_id', 'timestamp'),
    )

    def __repr__(self):
        return f"<Position(id={self.id}, vehicle_id={self.vehicle_id}, timestamp={self.timestamp})>"

    @property
    def coordinates(self) -> tuple:
        """Get coordinates as (lat, lon) tuple."""
        return (self.latitude, self.longitude)
