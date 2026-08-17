"""
Tests for API Permissions System

Tests permission-based access control, cache performance, and admin endpoints.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.api_permissions import (
    ApiPermission,
    ApiUserPermission,
    ApiResource,
    ApiAction,
)
from app.services.permission_cache import permission_cache


class TestPermissionCacheService:
    """Test permission cache functionality"""

    @pytest.mark.asyncio
    async def test_get_or_load_permissions_user_4674(self, db: AsyncSession):
        """Test loading permissions for user 4674 (admin - all permissions)"""
        permissions = await permission_cache.get_or_load_permissions(db, 4674)

        # User 4674 should have 24+ permissions (6 resources × 4 actions + admin.manage)
        assert len(permissions) >= 24
        assert "vehicles.read" in permissions
        assert "vehicles.create" in permissions
        assert "vehicles.update" in permissions
        assert "vehicles.delete" in permissions
        assert "devices.read" in permissions
        assert "admin.manage" in permissions

    @pytest.mark.asyncio
    async def test_get_or_load_permissions_user_1391(self, db: AsyncSession):
        """Test loading permissions for user 1391 (viewer - read only)"""
        permissions = await permission_cache.get_or_load_permissions(db, 1391)

        # User 1391 should have at least core read permissions
        assert len(permissions) >= 6
        assert "vehicles.read" in permissions
        assert "devices.read" in permissions
        assert "drivers.read" in permissions
        assert "reports.read" in permissions
        assert "users.read" in permissions
        assert "groups.read" in permissions

        # Should NOT have write/delete permissions
        assert "vehicles.create" not in permissions
        assert "vehicles.update" not in permissions
        assert "vehicles.delete" not in permissions

    @pytest.mark.asyncio
    async def test_permission_cache_performance(self, db: AsyncSession):
        """Test cache hit performance (should be <5ms)"""
        import time

        # First load (DB query)
        start = time.perf_counter()
        permissions1 = await permission_cache.get_or_load_permissions(db, 4674)
        first_load_time = (time.perf_counter() - start) * 1000  # Convert to ms

        # Second load (cached)
        start = time.perf_counter()
        permissions2 = await permission_cache.get_or_load_permissions(db, 4674)
        cache_hit_time = (time.perf_counter() - start) * 1000  # Convert to ms

        # Verify cache works
        assert permissions1 == permissions2

        # Cache hit should be significantly faster (< 5ms target)
        print(f"First load: {first_load_time:.2f}ms, Cache hit: {cache_hit_time:.2f}ms")
        assert cache_hit_time < 10  # Allow some margin for test environment

    @pytest.mark.asyncio
    async def test_has_permission(self, db: AsyncSession):
        """Test has_permission method"""
        # User 4674 should have vehicles.read
        has_perm = await permission_cache.has_permission(db, 4674, "vehicles.read")
        assert has_perm is True

        # User 1391 should NOT have vehicles.create
        has_perm = await permission_cache.has_permission(db, 1391, "vehicles.create")
        assert has_perm is False

    @pytest.mark.asyncio
    async def test_invalidate_user_cache(self, db: AsyncSession):
        """Test cache invalidation"""
        # Load permissions (cache them)
        await permission_cache.get_or_load_permissions(db, 4674)

        # Invalidate cache
        await permission_cache.invalidate_user(4674)

        # Verify cache is empty
        cached = await permission_cache.get_user_permissions(4674)
        assert cached is None


class TestAdminPermissionEndpoints:
    """Test admin permission management endpoints"""

    @pytest.mark.asyncio
    async def test_list_all_permissions(self, client: AsyncClient, admin_token: str):
        """Test GET /admin/permissions - list all permissions"""
        response = await client.get(
            "/api/v1/admin/permissions/",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

        assert response.status_code == 200
        permissions = response.json()

        # Should have at least 24 permissions (6 resources × 4 actions)
        assert len(permissions) >= 24

        # Check structure
        assert "id" in permissions[0]
        assert "permission_key" in permissions[0]
        assert "resource_name" in permissions[0]
        assert "action_name" in permissions[0]

    @pytest.mark.asyncio
    async def test_list_user_permissions(self, client: AsyncClient, admin_token: str):
        """Test GET /admin/permissions/users/{user_id}"""
        response = await client.get(
            "/api/v1/admin/permissions/users/4674",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

        assert response.status_code == 200
        permissions = response.json()

        # User 4674 should have 24+ permissions
        assert len(permissions) >= 24

        # Check structure
        assert permissions[0]["user_id"] == 4674
        assert "permission_key" in permissions[0]
        assert "granted" in permissions[0]

    @pytest.mark.asyncio
    async def test_grant_permission(
        self, client: AsyncClient, admin_token: str, db: AsyncSession
    ):
        """Test POST /admin/permissions/grant"""
        # Grant devices.update to user 1391 (who currently has only read permissions)
        response = await client.post(
            "/api/v1/admin/permissions/grant",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "user_id": 1391,
                "permission_key": "devices.update",
                "notes": "Testing permission grant"
            }
        )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "completed"
        assert data["user_id"] == 1391
        assert "devices.update" in data["granted"] or "devices.update" in data["already_granted"]
        assert data["granted_by"] == 4674

        # Verify permission was granted
        permission = await db.execute(
            select(ApiPermission).where(ApiPermission.permission_key == "devices.update")
        )
        perm = permission.scalar_one()

        user_perm = await db.execute(
            select(ApiUserPermission).where(
                ApiUserPermission.user_id == 1391,
                ApiUserPermission.permission_id == perm.id
            )
        )
        up = user_perm.scalar_one_or_none()

        assert up is not None
        assert up.granted is True

        # Verify cache was invalidated
        cached_perms = await permission_cache.get_or_load_permissions(db, 1391)
        assert "devices.update" in cached_perms

    @pytest.mark.asyncio
    async def test_revoke_permission(
        self, client: AsyncClient, admin_token: str, db: AsyncSession
    ):
        """Test POST /admin/permissions/revoke"""
        # First grant a permission
        await client.post(
            "/api/v1/admin/permissions/grant",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "user_id": 1391,
                "permission_key": "devices.create",
                "notes": "Test grant for revoke"
            }
        )

        # Now revoke it
        response = await client.post(
            "/api/v1/admin/permissions/revoke",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "user_id": 1391,
                "permission_key": "devices.create",
                "notes": "Testing permission revoke"
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "revoked"

        # Verify permission was revoked
        permission = await db.execute(
            select(ApiPermission).where(ApiPermission.permission_key == "devices.create")
        )
        perm = permission.scalar_one()

        user_perm = await db.execute(
            select(ApiUserPermission).where(
                ApiUserPermission.user_id == 1391,
                ApiUserPermission.permission_id == perm.id
            )
        )
        up = user_perm.scalar_one()

        assert up.granted is False

        # Verify cache was invalidated
        cached_perms = await permission_cache.get_or_load_permissions(db, 1391)
        assert "devices.create" not in cached_perms

    @pytest.mark.asyncio
    async def test_permission_audit_log(self, client: AsyncClient, admin_token: str):
        """Test GET /admin/permissions/audit"""
        response = await client.get(
            "/api/v1/admin/permissions/audit?limit=10",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

        assert response.status_code == 200
        audit_log = response.json()

        # Should have audit entries
        assert len(audit_log) > 0

        # Check structure
        assert "user_id" in audit_log[0]
        assert "permission_key" in audit_log[0]
        assert "action" in audit_log[0]  # granted, revoked, modified
        assert "changed_by" in audit_log[0]
        assert "changed_at" in audit_log[0]

    @pytest.mark.asyncio
    async def test_unauthorized_access_to_admin_endpoints(
        self, client: AsyncClient, viewer_token: str
    ):
        """Test that non-admin users cannot access permission management"""
        # Try to list permissions with viewer token
        response = await client.get(
            "/api/v1/admin/permissions/",
            headers={"Authorization": f"Bearer {viewer_token}"}
        )

        assert response.status_code == 403
        assert "admin.manage" in response.json()["detail"].lower()


class TestPermissionEnforcement:
    """Test permission enforcement on actual endpoints"""

    @pytest.mark.asyncio
    async def test_user_4674_can_access_all_endpoints(
        self, client: AsyncClient, admin_token: str
    ):
        """Test user 4674 (admin) can access all CRUD operations"""
        # GET (read)
        response = await client.get(
            "/api/v1/vehicles/",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert response.status_code == 200

        # POST (create) - will fail due to validation, but not permission
        response = await client.post(
            "/api/v1/vehicles/",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"label": "TEST-001", "group_id": 1, "subgroup_id": 1}
        )
        # 422 (validation error) or 201 (success) - not 403 (forbidden)
        assert response.status_code in [201, 422]

    @pytest.mark.asyncio
    async def test_user_1391_can_only_read(
        self, client: AsyncClient, viewer_token: str
    ):
        """Test user 1391 (viewer) can only read, not create/update/delete"""
        # GET (read) - should work
        response = await client.get(
            "/api/v1/vehicles/",
            headers={"Authorization": f"Bearer {viewer_token}"}
        )
        assert response.status_code == 200

        # POST (create) - should fail with 403
        response = await client.post(
            "/api/v1/vehicles/",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={"label": "TEST-002", "group_id": 1, "subgroup_id": 1}
        )
        assert response.status_code == 403
        error_msg = response.json()["detail"].lower()
        assert any(keyword in error_msg for keyword in ["vehicles", "create", "permission"])

        # PUT (update) - should fail with 403
        response = await client.put(
            "/api/v1/vehicles/1",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={"label": "UPDATED"}
        )
        assert response.status_code == 403
        error_msg = response.json()["detail"].lower()
        assert any(keyword in error_msg for keyword in ["vehicles", "update", "permission"])

        # DELETE - should fail with 403
        response = await client.delete(
            "/api/v1/vehicles/1",
            headers={"Authorization": f"Bearer {viewer_token}"}
        )
        assert response.status_code == 403
        error_msg = response.json()["detail"].lower()
        assert any(keyword in error_msg for keyword in ["vehicles", "delete", "permission"])
