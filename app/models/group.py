"""SQLAlchemy model for Group (client)."""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, func
from app.core.database import Base


class Group(Base):
    """
    Group (formerly 'client') model.
    Represents a company/organization that owns vehicles and devices.

    Business Rules:
    - Groups contain multiple subgroups
    - Each group has account_id (billing/tenant)
    - Soft delete pattern: status field (not in DB schema, managed by application)
    - Access control: Users have access to specific groups via user_group_access
    """
    __tablename__ = "group"
    __table_args__ = {'schema': 'mova'}

    # Primary Key
    id = Column(Integer, primary_key=True, index=True)

    # Group Information
    name = Column(String, nullable=False, index=True)
    corporate_name = Column(String, nullable=True)
    cnpj = Column(String, nullable=True)
    client_cod = Column(String, nullable=True, unique=True, index=True)  # External ID

    # Account (tenant)
    account_id = Column(Integer, ForeignKey('mova.account.id'), nullable=False, index=True)

    # Billing
    cli_fat = Column(Integer, default=0, nullable=False)  # Billing ID
    pro_rata = Column(Boolean, default=False, nullable=False)  # Pro-rata billing

    # Contact
    address = Column(String, nullable=True)
    contact = Column(String, nullable=True)

    # Configuration
    general = Column(Boolean, default=False, nullable=True)  # General/shared group
    max_speed = Column(Integer, default=90, nullable=True)  # Default max speed (km/h)
    vcms_day_off = Column(Integer, default=3, nullable=False)  # VCMS days off

    # Audit Trail
    user_add = Column(Integer, nullable=True)
    date_add = Column(DateTime, server_default=func.now(), nullable=True)
    user_modif = Column(Integer, nullable=True)
    date_modif = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<Group(id={self.id}, name='{self.name}', account_id={self.account_id})>"
