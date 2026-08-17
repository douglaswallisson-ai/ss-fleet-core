"""
TrackedUnitDevice model - Maps to mova.tracked_unit_device table.
Represents the association between vehicles and tracking devices.
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import Column, Integer, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.core.database import Base


class TrackedUnitDevice(Base):
    """
    TrackedUnitDevice model - Vehicle-Device association.

    Business Rules:
    1. One-to-One relationship when both vehicle and device are active (status=1)
    2. device_primary must be 1 when status=1, 0 otherwise
    3. association_date must be < release_date (when release_date is not NULL)
    4. Soft delete pattern: status=-1 for deleted associations
    5. When vehicle or device is deleted, cascade update to release association

    Fields:
        id: Primary key
        tracked_unit_id: Foreign key to tracked_unit (vehicle)
        device_id: Foreign key to device
        association_date: When device was installed on vehicle
        release_date: When device was removed (NULL = still installed)
        status: Association status (1=active, -1=deleted)
        user_id: User who created/modified the association
        device_primary: Primary device flag (1=primary, 0=secondary)
    """

    __tablename__ = "tracked_unit_device"
    __table_args__ = {'schema': 'mova'}

    # Primary key
    id = Column('id', Integer, primary_key=True, index=True)

    # Foreign keys
    tracked_unit_id = Column('tracked_unit_id', Integer, nullable=False, index=True)
    device_id = Column('device_id', Integer, nullable=False, index=True)

    # Association dates
    association_date = Column('association_date', DateTime, nullable=False)
    release_date = Column('release_date', DateTime, nullable=True)

    # Status and metadata
    status = Column('status', Integer, nullable=False, default=1)
    user_id = Column('user_id', Integer, nullable=False)
    device_primary = Column('device_primary', Integer, nullable=False, default=1)

    # Relationships (optional - legacy table may not have FKs)
    # vehicle = relationship("Vehicle", foreign_keys=[tracked_unit_id])
    # device = relationship("Device", foreign_keys=[device_id])

    @property
    def is_active(self) -> bool:
        """Check if association is active (status == 1)."""
        return self.status == 1

    @property
    def is_deleted(self) -> bool:
        """Check if association is soft-deleted (status == -1)."""
        return self.status == -1

    @property
    def is_currently_associated(self) -> bool:
        """Check if device is currently associated (release_date is NULL)."""
        return self.release_date is None and self.status == 1

    @property
    def is_primary(self) -> bool:
        """Check if this is the primary device association."""
        return self.device_primary == 1

    def duration_days(self) -> Optional[int]:
        """Calculate association duration in days."""
        if not self.release_date:
            # Still active - calculate from association_date to now
            delta = datetime.now() - self.association_date
            return delta.days

        # Released - calculate actual duration
        delta = self.release_date - self.association_date
        return delta.days

    def __repr__(self):
        return (
            f"<TrackedUnitDevice(id={self.id}, "
            f"vehicle_id={self.tracked_unit_id}, "
            f"device_id={self.device_id}, "
            f"status={self.status}, "
            f"primary={self.device_primary})>"
        )
