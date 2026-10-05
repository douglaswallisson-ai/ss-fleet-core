"""
Authentication middleware and dependencies.
Handles JWT and API Key authentication.
"""

from typing import Optional, Set
from datetime import datetime
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import decode_token, verify_api_key
from app.core.logging import get_logger
from app.core.access_control import get_user_group_access
from app.models.user import User
from app.models.api_key import APIKey
from app.services.permission_cache import permission_cache

# Import to set user access in context for automatic filtering
from app.core.secure_session import set_request_user_access

logger = get_logger(__name__)

# HTTP Bearer scheme for token extraction
security = HTTPBearer(auto_error=False)


class AuthenticatedUser:
    """
    Wrapper class for authenticated users (both platform users and API keys).
    Provides unified interface for permission checking.
    """

    def __init__(
        self,
        user_id: int,
        email: Optional[str] = None,
        role: Optional[str] = None,
        auth_type: str = "jwt",
        api_key_id: Optional[int] = None,
        permissions: Optional[Set[str]] = None,
        group_access: Optional[list] = None,
        is_super_admin: bool = False,
    ):
        self.user_id = user_id
        self.email = email
        self.role = role
        self.auth_type = auth_type  # "jwt" or "api_key"
        self.api_key_id = api_key_id
        self.permissions = permissions or set()  # Set of permission keys (e.g., {"vehicles.read"})
        self.group_access = group_access or []  # List of (group_id, subgroup_id) tuples
        self.is_super_admin = is_super_admin  # user_mova=1 (SS Telematica internal user)

    def is_admin(self) -> bool:
        """Check if user has admin role (DEPRECATED: use permission-based checks)."""
        return self.role == "ADMIN"

    def has_permission(self, resource: str, action: str) -> bool:
        """
        Check if user has specific permission using new permission system.

        Args:
            resource: Resource name (e.g., "vehicles")
            action: Action name (e.g., "read", "create", "update", "delete")

        Returns:
            True if permission exists

        Examples:
            >>> user.has_permission("vehicles", "read")
            True
            >>> user.has_permission("devices", "delete")
            False
        """
        # Build permission key (e.g., "vehicles.read")
        permission_key = f"{resource}.{action}"

        # Check if user has this permission
        return permission_key in self.permissions


async def authenticate_jwt(
    token: str,
    db: AsyncSession
) -> Optional[AuthenticatedUser]:
    """
    Authenticate user via JWT token.

    Args:
        token: JWT token string
        db: Database session

    Returns:
        AuthenticatedUser if valid, None if invalid

    Raises:
        HTTPException: If token is invalid or user not found
    """
    try:
        # Decode JWT
        payload = decode_token(token)
        user_id = payload.get("sub")
        token_type = payload.get("type")

        if not user_id or token_type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type"
            )

        # Get user from database
        result = await db.execute(
            select(User).where(User.id == int(user_id))
        )
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found"
            )

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is disabled"
            )

        # Get user's accessible groups and subgroups (with Redis cache)
        group_access = await get_user_group_access(user.id, db)

        # Load user permissions from cache or DB (new permission system)
        permissions = await permission_cache.get_or_load_permissions(db, user.id)

        return AuthenticatedUser(
            user_id=user.id,
            email=user.email,
            role=user.role,
            auth_type="jwt",
            permissions=permissions,
            group_access=group_access,
            is_super_admin=user.is_super_admin
        )

    except JWTError as e:
        logger.warning("jwt_authentication_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )


async def authenticate_api_key(
    api_key: str,
    db: AsyncSession
) -> Optional[AuthenticatedUser]:
    """
    Authenticate via API Key.

    Args:
        api_key: API key string (e.g., "sk_live_abc123...")
        db: Database session

    Returns:
        AuthenticatedUser if valid, None if invalid

    Raises:
        HTTPException: If API key is invalid or expired
    """
    # Extract prefix (e.g., "sk_live_")
    if "_" not in api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key format"
        )

    parts = api_key.split("_")
    if len(parts) < 3:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key format"
        )

    prefix = f"{parts[0]}_{parts[1]}_"

    # Query API keys with matching prefix
    result = await db.execute(
        select(APIKey).where(
            APIKey.key_prefix == prefix,
            APIKey.is_active == True
        )
    )
    api_keys = result.scalars().all()

    # Verify hash
    for key in api_keys:
        if verify_api_key(api_key, key.key_hash):
            # Check expiration
            if key.is_expired():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API key has expired"
                )

            # Update last used timestamp
            key.last_used_at = datetime.utcnow()
            key.total_requests += 1
            await db.commit()

            # Load user permissions from cache or DB (new permission system)
            permissions = await permission_cache.get_or_load_permissions(db, key.owner_id)

            # Get user's accessible groups and subgroups (with Redis cache)
            group_access = await get_user_group_access(key.owner_id, db)

            # Get user to check is_super_admin (user_mova)
            user_result = await db.execute(
                select(User).where(User.id == key.owner_id)
            )
            owner = user_result.scalar_one_or_none()
            is_super_admin = owner.is_super_admin if owner else False

            return AuthenticatedUser(
                user_id=key.owner_id,
                auth_type="api_key",
                api_key_id=key.id,
                permissions=permissions,
                group_access=group_access,
                is_super_admin=is_super_admin
            )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key"
    )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> AuthenticatedUser:
    """
    FastAPI dependency to get current authenticated user.
    Supports both JWT tokens and API keys.

    Usage:
        @app.get("/protected")
        async def protected_route(user: AuthenticatedUser = Depends(get_current_user)):
            return {"user_id": user.user_id}
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Detect token type
    if token.startswith("sk_") or token.startswith("pk_"):
        # API Key authentication
        user = await authenticate_api_key(token, db)
    else:
        # JWT authentication
        user = await authenticate_jwt(token, db)

    # Set user access in context for automatic query filtering
    set_request_user_access(user.group_access)

    return user


async def get_current_active_user(
    current_user: AuthenticatedUser = Depends(get_current_user)
) -> AuthenticatedUser:
    """
    Get current active user (already verified by get_current_user).
    Alias for better code readability.
    """
    return current_user


def require_permission(resource: str, action: str):
    """
    Dependency factory for permission-based access control.

    Usage:
        @app.get("/vehicles")
        async def list_vehicles(
            user: AuthenticatedUser = Depends(require_permission("vehicles", "read"))
        ):
            return vehicles
    """
    async def permission_checker(
        user: AuthenticatedUser = Depends(get_current_user)
    ) -> AuthenticatedUser:
        if not user.has_permission(resource, action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions: {action}:{resource}"
            )
        return user

    return permission_checker


def require_role(*roles: str):
    """
    Dependency factory for role-based access control.

    Usage:
        @app.delete("/users/{user_id}")
        async def delete_user(
            user_id: int,
            user: AuthenticatedUser = Depends(require_role("admin"))
        ):
            # Only admins can access
            pass
    """
    async def role_checker(
        user: AuthenticatedUser = Depends(get_current_user)
    ) -> AuthenticatedUser:
        if user.auth_type != "jwt":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This endpoint requires platform authentication"
            )

        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required role: {', '.join(roles)}"
            )

        return user

    return role_checker
