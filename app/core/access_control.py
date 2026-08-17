"""
Access Control utilities for group-based permissions.
Provides functions to manage user access to resources based on group_id and subgroup_id.
"""

import json
from typing import List, Optional, Tuple, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from app.models.user_group_access import UserGroupAccess
from app.core.redis import async_redis_client
from app.core.logging import get_logger

logger = get_logger(__name__)

# Cache TTL for user groups (24 hours)
USER_GROUPS_CACHE_TTL = 86400


async def get_user_group_access(user_id: int, db: AsyncSession) -> List[Tuple[int, Optional[int]]]:
    """
    Get list of (group_id, subgroup_id) pairs that a user has access to.
    Uses Redis cache for performance (24h TTL).

    Args:
        user_id: User ID
        db: Database session

    Returns:
        List of tuples (group_id, subgroup_id) the user can access.
        Example: [(14330, 15812), (14330, None), (14331, 99999)]
    """
    cache_key = f"user:{user_id}:group_access"

    try:
        # Try to get from Redis cache
        cached_access = await async_redis_client.get(cache_key)
        if cached_access:
            logger.debug("user_group_access_cache_hit", user_id=user_id)
            # Convert from JSON list of lists to list of tuples
            return [tuple(item) for item in json.loads(cached_access)]

        # Cache miss - query database
        logger.debug("user_group_access_cache_miss", user_id=user_id)
        result = await db.execute(
            select(UserGroupAccess.group_id, UserGroupAccess.subgroup_id)
            .where(UserGroupAccess.user_id == user_id)
        )
        group_access = [
            (row[0], row[1])
            for row in result.fetchall()
            if row[0] is not None  # group_id must exist
        ]

        # Store in cache (convert tuples to lists for JSON serialization)
        if group_access:
            await async_redis_client.setex(
                cache_key,
                USER_GROUPS_CACHE_TTL,
                json.dumps([list(item) for item in group_access])
            )
            logger.info("user_group_access_cached", user_id=user_id, access_count=len(group_access))

        return group_access

    except Exception as e:
        logger.error("user_group_access_fetch_failed", user_id=user_id, error=str(e))
        # Fallback: query database without cache
        result = await db.execute(
            select(UserGroupAccess.group_id, UserGroupAccess.subgroup_id)
            .where(UserGroupAccess.user_id == user_id)
        )
        return [(row[0], row[1]) for row in result.fetchall() if row[0] is not None]


async def invalidate_user_groups_cache(user_id: int) -> None:
    """
    Invalidate cached group access for a user.
    Call this when user's group access changes.

    Args:
        user_id: User ID
    """
    cache_key = f"user:{user_id}:group_access"
    try:
        await async_redis_client.delete(cache_key)
        logger.info("user_group_access_cache_invalidated", user_id=user_id)
    except Exception as e:
        logger.warning("user_group_access_cache_invalidation_failed", user_id=user_id, error=str(e))


def build_group_subgroup_filter(group_access: List[Tuple[int, Optional[int]]]):
    """
    Build SQLAlchemy filter for group_id and subgroup_id access control.

    **AUTOMATICALLY DETECTS** if model has subgroup_id column:
    - If model HAS subgroup_id: applies full group+subgroup filtering
    - If model DOES NOT have subgroup_id: applies only group_id filtering

    Logic for models WITH subgroup_id (e.g., Vehicle):
    - User can access resources where:
      - resource.group_id matches user's group_id AND
      - (resource.subgroup_id matches user's subgroup_id OR resource.subgroup_id IS NULL)

    Logic for models WITHOUT subgroup_id (e.g., Device):
    - User can access resources where:
      - resource.group_id IN (user's accessible group_ids)

    Args:
        group_access: List of (group_id, subgroup_id) tuples from get_user_group_access()

    Returns:
        A function that takes a model_class and returns the appropriate SQLAlchemy filter

    Example (model WITH subgroup_id):
        group_access = [(14330, 15812), (14331, None)]
        Returns filter equivalent to:
        (
            (group_id=14330 AND (subgroup_id=15812 OR subgroup_id IS NULL)) OR
            (group_id=14331 AND subgroup_id IS NULL)
        )

    Example (model WITHOUT subgroup_id):
        group_access = [(2977, 3099), (2977, 3100)]
        Returns filter equivalent to:
        group_id IN (2977)
    """
    from sqlalchemy import and_, or_

    if not group_access:
        # No access - return always false condition
        return False

    # Build conditions for each (group_id, subgroup_id) pair
    conditions = []

    # Group by group_id to optimize query
    group_map: Dict[int, List[Optional[int]]] = {}
    for group_id, subgroup_id in group_access:
        if group_id not in group_map:
            group_map[group_id] = []
        group_map[group_id].append(subgroup_id)

    # Build filter for each group
    def build_filter(model_class):
        """Builds filter for a given model class with group_id and optional subgroup_id"""
        # Check if model has subgroup_id column
        has_subgroup = hasattr(model_class, 'subgroup_id')

        if not has_subgroup:
            # Model doesn't have subgroup_id (e.g., Device) - use only group_id filtering
            accessible_groups = list(group_map.keys())
            logger.debug("access_filter_group_only", model=model_class.__name__, groups=accessible_groups)
            return model_class.group_id.in_(accessible_groups)

        # Model has subgroup_id (e.g., Vehicle) - use full group+subgroup filtering
        logger.debug("access_filter_group_subgroup", model=model_class.__name__, access_count=len(group_access))
        group_conditions = []

        for group_id, subgroup_ids in group_map.items():
            # Subgroup conditions for this group
            subgroup_conditions = []

            for subgroup_id in subgroup_ids:
                if subgroup_id is None:
                    # NULL subgroup means access to all subgroups in this group
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
