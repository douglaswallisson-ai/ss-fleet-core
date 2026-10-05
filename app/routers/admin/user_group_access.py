"""
Admin User Group Access Management Endpoints

Endpoints for managing user group access (grant, revoke, list).
Controls which groups/subgroups each user can access.
Only accessible by users with admin.manage permission.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, delete
from pydantic import BaseModel, Field

from app.core.database import get_db, get_db_read
from app.middleware.auth import AuthenticatedUser, get_current_user
from app.models.user_group_access import UserGroupAccess
from app.models.user import User
from app.core.access_control import invalidate_user_groups_cache
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/user-group-access", tags=["Admin - User Group Access"])


# ========================================
# Pydantic Schemas
# ========================================

class UserGroupAccessResponse(BaseModel):
    """User group access entry response"""
    id: int
    user_id: int
    group_id: int
    subgroup_id: Optional[int]

    model_config = {"from_attributes": True}


class UserGroupAccessCreate(BaseModel):
    """Request to grant group access to a user"""
    group_id: int = Field(..., description="Group ID to grant access to")
    subgroup_id: Optional[int] = Field(None, description="Subgroup ID (NULL means access to all subgroups in group)")


class UserGroupAccessBatchCreate(BaseModel):
    """Request to grant multiple group accesses to a user"""
    accesses: List[UserGroupAccessCreate] = Field(..., description="List of group/subgroup accesses to grant")


class UserGroupAccessSummary(BaseModel):
    """Summary of user's group access"""
    user_id: int
    user_email: Optional[str]
    total_accesses: int
    groups: List[int]
    accesses: List[UserGroupAccessResponse]


# ========================================
# Helper Functions
# ========================================

async def require_admin_permission(
    current_user: AuthenticatedUser = Depends(get_current_user)
) -> AuthenticatedUser:
    """
    Dependency to ensure user has admin.manage permission.
    """
    if not current_user.has_permission("admin", "manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions: admin.manage required"
        )
    return current_user


async def get_user_or_404(db: AsyncSession, user_id: int) -> User:
    """Get user by ID or raise 404"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found"
        )

    return user


def validate_admin_has_group_access(
    current_user: AuthenticatedUser,
    group_id: int,
    subgroup_id: Optional[int] = None
) -> None:
    """
    Validate that admin has access to the group/subgroup they're trying to grant.

    Security Rule: Admins can only grant access to groups/subgroups they themselves have access to.
    This prevents privilege escalation where an admin could grant access to resources
    they don't control.

    Exception: Super admins (user_mova=1) can grant access to any group/subgroup.

    Args:
        current_user: The authenticated admin user
        group_id: Group ID being granted
        subgroup_id: Optional subgroup ID being granted

    Raises:
        HTTPException 403: If admin doesn't have access to the group/subgroup
    """
    # Super admins (user_mova=1) can grant access to any group/subgroup
    if current_user.is_super_admin:
        logger.info(
            "super_admin_bypass",
            admin_id=current_user.user_id,
            group_id=group_id,
            subgroup_id=subgroup_id
        )
        return  # Bypass validation

    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You have no group access configured"
        )

    # Extract admin's accessible groups
    admin_groups = {g for g, s in current_user.group_access if g is not None}

    # Check if admin has access to the group
    if group_id not in admin_groups:
        logger.warning(
            "admin_group_access_denied",
            admin_id=current_user.user_id,
            requested_group=group_id,
            admin_groups=list(admin_groups)
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You don't have access to group {group_id}"
        )

    # If granting specific subgroup, validate subgroup access
    if subgroup_id is not None:
        # Check if admin has full group access (subgroup_id=NULL) or specific subgroup access
        admin_access_tuples = set(current_user.group_access)
        has_full_group_access = (group_id, None) in admin_access_tuples
        has_specific_subgroup = (group_id, subgroup_id) in admin_access_tuples

        if not has_full_group_access and not has_specific_subgroup:
            logger.warning(
                "admin_subgroup_access_denied",
                admin_id=current_user.user_id,
                requested_group=group_id,
                requested_subgroup=subgroup_id,
                admin_access=list(admin_access_tuples)
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"You don't have access to subgroup {subgroup_id} in group {group_id}"
            )


# ========================================
# Endpoints
# ========================================

@router.get("/users/{user_id}", response_model=UserGroupAccessSummary)
async def list_user_group_access(
    user_id: int,
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_admin_permission)
):
    """
    List all group accesses for a specific user.

    Returns:
        - Total number of access entries
        - List of unique group IDs
        - Full access details (group_id, subgroup_id)
    """
    # Verify user exists
    user = await get_user_or_404(db, user_id)

    # Get all access entries
    result = await db.execute(
        select(UserGroupAccess)
        .where(UserGroupAccess.user_id == user_id)
        .order_by(UserGroupAccess.group_id, UserGroupAccess.subgroup_id)
    )
    accesses = result.scalars().all()

    # Extract unique groups
    unique_groups = list(set(a.group_id for a in accesses if a.group_id is not None))

    return UserGroupAccessSummary(
        user_id=user_id,
        user_email=user.email,
        total_accesses=len(accesses),
        groups=sorted(unique_groups),
        accesses=[UserGroupAccessResponse.model_validate(a) for a in accesses]
    )


@router.post("/users/{user_id}", response_model=UserGroupAccessResponse, status_code=status.HTTP_201_CREATED)
async def grant_user_group_access(
    user_id: int,
    request: UserGroupAccessCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_admin_permission)
):
    """
    Grant group access to a user.

    - **group_id**: Group to grant access to
    - **subgroup_id**: Optional subgroup (NULL means access to all subgroups in group)

    Security: Admin can only grant access to groups/subgroups they have access to.
    Note: If an identical access entry already exists, returns 409 Conflict.
    """
    # Verify user exists
    await get_user_or_404(db, user_id)

    # Security: Validate admin has access to the group/subgroup being granted
    validate_admin_has_group_access(current_user, request.group_id, request.subgroup_id)

    # Check if access already exists
    existing = await db.execute(
        select(UserGroupAccess).where(
            and_(
                UserGroupAccess.user_id == user_id,
                UserGroupAccess.group_id == request.group_id,
                UserGroupAccess.subgroup_id == request.subgroup_id if request.subgroup_id else UserGroupAccess.subgroup_id.is_(None)
            )
        )
    )
    existing_access = existing.scalar_one_or_none()

    if existing_access:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User {user_id} already has access to group {request.group_id}" +
                   (f" subgroup {request.subgroup_id}" if request.subgroup_id else " (all subgroups)")
        )

    # Create new access
    new_access = UserGroupAccess(
        user_id=user_id,
        group_id=request.group_id,
        subgroup_id=request.subgroup_id
    )

    db.add(new_access)
    await db.commit()
    await db.refresh(new_access)

    # Invalidate user's group access cache
    await invalidate_user_groups_cache(user_id)

    logger.info(
        "user_group_access_granted",
        user_id=user_id,
        group_id=request.group_id,
        subgroup_id=request.subgroup_id,
        granted_by=current_user.user_id
    )

    return new_access


@router.post("/users/{user_id}/batch", status_code=status.HTTP_201_CREATED)
async def grant_user_group_access_batch(
    user_id: int,
    request: UserGroupAccessBatchCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_admin_permission)
):
    """
    Grant multiple group accesses to a user in a single request.

    Security: Admin can only grant access to groups/subgroups they have access to.
    Returns summary of granted, skipped (already exists), and denied (no access) entries.
    """
    # Verify user exists
    await get_user_or_404(db, user_id)

    granted = []
    skipped = []
    denied = []

    for access_req in request.accesses:
        # Security: Check if admin has access to this group/subgroup
        try:
            validate_admin_has_group_access(current_user, access_req.group_id, access_req.subgroup_id)
        except HTTPException:
            denied.append({
                "group_id": access_req.group_id,
                "subgroup_id": access_req.subgroup_id,
                "reason": "no_admin_access"
            })
            continue
        # Check if access already exists
        existing = await db.execute(
            select(UserGroupAccess).where(
                and_(
                    UserGroupAccess.user_id == user_id,
                    UserGroupAccess.group_id == access_req.group_id,
                    UserGroupAccess.subgroup_id == access_req.subgroup_id if access_req.subgroup_id else UserGroupAccess.subgroup_id.is_(None)
                )
            )
        )
        existing_access = existing.scalar_one_or_none()

        if existing_access:
            skipped.append({
                "group_id": access_req.group_id,
                "subgroup_id": access_req.subgroup_id,
                "reason": "already_exists"
            })
            continue

        # Create new access
        new_access = UserGroupAccess(
            user_id=user_id,
            group_id=access_req.group_id,
            subgroup_id=access_req.subgroup_id
        )
        db.add(new_access)
        granted.append({
            "group_id": access_req.group_id,
            "subgroup_id": access_req.subgroup_id
        })

    await db.commit()

    # Invalidate user's group access cache (only if something was granted)
    if granted:
        await invalidate_user_groups_cache(user_id)

    logger.info(
        "user_group_access_batch_granted",
        user_id=user_id,
        granted_count=len(granted),
        skipped_count=len(skipped),
        denied_count=len(denied),
        granted_by=current_user.user_id
    )

    return {
        "status": "completed",
        "user_id": user_id,
        "granted": granted,
        "skipped": skipped,
        "denied": denied,
        "total_granted": len(granted),
        "total_skipped": len(skipped),
        "total_denied": len(denied)
    }


@router.delete("/users/{user_id}/{access_id}", response_model=UserGroupAccessResponse)
async def revoke_user_group_access(
    user_id: int,
    access_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_admin_permission)
):
    """
    Revoke a specific group access from a user.

    - **user_id**: User ID
    - **access_id**: Access entry ID to revoke
    """
    # Verify user exists
    await get_user_or_404(db, user_id)

    # Get access entry
    result = await db.execute(
        select(UserGroupAccess).where(
            and_(
                UserGroupAccess.id == access_id,
                UserGroupAccess.user_id == user_id
            )
        )
    )
    access = result.scalar_one_or_none()

    if not access:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Access entry {access_id} not found for user {user_id}"
        )

    # Security: Validate admin has access to the group/subgroup being revoked
    validate_admin_has_group_access(current_user, access.group_id, access.subgroup_id)

    # Store for response before deletion
    response = UserGroupAccessResponse.model_validate(access)

    # Delete access
    await db.delete(access)
    await db.commit()

    # Invalidate user's group access cache
    await invalidate_user_groups_cache(user_id)

    logger.info(
        "user_group_access_revoked",
        user_id=user_id,
        access_id=access_id,
        group_id=access.group_id,
        subgroup_id=access.subgroup_id,
        revoked_by=current_user.user_id
    )

    return response


@router.delete("/users/{user_id}/group/{group_id}")
async def revoke_user_group_access_by_group(
    user_id: int,
    group_id: int,
    subgroup_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_admin_permission)
):
    """
    Revoke group access from a user by group_id.

    - **user_id**: User ID
    - **group_id**: Group ID to revoke
    - **subgroup_id**: Optional subgroup ID. If not provided, revokes ALL accesses for the group.

    Security: Admin can only revoke access to groups/subgroups they have access to.
    """
    # Verify user exists
    await get_user_or_404(db, user_id)

    # Security: Validate admin has access to the group being revoked
    # For "revoke all subgroups in group", admin needs at least group-level access
    validate_admin_has_group_access(current_user, group_id, subgroup_id)

    # Build delete query
    if subgroup_id is not None:
        # Delete specific subgroup access
        delete_query = delete(UserGroupAccess).where(
            and_(
                UserGroupAccess.user_id == user_id,
                UserGroupAccess.group_id == group_id,
                UserGroupAccess.subgroup_id == subgroup_id
            )
        )
    else:
        # Delete ALL accesses for this group
        delete_query = delete(UserGroupAccess).where(
            and_(
                UserGroupAccess.user_id == user_id,
                UserGroupAccess.group_id == group_id
            )
        )

    result = await db.execute(delete_query)
    deleted_count = result.rowcount

    if deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No access found for user {user_id} to group {group_id}" +
                   (f" subgroup {subgroup_id}" if subgroup_id else "")
        )

    await db.commit()

    # Invalidate user's group access cache
    await invalidate_user_groups_cache(user_id)

    logger.info(
        "user_group_access_revoked_by_group",
        user_id=user_id,
        group_id=group_id,
        subgroup_id=subgroup_id,
        deleted_count=deleted_count,
        revoked_by=current_user.user_id
    )

    return {
        "status": "revoked",
        "user_id": user_id,
        "group_id": group_id,
        "subgroup_id": subgroup_id,
        "deleted_count": deleted_count,
        "revoked_by": current_user.user_id
    }


@router.delete("/users/{user_id}/all")
async def revoke_all_user_group_access(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_admin_permission)
):
    """
    Revoke ALL group accesses from a user.

    WARNING: This removes all group access for the user!

    Security: Admin can only revoke accesses for groups they have access to.
    If user has access to groups the admin doesn't have access to, those will NOT be revoked.
    Exception: Super admins (user_mova=1) can revoke ALL accesses.
    """
    # Verify user exists
    await get_user_or_404(db, user_id)

    # Super admins can delete all accesses
    if current_user.is_super_admin:
        logger.info(
            "super_admin_revoke_all",
            admin_id=current_user.user_id,
            target_user_id=user_id
        )
        result = await db.execute(
            delete(UserGroupAccess).where(UserGroupAccess.user_id == user_id)
        )
        deleted_count = result.rowcount
    else:
        # Regular admins: only delete accesses for groups they have access to
        if not current_user.group_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You have no group access configured"
            )

        admin_groups = {g for g, s in current_user.group_access if g is not None}

        result = await db.execute(
            delete(UserGroupAccess).where(
                and_(
                    UserGroupAccess.user_id == user_id,
                    UserGroupAccess.group_id.in_(admin_groups)
                )
            )
        )
        deleted_count = result.rowcount

    await db.commit()

    # Invalidate user's group access cache
    await invalidate_user_groups_cache(user_id)

    logger.warning(
        "user_group_access_all_revoked",
        user_id=user_id,
        deleted_count=deleted_count,
        revoked_by=current_user.user_id
    )

    return {
        "status": "all_revoked",
        "user_id": user_id,
        "deleted_count": deleted_count,
        "revoked_by": current_user.user_id
    }
