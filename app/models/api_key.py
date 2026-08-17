"""
API Key model - Represents third-party API access credentials.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import relationship

from app.core.database import Base


class APIKey(Base):
    """
    API Key model for third-party integrations.

    Attributes:
        id: Primary key
        owner_id: User who owns this API key
        name: Descriptive name for the API key
        key_prefix: Visible prefix (e.g., "sk_live_")
        key_hash: Hashed secret key
        permissions: JSON array of permissions
        rate_limit_per_hour: Custom rate limit
        is_active: Whether key is active
        expires_at: Optional expiration timestamp
        last_used_at: Last usage timestamp
        created_at: Creation timestamp
    """

    __tablename__ = "fleet_api_keys"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("mova.users.id"), nullable=False, index=True)

    # Key information
    name = Column(String(255), nullable=False)  # e.g., "ERP Integration"
    key_prefix = Column(String(20), nullable=False, index=True)  # e.g., "sk_live_"
    key_hash = Column(String(255), nullable=False)  # Bcrypt hash of full key

    # Permissions and limits
    permissions = Column(JSON, default=list, nullable=False)  # ["read:vehicles", "write:reports"]
    rate_limit_per_hour = Column(Integer, default=1000, nullable=False)

    # Status
    is_active = Column(Boolean, default=True, nullable=False)

    # Timestamps
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Usage tracking
    total_requests = Column(Integer, default=0, nullable=False)

    # Notes
    description = Column(Text, nullable=True)

    # Relationships
    # Note: Disabled because User model maps to mova.users (legacy table)
    # which doesn't have foreign key constraints to fleet_api_keys
    # owner = relationship("User", back_populates="api_keys")

    def __repr__(self):
        return f"<APIKey(id={self.id}, name={self.name}, prefix={self.key_prefix})>"

    def has_permission(self, resource: str, action: str) -> bool:
        """
        Check if API key has specific permission.

        Args:
            resource: Resource name (e.g., "vehicles")
            action: Action name (e.g., "read", "write")

        Returns:
            True if permission exists, False otherwise
        """
        permission = f"{action}:{resource}"
        return permission in self.permissions

    def is_expired(self) -> bool:
        """Check if API key is expired."""
        if not self.expires_at:
            return False
        return datetime.utcnow() > self.expires_at
