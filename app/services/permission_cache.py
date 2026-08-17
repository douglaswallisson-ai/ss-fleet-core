"""
Permission Cache Service

Redis-based caching for user permissions to ensure <5ms response time.
Caches user permissions as sets for fast lookup.
"""

import json
from typing import Set, Optional, List, Dict
from redis import asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.core.config import settings
from app.models.api_permissions import (
    ApiUserPermission,
    ApiPermission,
    ApiAction,
    ApiResource,
)


class PermissionCache:
    """Redis cache manager for user permissions"""

    def __init__(self):
        self.redis: Optional[aioredis.Redis] = None
        self.ttl = 3600  # 1 hour cache TTL
        self.prefix = "user_perms:"
        self._bypass_cache = False  # For testing without Redis
        self._memory_cache: Dict[int, Set[str]] = {}  # In-memory cache for testing

    async def connect(self):
        """Initialize Redis connection"""
        if not self.redis:
            self.redis = await aioredis.from_url(
                str(settings.REDIS_URL),
                encoding="utf-8",
                decode_responses=True,
            )

    async def disconnect(self):
        """Close Redis connection"""
        if self.redis:
            await self.redis.aclose()

    def _get_cache_key(self, user_id: int) -> str:
        """Generate cache key for user permissions"""
        return f"{self.prefix}{user_id}"

    async def get_user_permissions(self, user_id: int) -> Optional[Set[str]]:
        """
        Get cached user permissions.

        Args:
            user_id: User ID to fetch permissions for

        Returns:
            Set of permission keys (e.g., {"vehicles.read", "devices.create"})
            None if not cached
        """
        if self._bypass_cache:
            # Use in-memory cache when Redis is bypassed (for testing)
            return self._memory_cache.get(user_id)

        if not self.redis:
            await self.connect()

        cache_key = self._get_cache_key(user_id)
        cached_data = await self.redis.smembers(cache_key)

        if cached_data:
            return set(cached_data)

        return None

    async def set_user_permissions(
        self, user_id: int, permissions: Set[str], ttl: Optional[int] = None
    ) -> bool:
        """
        Cache user permissions.

        Args:
            user_id: User ID
            permissions: Set of permission keys
            ttl: Optional custom TTL in seconds

        Returns:
            True if cached successfully
        """
        if self._bypass_cache:
            # Use in-memory cache when Redis is bypassed (for testing)
            self._memory_cache[user_id] = permissions
            return True

        if not self.redis:
            await self.connect()

        cache_key = self._get_cache_key(user_id)
        ttl = ttl or self.ttl

        # Delete existing key
        await self.redis.delete(cache_key)

        # Cache as Redis SET for O(1) membership testing
        if permissions:
            await self.redis.sadd(cache_key, *permissions)
            await self.redis.expire(cache_key, ttl)

        return True

    async def invalidate_user(self, user_id: int) -> bool:
        """
        Invalidate cache for specific user.

        Args:
            user_id: User ID to invalidate

        Returns:
            True if invalidated successfully
        """
        if self._bypass_cache:
            # Clear from in-memory cache when Redis is bypassed (for testing)
            self._memory_cache.pop(user_id, None)
            return True

        if not self.redis:
            await self.connect()

        cache_key = self._get_cache_key(user_id)
        await self.redis.delete(cache_key)
        return True

    async def invalidate_all(self) -> bool:
        """
        Invalidate all permission caches.

        Returns:
            True if invalidated successfully
        """
        if self._bypass_cache:
            # Clear in-memory cache when Redis is bypassed (for testing)
            self._memory_cache.clear()
            return True

        if not self.redis:
            await self.connect()

        # Find all permission cache keys
        pattern = f"{self.prefix}*"
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
            if keys:
                await self.redis.delete(*keys)
            if cursor == 0:
                break

        return True

    async def load_user_permissions_from_db(
        self, db: AsyncSession, user_id: int
    ) -> Set[str]:
        """
        Load user permissions from database and cache them.

        Args:
            db: Database session
            user_id: User ID

        Returns:
            Set of permission keys
        """
        # Query database for user permissions
        query = (
            select(ApiPermission.permission_key)
            .join(ApiUserPermission, ApiUserPermission.permission_id == ApiPermission.id)
            .where(
                and_(
                    ApiUserPermission.user_id == user_id,
                    ApiUserPermission.granted == True,
                    ApiPermission.is_active == True,
                )
            )
        )

        result = await db.execute(query)
        permissions = {row[0] for row in result.fetchall()}

        # Cache the permissions
        await self.set_user_permissions(user_id, permissions)

        return permissions

    async def get_or_load_permissions(
        self, db: AsyncSession, user_id: int
    ) -> Set[str]:
        """
        Get permissions from cache or load from DB if not cached.

        Args:
            db: Database session
            user_id: User ID

        Returns:
            Set of permission keys
        """
        # Try cache first
        permissions = await self.get_user_permissions(user_id)

        # If not cached, load from DB
        if permissions is None:
            permissions = await self.load_user_permissions_from_db(db, user_id)

        return permissions

    async def has_permission(
        self, db: AsyncSession, user_id: int, permission_key: str
    ) -> bool:
        """
        Check if user has specific permission (optimized for <5ms).

        Args:
            db: Database session
            user_id: User ID
            permission_key: Permission to check (e.g., "vehicles.read")

        Returns:
            True if user has permission
        """
        permissions = await self.get_or_load_permissions(db, user_id)
        return permission_key in permissions

    async def get_permissions_by_resource(
        self, db: AsyncSession, user_id: int, resource_name: str
    ) -> Dict[str, bool]:
        """
        Get all permissions for a specific resource.

        Args:
            db: Database session
            user_id: User ID
            resource_name: Resource name (e.g., "vehicles")

        Returns:
            Dict mapping action to permission status:
            {"read": True, "create": False, "update": True, "delete": False}
        """
        permissions = await self.get_or_load_permissions(db, user_id)

        # Extract resource permissions
        resource_perms = {
            perm.split(".")[-1]  # Extract action name
            for perm in permissions
            if perm.startswith(f"{resource_name}.")
        }

        # Map all possible actions
        all_actions = ["read", "create", "update", "delete"]
        return {action: action in resource_perms for action in all_actions}


# Global instance
permission_cache = PermissionCache()
