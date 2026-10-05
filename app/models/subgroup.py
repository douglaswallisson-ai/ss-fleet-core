"""SQLAlchemy model for Subgroup."""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, func
from app.core.database import Base


class Subgroup(Base):
    """
    Subgroup model.
    Represents a subdivision within a Group (e.g., departments, branches, teams).

    Business Rules:
    - Each subgroup belongs to one group
    - Used for granular access control (user_group_access)
    - Subgroup 0 typically represents shared/general resources
    - Can be suspended (billing suspension)
    - Tolerance fields for journey time validation
    """
    __tablename__ = "subgroup"
    __table_args__ = {'schema': 'mova'}

    # Primary Key
    id = Column(Integer, primary_key=True, index=True)

    # Subgroup Information
    name = Column(String, nullable=False, index=True)
    color = Column(String(7), default='#000000', nullable=True)  # Hex color for UI
    client_cod = Column(String, nullable=True, index=True)  # External ID

    # Group relationship
    group_id = Column(Integer, nullable=False, index=True)  # FK to mova.group

    # Company Info (optional - for billing/legal)
    company = Column(String, nullable=True)
    address = Column(String, nullable=True)
    cnpj = Column(String, nullable=True)

    # Journey Tolerance Configuration (minutes)
    tolerance_before_ini = Column(Integer, default=5, nullable=False)  # Before journey start
    tolerance_after_ini = Column(Integer, default=5, nullable=False)   # After journey start

    # Suspension (billing/access)
    suspended = Column(Boolean, default=False, nullable=False)
    suspended_date = Column(DateTime, nullable=True)

    # Integration
    int_cittati = Column(Boolean, nullable=True)  # Cittati integration flag

    # Audit Trail
    user_add = Column(Integer, nullable=True)
    date_add = Column(DateTime, server_default=func.now(), nullable=True)
    user_modif = Column(Integer, nullable=True)
    date_modif = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<Subgroup(id={self.id}, name='{self.name}', group_id={self.group_id}, suspended={self.suspended})>"
