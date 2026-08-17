"""
Device model - Maps to existing mova.device table (legacy).
Represents tracking devices in the fleet management system.
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import Column, Integer, String, DateTime, BigInteger
from app.core.database import Base


class Device(Base):
    """
    Device/Tracker model mapped to existing mova.device table (LEGACY).

    This model maps to the production mova.device table.

    Key characteristics:
    - NO subgroup_id column (only group_id for access control)
    - Soft delete pattern with status=-1
    - Audit fields: user_add, date_add, user_modif, date_modif, user_removed, date_removed
    - Unique constraint: identifier per (device_model_id, status != -1)
    - Auto-generated: internal_id = device_model_id + identifier
    """

    __tablename__ = "device"
    __table_args__ = {'schema': 'mova'}

    # Primary key
    id = Column('id', Integer, primary_key=True, index=True)

    # Required fields
    device_model_id = Column('device_model_id', Integer, nullable=False)
    identifier = Column('identifier', String, nullable=False)  # Unique per (device_model_id, status)

    # Access control fields (NO subgroup_id - only group_id)
    group_id = Column('group_id', Integer, nullable=True, index=True)
    account_id = Column('account_id', Integer, nullable=True)

    # Generated/derived fields
    internal_id = Column('internal_id', String, nullable=True)  # device_model_id + identifier
    asset = Column('asset', Integer, nullable=True)  # Reference to tracked_unit (vehicle)

    # Status and configuration
    status = Column('status', Integer, nullable=False, default=1)  # 1=active, 0=inactive, -1=removed

    # Technical fields
    operadora = Column('operadora', String, nullable=True)  # Network operator
    number = Column('number', String, nullable=True)  # Phone number
    imei = Column('imei', String, nullable=True)  # IMEI number
    iccid = Column('iccid', String, nullable=True)  # SIM card ICCID
    serial_number = Column('serial_number', String, nullable=True)  # Device serial number
    modem = Column('modem', String, nullable=True)  # Modem information
    device_type = Column('device_type', Integer, nullable=True)  # Device type classification

    # Additional references
    device_model_version_id = Column('device_model_version_id', Integer, nullable=True)
    current_product_id = Column('current_product_id', Integer, nullable=True)
    customer_id = Column('customer_id', BigInteger, nullable=True)
    manufacturing_date = Column('manufacturing_date', DateTime, nullable=True)

    # Audit fields (seguindo padrão do sistema)
    date_add = Column('date_add', DateTime, default=datetime.now, nullable=True)
    user_add = Column('user_add', Integer, nullable=True)
    date_modif = Column('date_modif', DateTime, nullable=True)
    user_modif = Column('user_modif', Integer, nullable=True)
    date_removed = Column('date_removed', DateTime, nullable=True)
    user_removed = Column('user_removed', Integer, nullable=True)

    @property
    def is_active(self) -> bool:
        """Check if device is active (status == 1)."""
        return self.status == 1

    @property
    def is_deleted(self) -> bool:
        """Check if device is soft-deleted (status == -1)."""
        return self.status == -1

    @property
    def full_identifier(self) -> str:
        """Return full identifier (internal_id or generated)."""
        return self.internal_id or f"{self.device_model_id}_{self.identifier}"

    def __repr__(self):
        return f"<Device(id={self.id}, identifier={self.identifier}, model={self.device_model_id}, status={self.status})>"
