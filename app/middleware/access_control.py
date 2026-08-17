"""
Access Control Middleware.
Automatically enforces group/subgroup filters on all database queries.
"""

from contextvars import ContextVar
from typing import Optional, List, Tuple
from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

# Context variable to store current user's group access
# This is thread-safe and async-safe
current_user_group_access: ContextVar[Optional[List[Tuple[int, Optional[int]]]]] = ContextVar(
    'current_user_group_access',
    default=None
)


def set_current_user_access(group_access: List[Tuple[int, Optional[int]]]) -> None:
    """
    Set current user's group access in context.
    This will be used to automatically filter queries.

    Args:
        group_access: List of (group_id, subgroup_id) tuples
    """
    current_user_group_access.set(group_access)
    logger.debug("user_access_context_set", access_count=len(group_access))


def clear_current_user_access() -> None:
    """
    Clear current user's group access from context.
    Should be called after request is processed.
    """
    current_user_group_access.set(None)
    logger.debug("user_access_context_cleared")


def get_current_user_access() -> Optional[List[Tuple[int, Optional[int]]]]:
    """
    Get current user's group access from context.

    Returns:
        List of (group_id, subgroup_id) tuples or None if not set
    """
    return current_user_group_access.get()


class SecureAsyncSession(AsyncSession):
    """
    Custom AsyncSession that automatically applies access control filters.

    This session wrapper intercepts queries and adds group/subgroup filters
    automatically for models that have group_id and subgroup_id columns.

    Usage:
        Instead of: db.execute(select(Vehicle))
        Same result with automatic filtering based on current_user.group_access
    """

    def __init__(self, *args, enforce_access_control: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.enforce_access_control = enforce_access_control
        self._access_control_applied = False

    async def execute(self, statement, *args, **kwargs):
        """
        Override execute to apply access control filters.
        """
        # Only apply access control for SELECT statements on models with group_id
        if self.enforce_access_control and not self._access_control_applied:
            group_access = get_current_user_access()

            if group_access is not None:
                statement = self._apply_access_filter(statement, group_access)

        return await super().execute(statement, *args, **kwargs)

    def _apply_access_filter(self, statement, group_access: List[Tuple[int, Optional[int]]]):
        """
        Apply group/subgroup filter to statement if applicable.

        Args:
            statement: SQLAlchemy statement
            group_access: List of (group_id, subgroup_id) tuples

        Returns:
            Modified statement with access filters applied
        """
        from sqlalchemy.sql.selectable import Select
        from sqlalchemy import and_, or_

        # Only process SELECT statements
        if not isinstance(statement, Select):
            return statement

        # Check if statement involves a model with group_id/subgroup_id
        # This is a simplified check - in production you'd want more robust detection
        try:
            # Get the primary entity being queried
            if hasattr(statement, 'column_descriptions'):
                for desc in statement.column_descriptions:
                    entity = desc.get('entity')
                    if entity and hasattr(entity, 'group_id') and hasattr(entity, 'subgroup_id'):
                        # Apply filter
                        filter_func = self._build_access_filter(group_access)
                        access_filter = filter_func(entity)
                        statement = statement.where(access_filter)

                        logger.debug(
                            "access_filter_auto_applied",
                            model=entity.__name__,
                            group_access_count=len(group_access)
                        )
                        break
        except Exception as e:
            logger.warning(
                "access_filter_application_skipped",
                error=str(e),
                statement_type=type(statement).__name__
            )

        return statement

    def _build_access_filter(self, group_access: List[Tuple[int, Optional[int]]]):
        """
        Build SQLAlchemy filter for group_id and subgroup_id.
        Same logic as build_group_subgroup_filter but inline.
        """
        from sqlalchemy import and_, or_
        from typing import Dict, List as ListType, Optional

        def build_filter(model_class):
            """Builds filter for a given model class with group_id and subgroup_id"""
            if not group_access:
                return False  # No access

            # Group by group_id to optimize query
            group_map: Dict[int, ListType[Optional[int]]] = {}
            for group_id, subgroup_id in group_access:
                if group_id not in group_map:
                    group_map[group_id] = []
                group_map[group_id].append(subgroup_id)

            group_conditions = []

            for group_id, subgroup_ids in group_map.items():
                subgroup_conditions = []

                for subgroup_id in subgroup_ids:
                    if subgroup_id is None:
                        # NULL subgroup means access to shared resources only
                        subgroup_conditions.append(model_class.subgroup_id.is_(None))
                    else:
                        # Specific subgroup: match subgroup_id OR NULL (shared resources)
                        subgroup_conditions.append(
                            or_(
                                model_class.subgroup_id == subgroup_id,
                                model_class.subgroup_id.is_(None)
                            )
                        )

                # Combine: group_id matches AND any subgroup condition
                group_conditions.append(
                    and_(
                        model_class.group_id == group_id,
                        or_(*subgroup_conditions)
                    )
                )

            return or_(*group_conditions)

        return build_filter


def bypass_access_control(session: AsyncSession) -> AsyncSession:
    """
    Temporarily bypass access control for a session.
    Use with extreme caution - only for system operations.

    Args:
        session: AsyncSession instance

    Returns:
        Same session with access control disabled

    Example:
        # For admin operations that need to bypass filters
        session = bypass_access_control(db)
        result = await session.execute(select(Vehicle))  # No filter applied
    """
    if isinstance(session, SecureAsyncSession):
        session.enforce_access_control = False
        logger.warning("access_control_bypassed",
                      warning="Access control has been bypassed - ensure this is intentional")
    return session


def enable_access_control(session: AsyncSession) -> AsyncSession:
    """
    Re-enable access control for a session.

    Args:
        session: AsyncSession instance

    Returns:
        Same session with access control enabled
    """
    if isinstance(session, SecureAsyncSession):
        session.enforce_access_control = True
        logger.debug("access_control_enabled")
    return session
