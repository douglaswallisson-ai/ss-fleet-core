"""
Pytest configuration and shared fixtures for tests.
"""

import pytest
import asyncio
from typing import AsyncGenerator
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.main import app
from app.core.database import get_db, get_db_read
from app.core.config import settings
from app.core.security import create_access_token
from app.services.permission_cache import permission_cache


# Event loop fixture for async tests
@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# Database session fixture
@pytest.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """
    Create a test database session.

    Uses the same database as production but in a transaction that can be rolled back.
    """
    engine = create_async_engine(settings.database_url_async, echo=False)
    async_session_local = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session_local() as session:
        yield session


# Redis cleanup fixture
@pytest.fixture(autouse=True)
async def cleanup_redis():
    """
    Cleanup Redis connections after each test to prevent event loop issues.
    Bypass Redis cache during tests to avoid connection errors.
    Uses in-memory cache instead for testing.
    """
    # Enable cache bypass mode for testing (uses in-memory cache instead of Redis)
    permission_cache._bypass_cache = True
    permission_cache._memory_cache.clear()  # Clear in-memory cache before each test
    yield
    # Disconnect Redis after each test if it was connected
    if permission_cache.redis:
        await permission_cache.disconnect()
        permission_cache.redis = None
    # Clear in-memory cache after test
    permission_cache._memory_cache.clear()
    # Reset bypass mode
    permission_cache._bypass_cache = False


# HTTP client fixture
@pytest.fixture
async def client(db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """
    Create an async HTTP client for API testing.

    Overrides the get_db dependency to use the test database session.
    """
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_read] = override_get_db  # Same session for reads in tests

    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# Authentication token fixtures
@pytest.fixture
def admin_token() -> str:
    """
    Generate JWT token for user 4674 (admin with all permissions).
    """
    return create_access_token("4674")


@pytest.fixture
def viewer_token() -> str:
    """
    Generate JWT token for user 1391 (viewer with read-only permissions).
    """
    return create_access_token("1391")


@pytest.fixture
def unauthorized_user_token() -> str:
    """
    Generate JWT token for a user with no permissions (user_id=9999).
    """
    return create_access_token("9999")
