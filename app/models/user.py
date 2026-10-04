"""
User model - Maps to existing mova.users table (legacy).
Uses SHA1 for password validation (compatibility with old system).
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Date, Time
from sqlalchemy.orm import relationship
from typing import Optional

from app.core.database import Base


class User(Base):
    """
    User model mapped to existing mova.users table.

    Legacy fields from mova.users:
        id: Primary key
        name: Full name
        login: Username (used for authentication)
        password: SHA1 hashed password
        email: Email address
        status: 1=active, 0=inactive
        master: 1=admin, 0=regular user
        account_id: Account/Company ID
        token: Legacy API token (not used in new system)
        ... other legacy fields preserved
    """

    __tablename__ = "users"
    __table_args__ = {'schema': 'mova'}  # mova schema

    # Primary fields
    id = Column('id', Integer, primary_key=True, index=True)
    name = Column('name', String(255))
    login = Column('login', String(255), unique=True, index=True)  # Username for auth
    password = Column('password', String(255))  # SHA1 hash
    email = Column('email', String(255), index=True)

    # Status and hierarchy
    status = Column('status', Integer, default=1)  # 1=active, 0=inactive
    master = Column('master', Integer, default=0)  # 1=admin, 0=regular
    account_id = Column('account_id', Integer)
    customer_id = Column('customer_id', Integer)

    # Access control (temporal)
    hour_start = Column('hour_start', Time)
    hour_end = Column('hour_end', Time)
    day_start = Column('day_start', Integer)
    day_end = Column('day_end', Integer)
    end_access = Column('end_access', Date)

    # Access flags (legacy)
    user_mova = Column('user_mova', Integer, default=0)
    user_mobile = Column('user_mobile', Integer, default=0)
    user_web = Column('user_web', Integer, default=0)

    # Audit fields
    date_add = Column('date_add', DateTime, default=datetime.utcnow)
    user_add = Column('user_add', Integer)
    date_modif = Column('date_modif', DateTime, onupdate=datetime.utcnow)
    user_modif = Column('user_modif', Integer)
    modify_date = Column('modify_date', DateTime)

    # Version control
    version_id = Column('version_id', Integer, default=0)

    # Legacy token (not used in new JWT system)
    token = Column('token', String(255))

    # Virtual properties for compatibility with new system
    @property
    def is_active(self) -> bool:
        """Check if user is active based on status field."""
        return self.status == 1

    @property
    def full_name(self) -> str:
        """Alias for name field."""
        return self.name or ""

    @property
    def role(self) -> str:
        """Infer role from master flag."""
        return "ADMIN" if self.master == 1 else "VIEWER"

    @property
    def is_super_admin(self) -> bool:
        """Check if user is a SS Telematica internal user (super admin).

        Além de `user_mova`, aceita os ids de SS_ADMIN_USER_IDS (paliativo para
        usuário da SS cujo cadastro está sem a caixa "Usuário SS").
        """
        if self.user_mova == 1:
            return True
        from app.core.config import settings
        ids = {s.strip() for s in (settings.SS_ADMIN_USER_IDS or "").split(",") if s.strip()}
        return str(self.id) in ids

    @property
    def password_hash(self) -> str:
        """Alias for password field (SHA1)."""
        return self.password or ""

    @property
    def created_at(self) -> Optional[datetime]:
        """Alias for date_add."""
        return self.date_add

    @property
    def updated_at(self) -> Optional[datetime]:
        """Alias for date_modif."""
        return self.date_modif or self.modify_date

    # Relationships (pointing to fleet_ tables)
    # Note: These won't work with legacy data, only for new records
    # api_keys = relationship("APIKey", back_populates="owner", cascade="all, delete-orphan")
    # vehicles = relationship("Vehicle", back_populates="owner", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(id={self.id}, login={self.login}, master={self.master})>"
