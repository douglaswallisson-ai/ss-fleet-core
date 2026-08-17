"""
Redis connection and caching utilities.
Provides Redis client and helper functions for caching and pub/sub.
"""

from typing import Optional, Any
import json
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Synchronous Redis client (for sync operations)
redis_client = Redis.from_url(
    str(settings.REDIS_URL),
    decode_responses=True,
    encoding="utf-8"
)

# Asynchronous Redis client (for FastAPI)
async_redis_client = AsyncRedis.from_url(
    str(settings.REDIS_URL),
    decode_responses=True,
    encoding="utf-8"
)


async def get_cache(key: str) -> Optional[Any]:
    """
    Get value from cache.

    Args:
        key: Cache key

    Returns:
        Cached value or None if not found
    """
    try:
        value = await async_redis_client.get(key)
        if value:
            return json.loads(value)
        return None
    except Exception as e:
        logger.error("cache_get_failed", key=key, error=str(e))
        return None


async def set_cache(key: str, value: Any, ttl: int = None) -> bool:
    """
    Set value in cache with optional TTL.

    Args:
        key: Cache key
        value: Value to cache (will be JSON serialized)
        ttl: Time to live in seconds (default from settings)

    Returns:
        True if successful, False otherwise
    """
    try:
        ttl = ttl or settings.REDIS_CACHE_TTL
        serialized = json.dumps(value)
        await async_redis_client.setex(key, ttl, serialized)
        return True
    except Exception as e:
        logger.error("cache_set_failed", key=key, error=str(e))
        return False


async def delete_cache(key: str) -> bool:
    """
    Delete value from cache.

    Args:
        key: Cache key

    Returns:
        True if successful, False otherwise
    """
    try:
        await async_redis_client.delete(key)
        return True
    except Exception as e:
        logger.error("cache_delete_failed", key=key, error=str(e))
        return False


async def check_redis_health() -> bool:
    """
    Check Redis connection health.

    Returns:
        True if Redis is accessible, False otherwise
    """
    try:
        await async_redis_client.ping()
        return True
    except Exception as e:
        logger.error("redis_health_check_failed", error=str(e))
        return False


def is_rate_limited(key: str, limit: int, window: int = 60) -> bool:
    """
    Check if rate limit is exceeded using sliding window.

    Args:
        key: Rate limit key (e.g., "ratelimit:user:123")
        limit: Maximum requests allowed
        window: Time window in seconds

    Returns:
        True if rate limit exceeded, False otherwise
    """
    try:
        count = redis_client.incr(key)

        # Set expiration on first request
        if count == 1:
            redis_client.expire(key, window)

        return count > limit
    except Exception as e:
        logger.error("rate_limit_check_failed", key=key, error=str(e))
        return False  # Allow request on error to avoid blocking legitimate users


async def publish_message(channel: str, message: dict) -> bool:
    """
    Publish message to Redis pub/sub channel.

    Args:
        channel: Channel name
        message: Message dict (will be JSON serialized)

    Returns:
        True if successful, False otherwise
    """
    try:
        serialized = json.dumps(message)
        await async_redis_client.publish(channel, serialized)
        return True
    except Exception as e:
        logger.error("pubsub_publish_failed", channel=channel, error=str(e))
        return False
