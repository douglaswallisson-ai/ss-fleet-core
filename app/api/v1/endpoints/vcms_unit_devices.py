"""
VcmsUnitDevice endpoints - Video monitoring device-vehicle association management.

CRUD operations following system standards with specific business rules:
1. One-to-One active association (one vehicle = one active video device)
2. Date validation (association_date < release_date)
3. Access control through vehicle's group/subgroup
4. Cascade delete when parent entities are removed
"""

from typing import List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.core.database import get_db, get_db_read
from app.core.access_control import build_group_subgroup_filter
from app.middleware.auth import AuthenticatedUser, require_permission
from app.models.vcms_unit_device import VcmsUnitDevice
from app.models.vehicle import Vehicle
from app.models.device import Device
from app.schemas.vcms_unit_device import (
    VcmsUnitDeviceCreate,
    VcmsUnitDeviceUpdate,
    VcmsUnitDeviceResponse,
    VcmsUnitDeviceWithDetails
)

router = APIRouter()


@router.get("/", response_model=List[VcmsUnitDeviceResponse])
async def list_video_associations(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    unit_id: int = Query(None, description="Filter by vehicle ID"),
    device_id: int = Query(None, description="Filter by video device ID"),
    include_deleted: bool = Query(False, description="Include soft-deleted associations (status=-1)"),
    active_only: bool = Query(False, description="Only currently active associations (release_date is NULL)"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "read"))
):
    """
    List video device-vehicle associations accessible by current user.

    Access control: User can only see associations for vehicles in their accessible groups/subgroups.
    """
    if not current_user.group_access:
        return []

    # Build access filter through vehicle's group/subgroup
    vehicle_access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    # Join with vehicle to apply access control
    query = (
        select(VcmsUnitDevice)
        .join(Vehicle, VcmsUnitDevice.unit_id == Vehicle.id)
        .where(vehicle_access_filter)
    )

    # Apply filters
    if unit_id:
        query = query.where(VcmsUnitDevice.unit_id == unit_id)

    if device_id:
        query = query.where(VcmsUnitDevice.device_id == device_id)

    if not include_deleted:
        query = query.where(VcmsUnitDevice.status != -1)

    if active_only:
        query = query.where(
            and_(
                VcmsUnitDevice.status == 1,
                VcmsUnitDevice.release_date.is_(None)
            )
        )

    query = query.order_by(VcmsUnitDevice.association_date.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    associations = result.scalars().all()

    return associations


@router.get("/{association_id}", response_model=VcmsUnitDeviceWithDetails)
async def get_video_association(
    association_id: int,
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "read"))
):
    """Get video device association by ID with vehicle and device details (only if user has access)."""
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Build access filter through vehicle
    vehicle_access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    # Get association with vehicle and device details
    result = await db.execute(
        select(
            VcmsUnitDevice,
            Vehicle.label.label('vehicle_label'),
            Device.identifier.label('device_identifier')
        )
        .join(Vehicle, VcmsUnitDevice.unit_id == Vehicle.id)
        .join(Device, VcmsUnitDevice.device_id == Device.id)
        .where(
            VcmsUnitDevice.id == association_id,
            vehicle_access_filter,
            VcmsUnitDevice.status != -1  # Exclude soft-deleted
        )
    )
    row = result.one_or_none()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video device association not found"
        )

    association, vehicle_label, device_identifier = row

    # Build response with details
    response_data = {
        **VcmsUnitDeviceResponse.model_validate(association).model_dump(),
        "vehicle_label": vehicle_label,
        "device_identifier": device_identifier,
        "duration_days": association.duration_days() if hasattr(association, 'duration_days') else None
    }

    return VcmsUnitDeviceWithDetails(**response_data)


@router.post("/", response_model=VcmsUnitDeviceResponse, status_code=status.HTTP_201_CREATED)
async def create_video_association(
    association_data: VcmsUnitDeviceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "write"))
):
    """
    Create new video device-vehicle association.

    Business Rules:
    1. Vehicle must exist and be active
    2. Video device must exist and be active
    3. User must have access to vehicle's group/subgroup
    4. User must have access to device's group
    5. Vehicle cannot already have an active video device
    6. Video device cannot already be associated with another active vehicle
    """
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Validate vehicle exists, is active, and user has access
    vehicle_access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)
    vehicle_result = await db.execute(
        select(Vehicle).where(
            Vehicle.id == association_data.unit_id,
            vehicle_access_filter,
            Vehicle.status == 1
        )
    )
    vehicle = vehicle_result.scalar_one_or_none()

    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehicle not found or not active"
        )

    # Validate device exists and is active
    device_result = await db.execute(
        select(Device).where(
            Device.id == association_data.device_id,
            Device.status == 1
        )
    )
    device = device_result.scalar_one_or_none()

    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video device not found or not active"
        )

    # Validate user has access to device's group
    accessible_group_ids = [group_id for group_id, subgroup_id in current_user.group_access]
    if device.group_id not in accessible_group_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to device's group"
        )

    # Check if vehicle already has an active video device (1:1 rule)
    existing_vehicle_device = await db.execute(
        select(VcmsUnitDevice).where(
            VcmsUnitDevice.unit_id == association_data.unit_id,
            VcmsUnitDevice.status == 1,
            VcmsUnitDevice.release_date.is_(None)
        )
    )
    if existing_vehicle_device.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vehicle already has an active video device associated. Release it first."
        )

    # Check if device is already associated with another active vehicle
    existing_device_vehicle = await db.execute(
        select(VcmsUnitDevice).where(
            VcmsUnitDevice.device_id == association_data.device_id,
            VcmsUnitDevice.status == 1,
            VcmsUnitDevice.release_date.is_(None)
        )
    )
    if existing_device_vehicle.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Video device is already associated with another vehicle. Release it first."
        )

    # Create association (auto-set association_date, status=1)
    association = VcmsUnitDevice(
        **association_data.model_dump(),
        association_date=datetime.now(),  # Auto-set to NOW()
        user_id=current_user.user_id,
        status=1
    )

    db.add(association)
    await db.commit()
    await db.refresh(association)

    return association


@router.put("/{association_id}", response_model=VcmsUnitDeviceResponse)
async def update_video_association(
    association_id: int,
    association_data: VcmsUnitDeviceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "write"))
):
    """
    Update video device-vehicle association.

    Common use cases:
    - Change association_date
    - Release device (set release_date, status=-1)
    - Reactivate association (clear release_date, set status=1)
    """
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Get association with access control
    vehicle_access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    result = await db.execute(
        select(VcmsUnitDevice)
        .join(Vehicle, VcmsUnitDevice.unit_id == Vehicle.id)
        .where(
            VcmsUnitDevice.id == association_id,
            vehicle_access_filter,
            VcmsUnitDevice.status != -1
        )
    )
    association = result.scalar_one_or_none()

    if not association:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video device association not found"
        )

    # Apply updates
    update_data = association_data.model_dump(exclude_unset=True)

    # If setting status to -1 and release_date not set, auto-set it
    if update_data.get('status') == -1 and association.release_date is None:
        if 'release_date' not in update_data or update_data['release_date'] is None:
            update_data['release_date'] = datetime.now()

    # Validate dates if both provided
    if 'association_date' in update_data and 'release_date' in update_data:
        if update_data['release_date'] and update_data['release_date'] <= update_data['association_date']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="release_date must be after association_date"
            )

    # Apply updates
    for key, value in update_data.items():
        setattr(association, key, value)

    await db.commit()
    await db.refresh(association)

    return association


@router.delete("/{association_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_video_association(
    association_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "delete"))
):
    """
    Soft delete video device association (sets status=-1, release_date=NOW).

    Note: This is a soft delete. The record remains in the database for audit trail.
    """
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Get association with access control
    vehicle_access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    result = await db.execute(
        select(VcmsUnitDevice)
        .join(Vehicle, VcmsUnitDevice.unit_id == Vehicle.id)
        .where(
            VcmsUnitDevice.id == association_id,
            vehicle_access_filter,
            VcmsUnitDevice.status != -1
        )
    )
    association = result.scalar_one_or_none()

    if not association:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video device association not found"
        )

    # Soft delete
    association.status = -1
    if association.release_date is None:
        association.release_date = datetime.now()

    await db.commit()

    return None
