"""
History model - Maps to mova.dev_status_30 table (legacy).
Stores device status and telemetry data with 30-day retention.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, BigInteger
from app.core.database import Base


class History(Base):
    """
    History model mapped to mova.dev_status_30 table.

    This table stores position and telemetry data from tracking devices
    with a 30-day retention policy (data older than 30 days is archived).

    Fields:
        id: Primary key
        unit_id: References mova.tracked_unit.id
        local_time: Local timestamp (device timezone)
        time_write: Server write timestamp
        latitude: GPS latitude
        longitude: GPS longitude
        ignition: Ignition status
        speed: Speed in km/h
        odom: Odometer reading
        odom_total: Total odometer reading
        rpm: Engine RPM
        address: Reverse geocoded address
    """

    __tablename__ = "dev_status_30"
    __table_args__ = {'schema': 'mova'}

    # Primary key
    id = Column('id', BigInteger, primary_key=True, index=True)

    # Foreign key to tracked_unit (no FK constraint in legacy DB)
    unit_id = Column('unit_id', Integer, nullable=False, index=True)

    # Timestamps
    local_time = Column('local_time', DateTime, nullable=False, index=True)
    time_write = Column('time_write', DateTime, nullable=True)

    # GPS data
    latitude = Column('latitude', Float, nullable=True)
    longitude = Column('longitude', Float, nullable=True)

    # Vehicle status
    ignition = Column('ignition', Boolean, nullable=True)
    speed = Column('speed', Float, nullable=True)

    # Odometer
    odom = Column('odom', Integer, nullable=True)
    odom_total = Column('odom_total', Integer, nullable=True)

    # Engine data
    rpm = Column('rpm', Integer, nullable=True)

    # Geocoding
    address = Column('address', String, nullable=True)

    def __repr__(self):
        return f"<History(id={self.id}, unit_id={self.unit_id}, local_time={self.local_time})>"
