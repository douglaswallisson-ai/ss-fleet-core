"""
Group endpoints.
CRUD operations for group (client) management.
"""

from typing import List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db, get_db_read
from app.middleware.auth import AuthenticatedUser, require_permission
from app.models.group import Group
from app.schemas.group import GroupCreate, GroupUpdate, GroupResponse

router = APIRouter()


@router.get("/", response_model=List[GroupResponse])
async def list_groups(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("groups", "read"))
):
    """
    List all groups accessible by current user based on group permissions.

    Access logic:
    - User sees groups where group_id matches their access
    - Extracts unique group_ids from user.group_access tuples

    Supports pagination via skip and limit parameters.
    """
    # Filter groups by user's accessible group_ids
    if not current_user.group_access:
        # User has no group access - return empty
        return []

    # Extract unique group_ids from access tuples [(group_id, subgroup_id), ...]
    accessible_group_ids = list(set(group_id for group_id, _ in current_user.group_access))

    result = await db.execute(
        select(Group)
        .where(Group.id.in_(accessible_group_ids))
        .order_by(Group.date_add.desc())
        .offset(skip)
        .limit(limit)
    )
    groups = result.scalars().all()

    return groups


@router.get("/{group_id}", response_model=GroupResponse)
async def get_group(
    group_id: int,
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("groups", "read"))
):
    """Get group by ID (only if user has access to this group)."""
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Extract accessible group_ids
    accessible_group_ids = list(set(group_id for group_id, _ in current_user.group_access))

    # Check if user has access to this group
    if group_id not in accessible_group_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to this group"
        )

    result = await db.execute(
        select(Group).where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found"
        )

    return group


@router.post("/", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    group_data: GroupCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("groups", "create"))
):
    """
    Create a new group.

    Note: Admin-only operation typically. Regular users may not have permission.
    """
    # Check if client_cod already exists (if provided)
    if group_data.client_cod:
        result = await db.execute(
            select(Group).where(Group.client_cod == group_data.client_cod)
        )
        existing = result.scalar_one_or_none()

        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Group with this client_cod already exists"
            )

    # Create group
    group = Group(
        **group_data.model_dump(),
        user_add=current_user.user_id
    )

    db.add(group)
    await db.commit()
    await db.refresh(group)

    return group


@router.put("/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: int,
    group_data: GroupUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("groups", "update"))
):
    """Update group by ID (only if user has access to this group)."""
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Extract accessible group_ids
    accessible_group_ids = list(set(gid for gid, _ in current_user.group_access))

    # Check if user has access to this group
    if group_id not in accessible_group_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to this group"
        )

    # Get existing group
    result = await db.execute(
        select(Group).where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found"
        )

    # Check if new client_cod already exists (if provided)
    if group_data.client_cod and group_data.client_cod != group.client_cod:
        result = await db.execute(
            select(Group).where(
                Group.client_cod == group_data.client_cod,
                Group.id != group_id
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Group with this client_cod already exists"
            )

    # Update fields
    update_data = group_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(group, field, value)

    # Set audit fields
    group.user_modif = current_user.user_id
    group.date_modif = datetime.now()

    await db.commit()
    await db.refresh(group)

    return group


@router.delete("/{group_id}", response_model=GroupResponse)
async def delete_group(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("groups", "delete"))
):
    """
    Delete group (admin-only operation).

    Note: Groups don't have a status field in the database schema.
    This endpoint performs a HARD DELETE. Use with extreme caution!

    WARNING: This will cascade delete related subgroups, vehicles, etc.
    Consider implementing soft delete by adding a status field in the future.
    """
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Extract accessible group_ids
    accessible_group_ids = list(set(gid for gid, _ in current_user.group_access))

    # Check if user has access to this group
    if group_id not in accessible_group_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to this group"
        )

    # Get existing group
    result = await db.execute(
        select(Group).where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found"
        )

    # WARNING: Hard delete (no soft delete in schema)
    # TODO: Consider adding status field for soft delete
    await db.delete(group)
    await db.commit()

    return group
