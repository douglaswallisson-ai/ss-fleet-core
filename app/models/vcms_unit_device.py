"""
VcmsUnitDevice model - Video monitoring device-vehicle associations.

Maps to vcms.vcms_unit_device table.
Manages secondary associations for video monitoring devices.
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import Column, Integer, DateTime
from sqlalchemy.sql import func

from app.core.database import Base


class VcmsUnitDevice(Base):
    """
    Video monitoring device-vehicle association model.

    Business Rules:
    1. One-to-one relationship when both unit and device are active (status=1)
    2. Soft delete pattern: status=1 (active), status=-1 (deleted)
    3. Date validation: association_date < release_date
    4. Cascade delete: parent deletion triggers association release
    """

    __tablename__ = "vcms_unit_device"
    __table_args__ = {'schema': 'vcms'}

    # Primary key
    id = Column('id', Integer, primary_key=True, index=True)

    # Foreign keys
    unit_id = Column('unit_id', Integer, nullable=False, index=True)
    device_id = Column('device_id', Integer, nullable=False, index=True)

    # Association metadata
    association_date = Column('association_date', DateTime, nullable=False, default=func.now())
    release_date = Column('release_date', DateTime, nullable=True)

    # Status and audit
    status = Column('status', Integer, nullable=False, default=1)
    user_id = Column('user_id', Integer, nullable=False)

    # Virtual properties
    @property
    def is_active(self) -> bool:
        """Check if association is active."""
        return self.status == 1

    @property
    def is_deleted(self) -> bool:
        """Check if association is soft-deleted."""
        return self.status == -1

    @property
    def is_currently_associated(self) -> bool:
        """Check if currently associated (active and not released)."""
        return self.release_date is None and self.status == 1

    def duration_days(self) -> Optional[int]:
        """
        Calculate association duration in days.

        Returns:
            Days between association_date and release_date (or now if not released)
        """
        if not self.release_date:
            # Still associated - calculate until now
            delta = datetime.now() - self.association_date
            return delta.days

        # Released - calculate actual duration
        delta = self.release_date - self.association_date
        return delta.days

    def __repr__(self):
        return f"<VcmsUnitDevice(id={self.id}, unit_id={self.unit_id}, device_id={self.device_id}, status={self.status})>"
