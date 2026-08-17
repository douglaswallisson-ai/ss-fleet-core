"""
Admin Permissions Management Endpoints

Endpoints for managing user permissions (grant, revoke, audit).
Only accessible by users with admin.permissions permission.
"""

from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc
from pydantic import BaseModel, Field

from app.core.database import get_db, get_db_read
from app.middleware.auth import AuthenticatedUser, get_current_user
from app.models.api_permissions import (
    ApiPermission,
    ApiUserPermission,
    ApiUserPermissionHistory,
    ApiResource,
    ApiAction,
)
from app.models.user import User
from app.services.permission_cache import permission_cache


router = APIRouter(prefix="/admin/permissions", tags=["Admin - Permissions"])


# ========================================
# Pydantic Schemas
# ========================================

class PermissionInfo(BaseModel):
    """Permission information"""
    id: int
    permission_key: str
    resource_name: str
    action_name: str
    description: Optional[str]
    is_active: bool

    class Config:
        from_attributes = True


class UserPermissionInfo(BaseModel):
    """User permission with details"""
    id: int
    user_id: int
    user_email: str
    permission_id: int
    permission_key: str
    resource_name: str
    action_name: str
    granted: bool
    granted_by: Optional[int]
    granted_at: datetime
    revoked_by: Optional[int]
    revoked_at: Optional[datetime]
    notes: Optional[str]

    class Config:
        from_attributes = True


class PermissionHistoryInfo(BaseModel):
    """Permission history entry"""
    id: int
    user_id: int
    user_email: Optional[str]
    permission_key: str
    resource_name: Optional[str]
    action_name: Optional[str]
    action: str  # granted, revoked, modified
    granted: bool
    changed_by: int
    changed_at: datetime
    ip_address: Optional[str]
    user_agent: Optional[str]
    notes: Optional[str]

    class Config:
        from_attributes = True


class PermissionItem(BaseModel):
    """Single permission item"""
    resource: str = Field(..., description="Resource name (e.g., 'vehicles')")
    action: str = Field(..., description="Action name (e.g., 'read')")


class GrantPermissionRequest(BaseModel):
    """Request to grant permission(s) - supports both single and batch"""
    user_id: int = Field(..., description="User ID to grant permission to")
    permission_key: Optional[str] = Field(None, description="Single permission key (e.g., 'vehicles.read')")
    permissions: Optional[List[PermissionItem]] = Field(None, description="List of permissions to grant")
    notes: Optional[str] = Field(None, description="Optional notes")

    def get_permission_keys(self) -> List[str]:
        """Get list of permission keys from either format"""
        if self.permission_key:
            return [self.permission_key]
        elif self.permissions:
            return [f"{p.resource}.{p.action}" for p in self.permissions]
        else:
            raise ValueError("Either permission_key or permissions must be provided")


class RevokePermissionRequest(BaseModel):
    """Request to revoke permission"""
    user_id: int = Field(..., description="User ID to revoke permission from")
    permission_key: str = Field(..., description="Permission key (e.g., 'vehicles.read')")
    notes: Optional[str] = Field(None, description="Optional notes")


# ========================================
# Helper Functions
# ========================================

async def require_admin_permission(
    current_user: AuthenticatedUser = Depends(get_current_user)
) -> AuthenticatedUser:
    """
    Dependency to ensure user has admin.permissions permission.
    """
    if not current_user.has_permission("admin", "manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions: admin.manage required"
        )
    return current_user


async def get_permission_by_key(db: AsyncSession, permission_key: str) -> ApiPermission:
    """Get permission by key or raise 404"""
    result = await db.execute(
        select(ApiPermission).where(ApiPermission.permission_key == permission_key)
    )
    permission = result.scalar_one_or_none()

    if not permission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Permission '{permission_key}' not found"
        )

    return permission


# ========================================
# Endpoints
# ========================================

@router.get("/", response_model=List[PermissionInfo])
async def list_all_permissions(
    resource: Optional[str] = None,
    action: Optional[str] = None,
    active_only: bool = True,
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_admin_permission)
):
    """
    List all available permissions.

    - **resource**: Filter by resource name (e.g., 'vehicles')
    - **action**: Filter by action name (e.g., 'read')
    - **active_only**: Only show active permissions (default: True)
    """
    # Build query
    query = (
        select(
            ApiPermission,
            ApiResource.resource_name,
            ApiAction.action_name
        )
        .join(ApiResource, ApiPermission.resource_id == ApiResource.id)
        .join(ApiAction, ApiPermission.action_id == ApiAction.id)
    )

    # Apply filters
    if active_only:
        query = query.where(ApiPermission.is_active == True)

    if resource:
        query = query.where(ApiResource.resource_name == resource)

    if action:
        query = query.where(ApiAction.action_name == action)

    result = await db.execute(query)
    rows = result.all()

    return [
        PermissionInfo(
            id=perm.id,
            permission_key=perm.permission_key,
            resource_name=resource_name,
            action_name=action_name,
            description=perm.description,
            is_active=perm.is_active
        )
        for perm, resource_name, action_name in rows
    ]


@router.get("/users/{user_id}", response_model=List[UserPermissionInfo])
async def list_user_permissions(
    user_id: int,
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_admin_permission)
):
    """
    List all permissions for a specific user.
    """
    # Verify user exists
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found"
        )

    # Get user permissions
    query = (
        select(
            ApiUserPermission,
            User.email,
            ApiPermission.permission_key,
            ApiResource.resource_name,
            ApiAction.action_name
        )
        .join(User, ApiUserPermission.user_id == User.id)
        .join(ApiPermission, ApiUserPermission.permission_id == ApiPermission.id)
        .join(ApiResource, ApiPermission.resource_id == ApiResource.id)
        .join(ApiAction, ApiPermission.action_id == ApiAction.id)
        .where(ApiUserPermission.user_id == user_id)
    )

    result = await db.execute(query)
    rows = result.all()

    return [
        UserPermissionInfo(
            id=up.id,
            user_id=up.user_id,
            user_email=email,
            permission_id=up.permission_id,
            permission_key=perm_key,
            resource_name=resource,
            action_name=action,
            granted=up.granted,
            granted_by=up.granted_by,
            granted_at=up.granted_at,
            revoked_by=up.revoked_by,
            revoked_at=up.revoked_at,
            notes=up.notes
        )
        for up, email, perm_key, resource, action in rows
    ]


@router.post("/grant", status_code=status.HTTP_201_CREATED)
async def grant_permission(
    request: GrantPermissionRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_admin_permission)
):
    """
    Grant permission(s) to a user.

    Supports two formats:
    1. Single permission: {"user_id": 123, "permission_key": "vehicles.read"}
    2. Batch permissions: {"user_id": 123, "permissions": [{"resource": "vehicles", "action": "read"}, ...]}
    """
    # Verify user exists
    user_result = await db.execute(select(User).where(User.id == request.user_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {request.user_id} not found"
        )

    # Get permission keys to grant
    try:
        permission_keys = request.get_permission_keys()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    # Process each permission
    granted_permissions = []
    already_granted = []
    not_found = []

    for perm_key in permission_keys:
        # Get permission
        try:
            permission = await get_permission_by_key(db, perm_key)
        except HTTPException:
            not_found.append(perm_key)
            continue

        # Check if already granted
        existing = await db.execute(
            select(ApiUserPermission).where(
                and_(
                    ApiUserPermission.user_id == request.user_id,
                    ApiUserPermission.permission_id == permission.id
                )
            )
        )
        existing_perm = existing.scalar_one_or_none()

        if existing_perm and existing_perm.granted:
            already_granted.append(perm_key)
            continue

        # Grant or update
        if existing_perm:
            # Re-grant previously revoked permission
            existing_perm.granted = True
            existing_perm.granted_by = current_user.user_id
            existing_perm.granted_at = datetime.utcnow()
            existing_perm.revoked_by = None
            existing_perm.revoked_at = None
            existing_perm.notes = request.notes
        else:
            # Create new permission
            new_perm = ApiUserPermission(
                user_id=request.user_id,
                permission_id=permission.id,
                granted=True,
                granted_by=current_user.user_id,
                notes=request.notes
            )
            db.add(new_perm)

        granted_permissions.append(perm_key)
        # Note: audit history is recorded automatically by the
        # `api_user_permissions_audit` DB trigger (see migration
        # 7c1e9a2f4b3d), no need to insert it here.

    await db.commit()

    # Invalidate user's permission cache
    await permission_cache.invalidate_user(request.user_id)

    return {
        "status": "completed",
        "user_id": request.user_id,
        "granted": granted_permissions,
        "already_granted": already_granted,
        "not_found": not_found,
        "granted_by": current_user.user_id,
        "total_granted": len(granted_permissions)
    }


@router.post("/revoke", status_code=status.HTTP_200_OK)
async def revoke_permission(
    request: RevokePermissionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_admin_permission)
):
    """
    Revoke permission from a user.
    """
    # Get permission
    permission = await get_permission_by_key(db, request.permission_key)

    # Find user permission
    result = await db.execute(
        select(ApiUserPermission).where(
            and_(
                ApiUserPermission.user_id == request.user_id,
                ApiUserPermission.permission_id == permission.id
            )
        )
    )
    user_perm = result.scalar_one_or_none()

    if not user_perm or not user_perm.granted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {request.user_id} does not have permission '{request.permission_key}'"
        )

    # Revoke permission
    user_perm.granted = False
    user_perm.revoked_by = current_user.user_id
    user_perm.revoked_at = datetime.utcnow()
    if request.notes:
        user_perm.notes = request.notes
    # Note: audit history is recorded automatically by the
    # `api_user_permissions_audit` DB trigger (see migration
    # 7c1e9a2f4b3d), no need to insert it here.

    await db.commit()

    # Invalidate user's permission cache
    await permission_cache.invalidate_user(request.user_id)

    return {
        "status": "revoked",
        "user_id": request.user_id,
        "permission_key": request.permission_key,
        "revoked_by": current_user.user_id
    }


@router.get("/audit", response_model=List[PermissionHistoryInfo])
async def get_permission_audit_log(
    user_id: Optional[int] = None,
    permission_key: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_admin_permission)
):
    """
    Get permission change audit log.

    - **user_id**: Filter by user ID
    - **permission_key**: Filter by permission key
    - **limit**: Max results to return (default: 100, max: 1000)
    - **offset**: Pagination offset
    """
    # Limit validation
    limit = min(limit, 1000)

    # Build query
    query = select(ApiUserPermissionHistory).order_by(desc(ApiUserPermissionHistory.changed_at))

    # Apply filters
    if user_id:
        query = query.where(ApiUserPermissionHistory.user_id == user_id)

    if permission_key:
        query = query.where(ApiUserPermissionHistory.permission_key == permission_key)

    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    history = result.scalars().all()

    return [
        PermissionHistoryInfo.model_validate(h)
        for h in history
    ]
