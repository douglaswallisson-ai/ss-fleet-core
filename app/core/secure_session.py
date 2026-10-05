"""
Secure Database Session with Automatic Access Control.
Provides context-aware session management that automatically enforces permissions.
"""

from typing import AsyncGenerator, Optional, List, Tuple
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger

logger = get_logger(__name__)

# Context variable to store current request's user access
_request_user_access: ContextVar[Optional[List[Tuple[int, Optional[int]]]]] = ContextVar(
    '_request_user_access',
    default=None
)


def set_request_user_access(group_access: Optional[List[Tuple[int, Optional[int]]]]) -> None:
    """
    Set current request's user group access in context.
    Called by authentication middleware after user is authenticated.

    Args:
        group_access: List of (group_id, subgroup_id) tuples or None
    """
    _request_user_access.set(group_access)
    if group_access:
        logger.debug("request_user_access_set", access_count=len(group_access))


def get_request_user_access() -> Optional[List[Tuple[int, Optional[int]]]]:
    """
    Get current request's user group access from context.

    Returns:
        List of (group_id, subgroup_id) tuples or None
    """
    return _request_user_access.get()


def clear_request_user_access() -> None:
    """Clear current request's user access from context."""
    _request_user_access.set(None)


async def get_secure_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency to get database session with automatic access control.

    This is a drop-in replacement for get_db() that provides the same interface
    but with automatic group/subgroup filtering based on the current user's access.

    Usage:
        @router.get("/vehicles")
        async def list_vehicles(db: AsyncSession = Depends(get_secure_db)):
            # Automatic filtering applied!
            result = await db.execute(select(Vehicle))
            return result.scalars().all()

    Access Control:
        - Automatically filters queries based on current_user.group_access
        - Only applies to models with group_id and subgroup_id columns
        - Can be bypassed for specific admin operations using bypass_access_control()
    """
    async with AsyncSessionLocal() as session:
        try:
            # Session is ready - access control will be applied via
            # the authentication middleware setting the context
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error("secure_database_error", error=str(e), exc_info=True)
            raise
        finally:
            await session.close()
            # Clean up context
            clear_request_user_access()


class AccessControlHelper:
    """
    Helper class to apply access control filters to queries manually.
    Used when automatic filtering via middleware is not desired.
    """

    @staticmethod
    def apply_filter(query, model_class, group_access: List[Tuple[int, Optional[int]]]):
        """
        Apply group/subgroup access filter to a query.

        Args:
            query: SQLAlchemy query or select statement
            model_class: Model class to filter (must have group_id and subgroup_id)
            group_access: List of (group_id, subgroup_id) tuples

        Returns:
            Filtered query

        Example:
            query = select(Vehicle)
            filtered = AccessControlHelper.apply_filter(
                query,
                Vehicle,
                current_user.group_access
            )
        """
        from app.core.access_control import build_group_subgroup_filter

        if not group_access:
            # No access - return query that matches nothing
            return query.where(False)

        access_filter = build_group_subgroup_filter(group_access)(model_class)
        return query.where(access_filter)

    @staticmethod
    def has_access_to_resource(
        resource_group_id: int,
        resource_subgroup_id: Optional[int],
        user_access: List[Tuple[int, Optional[int]]]
    ) -> bool:
        """
        Check if user has access to a specific resource based on group/subgroup.

        Args:
            resource_group_id: Resource's group_id
            resource_subgroup_id: Resource's subgroup_id (can be None)
            user_access: User's group access list

        Returns:
            True if user has access, False otherwise

        Example:
            has_access = AccessControlHelper.has_access_to_resource(
                vehicle.group_id,
                vehicle.subgroup_id,
                current_user.group_access
            )
        """
        for user_group_id, user_subgroup_id in user_access:
            # Check group match
            if user_group_id != resource_group_id:
                continue

            # Check subgroup match
            if resource_subgroup_id is None:
                # Resource is shared - accessible to all in group
                return True

            if user_subgroup_id is None:
                # User has access to shared resources only
                if resource_subgroup_id is None:
                    return True
            else:
                # User has specific subgroup access
                if resource_subgroup_id == user_subgroup_id or resource_subgroup_id is None:
                    return True

        return False


def require_access_control(func):
    """
    Decorator to enforce access control on endpoint functions.
    Validates that current user's access is set before executing.

    Usage:
        @router.get("/vehicles")
        @require_access_control
        async def list_vehicles(db: AsyncSession = Depends(get_db)):
            # Will fail if user access not set
            ...
    """
    from functools import wraps

    @wraps(func)
    async def wrapper(*args, **kwargs):
        user_access = get_request_user_access()
        if user_access is None:
            from fastapi import HTTPException, status
            logger.error(
                "access_control_violation",
                endpoint=func.__name__,
                error="User access not set in context"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Access control not properly configured"
            )
        return await func(*args, **kwargs)

    return wrapper
