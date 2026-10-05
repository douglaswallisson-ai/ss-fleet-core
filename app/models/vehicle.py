"""
Vehicle model - Maps to existing mova.tracked_unit table (legacy).
Represents tracked vehicles/assets in the fleet management system.
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Numeric

from app.core.database import Base


class Vehicle(Base):
    """
    Vehicle/Asset model mapped to existing mova.tracked_unit table.

    Legacy fields from mova.tracked_unit:
        id: Primary key
        label: Vehicle identification (plate/name)
        label2: Secondary label
        group_id: Group identifier (access control)
        subgroup_id: Subgroup identifier (access control)
        account_id: Account/Company ID
        unit_category_id: Category classification
        status: Vehicle status (1=active, 0=inactive, etc.)
        model: Vehicle model description
        ... other legacy fields preserved
    """

    __tablename__ = "tracked_unit"
    __table_args__ = {'schema': 'mova'}  # mova schema

    # Primary key
    id = Column('id', Integer, primary_key=True, index=True)

    # Access control fields
    group_id = Column('group_id', Integer, nullable=False, index=True)
    subgroup_id = Column('subgroup_id', Integer, nullable=True, index=True)  # NULL = shared resource
    account_id = Column('account_id', Integer, nullable=False)

    # Vehicle identification
    label = Column('label', String, nullable=False)  # Primary label (plate/name)
    label2 = Column('label2', String, nullable=True)  # Secondary label
    model = Column('model', String, nullable=True)  # Vehicle model

    # Classification
    unit_category_id = Column('unit_category_id', Integer, nullable=False)
    unit_type_id = Column('unit_type_id', Integer, nullable=True)
    vehicle_model_id = Column('vehicle_model_id', Integer, nullable=True)

    # Status and configuration
    status = Column('status', Integer, nullable=False)  # 1=active, 0=inactive, etc.
    timezone = Column('timezone', Integer, nullable=False)
    dst = Column('dst', Boolean, default=True, nullable=False)

    # Operational data
    initial_odometer = Column('initial_odometer', Integer, default=0, nullable=False)
    initial_horimeter = Column('initial_horimeter', Integer, default=0, nullable=True)
    max_speed = Column('max_speed', Integer, nullable=True)
    driver_id = Column('driver_id', Integer, default=0, nullable=True)

    # Additional info
    obs = Column('obs', Text, nullable=True)  # Observations/notes
    cost_km = Column('cost_km', Numeric, nullable=True)

    # Service/maintenance
    services_id = Column('services_id', Integer, default=0, nullable=False)
    os_num = Column('os_num', Integer, nullable=True)  # Service order number
    os_id = Column('os_id', Integer, nullable=True)

    # Audit fields
    date_add = Column('date_add', DateTime, default=datetime.now, nullable=True)
    user_add = Column('user_add', Integer, nullable=True)
    date_modif = Column('date_modif', DateTime, nullable=True)
    user_modif = Column('user_modif', Integer, nullable=True)
    date_removed = Column('date_removed', DateTime, nullable=True)
    user_removed = Column('user_removed', Integer, nullable=True)

    # Legacy/migration fields
    old_mova_id = Column('old_mova_id', Integer, nullable=True)

    # Virtual properties for API compatibility
    @property
    def plate(self) -> str:
        """Alias for label field (primary identification)."""
        return self.label or ""

    @property
    def is_active(self) -> bool:
        """Check if vehicle is active based on status field."""
        return self.status == 1

    @property
    def created_at(self) -> Optional[datetime]:
        """Alias for date_add."""
        return self.date_add

    @property
    def updated_at(self) -> Optional[datetime]:
        """Alias for date_modif."""
        return self.date_modif

    @property
    def description(self) -> Optional[str]:
        """Alias for obs field."""
        return self.obs

    # Relationships (disabled - legacy table doesn't have FKs)
    # devices = relationship("Device", back_populates="vehicle", cascade="all, delete-orphan")
    # positions = relationship("Position", back_populates="vehicle", cascade="all, delete-orphan")
    # events = relationship("Event", back_populates="vehicle", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Vehicle(id={self.id}, label={self.label}, model={self.model})>"
