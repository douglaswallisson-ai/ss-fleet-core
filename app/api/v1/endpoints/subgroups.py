"""
Subgroup endpoints.
CRUD operations for subgroup management.
"""

from typing import List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db, get_db_read
from app.middleware.auth import AuthenticatedUser, require_permission
from app.models.subgroup import Subgroup
from app.schemas.subgroup import SubgroupCreate, SubgroupUpdate, SubgroupResponse

router = APIRouter()


@router.get("/", response_model=List[SubgroupResponse])
async def list_subgroups(
    group_id: int = Query(None, description="Filter by group_id"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("subgroups", "read"))
):
    """
    List all subgroups accessible by current user based on group/subgroup permissions.

    Access logic:
    - User sees subgroups where (group_id, subgroup_id) matches their access
    - Optional filter by group_id parameter

    Supports pagination via skip and limit parameters.
    """
    # Filter subgroups by user's accessible (group_id, subgroup_id) tuples
    if not current_user.group_access:
        # User has no group access - return empty
        return []

    # Build list of accessible (group_id, subgroup_id) pairs
    accessible_pairs = current_user.group_access

    # Extract all accessible subgroup IDs with their group_ids
    query = select(Subgroup).where(
        # Check if (group_id, id) is in accessible_pairs
        # Note: We need to check both group_id AND subgroup.id match the access tuple
        Subgroup.group_id.in_([gid for gid, _ in accessible_pairs])
    )

    # Additionally filter: subgroup.id must be in accessible subgroup_ids for that group
    # Build conditions: (group_id == X AND id == Y) OR (group_id == Z AND id == W)
    conditions = []
    for group_id_access, subgroup_id_access in accessible_pairs:
        conditions.append(
            (Subgroup.group_id == group_id_access) & (Subgroup.id == subgroup_id_access)
        )

    if conditions:
        from sqlalchemy import or_
        query = query.where(or_(*conditions))

    # Optional filter by group_id parameter
    if group_id is not None:
        query = query.where(Subgroup.group_id == group_id)

    query = query.order_by(Subgroup.date_add.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    subgroups = result.scalars().all()

    return subgroups


@router.get("/{subgroup_id}", response_model=SubgroupResponse)
async def get_subgroup(
    subgroup_id: int,
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("subgroups", "read"))
):
    """Get subgroup by ID (only if user has access to this subgroup's group)."""
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Get subgroup
    result = await db.execute(
        select(Subgroup).where(Subgroup.id == subgroup_id)
    )
    subgroup = result.scalar_one_or_none()

    if not subgroup:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subgroup not found"
        )

    # Check if user has access to this subgroup's group and subgroup
    access_pair = (subgroup.group_id, subgroup_id)
    if access_pair not in current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to this subgroup"
        )

    return subgroup


@router.post("/", response_model=SubgroupResponse, status_code=status.HTTP_201_CREATED)
async def create_subgroup(
    subgroup_data: SubgroupCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("subgroups", "create"))
):
    """
    Create a new subgroup in a group the user has access to.

    Note: User must have access to the parent group_id.
    """
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Validate user has access to target group
    # Extract accessible group_ids
    accessible_group_ids = list(set(gid for gid, _ in current_user.group_access))

    if subgroup_data.group_id not in accessible_group_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to target group"
        )

    # Check if client_cod already exists (if provided)
    if subgroup_data.client_cod:
        result = await db.execute(
            select(Subgroup).where(
                Subgroup.client_cod == subgroup_data.client_cod,
                Subgroup.group_id == subgroup_data.group_id
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Subgroup with this client_cod already exists in this group"
            )

    # Create subgroup
    subgroup = Subgroup(
        **subgroup_data.model_dump(),
        user_add=current_user.user_id
    )

    # Auto-set suspended_date if suspended=True
    if subgroup.suspended and not subgroup.suspended_date:
        subgroup.suspended_date = datetime.now()

    db.add(subgroup)
    await db.commit()
    await db.refresh(subgroup)

    return subgroup


@router.put("/{subgroup_id}", response_model=SubgroupResponse)
async def update_subgroup(
    subgroup_id: int,
    subgroup_data: SubgroupUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("subgroups", "update"))
):
    """Update subgroup by ID (only if user has access to this subgroup)."""
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Get existing subgroup
    result = await db.execute(
        select(Subgroup).where(Subgroup.id == subgroup_id)
    )
    subgroup = result.scalar_one_or_none()

    if not subgroup:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subgroup not found"
        )

    # Check if user has access to current subgroup
    current_access_pair = (subgroup.group_id, subgroup_id)
    if current_access_pair not in current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to this subgroup"
        )

    # If changing group_id, validate access to new group
    if subgroup_data.group_id and subgroup_data.group_id != subgroup.group_id:
        accessible_group_ids = list(set(gid for gid, _ in current_user.group_access))
        if subgroup_data.group_id not in accessible_group_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No access to target group"
            )

    # Check if new client_cod already exists (if provided)
    if subgroup_data.client_cod and subgroup_data.client_cod != subgroup.client_cod:
        target_group_id = subgroup_data.group_id or subgroup.group_id
        result = await db.execute(
            select(Subgroup).where(
                Subgroup.client_cod == subgroup_data.client_cod,
                Subgroup.group_id == target_group_id,
                Subgroup.id != subgroup_id
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Subgroup with this client_cod already exists in this group"
            )

    # Update fields
    update_data = subgroup_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(subgroup, field, value)

    # Auto-set suspended_date if suspended status changed
    if 'suspended' in update_data:
        if update_data['suspended'] and not subgroup.suspended_date:
            subgroup.suspended_date = datetime.now()
        elif not update_data['suspended']:
            subgroup.suspended_date = None

    # Set audit fields
    subgroup.user_modif = current_user.user_id
    subgroup.date_modif = datetime.now()

    await db.commit()
    await db.refresh(subgroup)

    return subgroup


@router.delete("/{subgroup_id}", response_model=SubgroupResponse)
async def delete_subgroup(
    subgroup_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("subgroups", "delete"))
):
    """
    Delete subgroup.

    Note: Subgroups don't have a status field in the database schema.
    This endpoint performs a HARD DELETE. Use with extreme caution!

    WARNING: This may cascade delete related vehicles, devices, etc.
    Consider implementing soft delete by adding a status field in the future.
    """
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Get existing subgroup
    result = await db.execute(
        select(Subgroup).where(Subgroup.id == subgroup_id)
    )
    subgroup = result.scalar_one_or_none()

    if not subgroup:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subgroup not found"
        )

    # Check if user has access to this subgroup
    access_pair = (subgroup.group_id, subgroup_id)
    if access_pair not in current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to this subgroup"
        )

    # WARNING: Hard delete (no soft delete in schema)
    # TODO: Consider adding status field for soft delete
    await db.delete(subgroup)
    await db.commit()

    return subgroup
