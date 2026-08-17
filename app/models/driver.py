"""
Driver model - Driver/conductor management.

Maps to mova.driver table.
Manages driver information including licenses, documents, and credentials.
"""

from datetime import datetime, date
from typing import Optional
from sqlalchemy import Column, Integer, String, DateTime, Date, Text
from sqlalchemy.sql import func

from app.core.database import Base


class Driver(Base):
    """
    Driver model.

    Business Rules:
    1. Soft delete pattern: status=1 (active), status=-1 (deleted)
    2. Access control through group_id and subgroup_id
    3. Unique fields per group: login, cpf, email, matricula
    4. Password hashing: password (plain), password_apps (SHA1)
    5. CNH validation tracking
    """

    __tablename__ = "driver"
    __table_args__ = {'schema': 'mova'}

    # Primary key
    id = Column('id', Integer, primary_key=True, index=True)

    # Basic information
    name = Column('name', String, nullable=True)
    account_id = Column('account_id', Integer, nullable=True)
    email = Column('email', String, nullable=True)
    phone = Column('phone', String, nullable=True)
    cpf = Column('cpf', String(11), nullable=True)
    matricula = Column('matricula', String, nullable=True)

    # Authentication
    auth = Column('auth', Integer, nullable=False)
    login = Column('login', String, nullable=True)
    password = Column('password', String, nullable=True)  # Plain text for equipment verification
    password_apps = Column('password_apps', String, nullable=True)  # SHA1 for app access

    # License information
    cnh = Column('cnh', String, nullable=True)
    cnh_category = Column('cnh_category', String, nullable=True)
    cnh_validate = Column('cnh_validate', Date, nullable=True)
    passport = Column('passport', String, nullable=True)

    # Employment information
    area = Column('area', String, nullable=True)
    empresa = Column('empresa', String, nullable=True)
    gestor = Column('gestor', String, nullable=True)
    gestor_tel = Column('gestor_tel', String, nullable=True)
    local = Column('local', String, nullable=True)
    admission = Column('admission', Date, nullable=True)

    # Validation dates
    rac_validate = Column('rac_validate', Date, nullable=True)
    aso_validate = Column('aso_validate', Date, nullable=True)

    # Organization
    group_id = Column('group_id', Integer, nullable=True, index=True)
    subgroup_id = Column('subgroup_id', Integer, nullable=True, index=True)
    driver_function_id = Column('driver_function_id', Integer, nullable=True, default=19)

    # Status and audit
    status = Column('status', Integer, nullable=False, default=1)
    user_add = Column('user_add', Integer, nullable=True)
    date_add = Column('date_add', DateTime, nullable=True, default=func.now())
    user_modif = Column('user_modif', Integer, nullable=True)
    date_modif = Column('date_modif', DateTime, nullable=True)

    # Additional information
    obs_driver = Column('obs_driver', Text, nullable=True)
    integration_id = Column('integration_id', String, nullable=True)

    # Virtual properties
    @property
    def is_active(self) -> bool:
        """Check if driver is active."""
        return self.status == 1

    @property
    def is_deleted(self) -> bool:
        """Check if driver is soft-deleted."""
        return self.status == -1

    @property
    def cnh_expired(self) -> Optional[bool]:
        """Check if CNH is expired."""
        if not self.cnh_validate:
            return None
        return self.cnh_validate < date.today()

    @property
    def rac_expired(self) -> Optional[bool]:
        """Check if RAC is expired."""
        if not self.rac_validate:
            return None
        return self.rac_validate < date.today()

    @property
    def aso_expired(self) -> Optional[bool]:
        """Check if ASO is expired."""
        if not self.aso_validate:
            return None
        return self.aso_validate < date.today()

    def __repr__(self):
        return f"<Driver(id={self.id}, name={self.name}, cpf={self.cpf}, status={self.status})>"
