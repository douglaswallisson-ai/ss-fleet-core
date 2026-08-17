"""
API Permissions Models

SQLAlchemy models for fine-grained API access control system.
Replaces role-based access control with permission-based system.
"""

from sqlalchemy import (
    Column, Integer, String, Boolean, Text, TIMESTAMP,
    ForeignKey, UniqueConstraint, Index, text
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import INET
from datetime import datetime

from app.core.database import Base


class ApiResource(Base):
    """API Resources (vehicles, drivers, devices, etc.)"""
    __tablename__ = "api_resources"

    id = Column(Integer, primary_key=True, index=True)
    resource_name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(TIMESTAMP, nullable=False, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP, nullable=False, server_default=text("NOW()"))

    # Relationships
    permissions = relationship("ApiPermission", back_populates="resource", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ApiResource(id={self.id}, name={self.resource_name})>"


class ApiAction(Base):
    """API Actions (read, create, update, delete)"""
    __tablename__ = "api_actions"

    id = Column(Integer, primary_key=True, index=True)
    action_name = Column(String(20), nullable=False, unique=True)
    http_methods = Column(String(50), nullable=False)  # GET, POST, PUT,PATCH, DELETE
    description = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, nullable=False, server_default=text("NOW()"))

    # Relationships
    permissions = relationship("ApiPermission", back_populates="action", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ApiAction(id={self.id}, name={self.action_name}, methods={self.http_methods})>"


class ApiPermission(Base):
    """API Permissions (resource × action combinations)"""
    __tablename__ = "api_permissions"

    id = Column(Integer, primary_key=True, index=True)
    resource_id = Column(Integer, ForeignKey("api_resources.id", ondelete="CASCADE"), nullable=False)
    action_id = Column(Integer, ForeignKey("api_actions.id", ondelete="CASCADE"), nullable=False)
    permission_key = Column(String(100), nullable=False, unique=True)  # e.g., "vehicles.read"
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(TIMESTAMP, nullable=False, server_default=text("NOW()"))

    # Relationships
    resource = relationship("ApiResource", back_populates="permissions")
    action = relationship("ApiAction", back_populates="permissions")
    user_permissions = relationship("ApiUserPermission", back_populates="permission", cascade="all, delete-orphan")

    # Constraints
    __table_args__ = (
        UniqueConstraint('resource_id', 'action_id', name='unique_resource_action'),
        Index('idx_api_permissions_key', 'permission_key'),
        Index('idx_api_permissions_active', 'is_active'),
    )

    def __repr__(self):
        return f"<ApiPermission(id={self.id}, key={self.permission_key})>"


class ApiUserPermission(Base):
    """User Permissions (which users have which permissions)"""
    __tablename__ = "api_user_permissions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("mova.users.id", ondelete="CASCADE"), nullable=False)
    permission_id = Column(Integer, ForeignKey("api_permissions.id", ondelete="CASCADE"), nullable=False)
    granted = Column(Boolean, nullable=False, server_default="true")
    granted_by = Column(Integer, ForeignKey("mova.users.id"), nullable=True)
    granted_at = Column(TIMESTAMP, nullable=False, server_default=text("NOW()"))
    revoked_by = Column(Integer, ForeignKey("mova.users.id"), nullable=True)
    revoked_at = Column(TIMESTAMP, nullable=True)
    notes = Column(Text, nullable=True)

    # Relationships
    permission = relationship("ApiPermission", back_populates="user_permissions")

    # Constraints
    __table_args__ = (
        UniqueConstraint('user_id', 'permission_id', name='unique_user_permission'),
        Index('idx_api_user_permissions_user', 'user_id'),
        Index('idx_api_user_permissions_granted', 'user_id', 'granted'),
        Index('idx_api_user_permissions_composite', 'user_id', 'permission_id', 'granted'),
    )

    def __repr__(self):
        return f"<ApiUserPermission(user_id={self.user_id}, permission_id={self.permission_id}, granted={self.granted})>"


class ApiUserPermissionHistory(Base):
    """Audit History for Permission Changes"""
    __tablename__ = "api_user_permissions_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    permission_id = Column(Integer, nullable=False)
    permission_key = Column(String(100), nullable=False)
    action = Column(String(20), nullable=False)  # granted, revoked, modified
    granted = Column(Boolean, nullable=False)
    changed_by = Column(Integer, nullable=False)
    changed_at = Column(TIMESTAMP, nullable=False, server_default=text("NOW()"))
    ip_address = Column(INET, nullable=True)
    user_agent = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    user_email = Column(String(255), nullable=True)
    resource_name = Column(String(100), nullable=True)
    action_name = Column(String(20), nullable=True)

    __table_args__ = (
        Index('idx_api_permissions_history_user', 'user_id'),
        Index('idx_api_permissions_history_date', text('changed_at DESC')),
        Index('idx_api_permissions_history_changed_by', 'changed_by'),
    )

    def __repr__(self):
        return f"<ApiUserPermissionHistory(user_id={self.user_id}, action={self.action}, changed_at={self.changed_at})>"
