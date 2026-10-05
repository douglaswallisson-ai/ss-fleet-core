"""
TrackedUnitDevice endpoints - Device Associations management.

CRUD operations following system standards with specific business rules:
1. One-to-One active association (one vehicle = one active device)
2. Auto-manage device_primary flag (1 when active, 0 when inactive)
3. Date validation (association_date < release_date)
4. Access control through vehicle's group/subgroup
"""

from typing import List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.core.database import get_db, get_db_read
from app.core.access_control import build_group_subgroup_filter
from app.middleware.auth import AuthenticatedUser, require_permission
from app.models.tracked_unit_device import TrackedUnitDevice
from app.models.vehicle import Vehicle
from app.models.device import Device
from app.schemas.tracked_unit_device import (
    TrackedUnitDeviceCreate,
    TrackedUnitDeviceUpdate,
    TrackedUnitDeviceResponse,
    TrackedUnitDeviceWithDetails
)

router = APIRouter()


@router.get("/", response_model=List[TrackedUnitDeviceResponse])
async def list_associations(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    tracked_unit_id: int = Query(None, description="Filter by vehicle ID"),
    device_id: int = Query(None, description="Filter by device ID"),
    include_deleted: bool = Query(False, description="Include soft-deleted associations (status=-1)"),
    active_only: bool = Query(False, description="Only currently active associations (release_date is NULL)"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "read"))
):
    """
    List vehicle-device associations accessible by current user.

    Access control: User can only see associations for vehicles in their accessible groups/subgroups.
    """
    if not current_user.group_access:
        return []

    # Build access filter through vehicle's group/subgroup
    vehicle_access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    # Join with vehicle to apply access control
    query = (
        select(TrackedUnitDevice)
        .join(Vehicle, TrackedUnitDevice.tracked_unit_id == Vehicle.id)
        .where(vehicle_access_filter)
    )

    # Apply filters
    if tracked_unit_id:
        query = query.where(TrackedUnitDevice.tracked_unit_id == tracked_unit_id)

    if device_id:
        query = query.where(TrackedUnitDevice.device_id == device_id)

    if not include_deleted:
        query = query.where(TrackedUnitDevice.status != -1)

    if active_only:
        query = query.where(
            and_(
                TrackedUnitDevice.status == 1,
                TrackedUnitDevice.release_date.is_(None)
            )
        )

    query = query.order_by(TrackedUnitDevice.association_date.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    associations = result.scalars().all()

    return associations


@router.get("/{association_id}", response_model=TrackedUnitDeviceWithDetails)
async def get_association(
    association_id: int,
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "read"))
):
    """Get association by ID with vehicle and device details (only if user has access)."""
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
            TrackedUnitDevice,
            Vehicle.label.label('vehicle_label'),
            Device.identifier.label('device_identifier')
        )
        .join(Vehicle, TrackedUnitDevice.tracked_unit_id == Vehicle.id)
        .join(Device, TrackedUnitDevice.device_id == Device.id)
        .where(
            TrackedUnitDevice.id == association_id,
            vehicle_access_filter,
            TrackedUnitDevice.status != -1  # Exclude soft-deleted
        )
    )
    row = result.one_or_none()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Association not found"
        )

    association, vehicle_label, device_identifier = row

    # Build response with details
    response_data = {
        **TrackedUnitDeviceResponse.model_validate(association).model_dump(),
        "vehicle_label": vehicle_label,
        "device_identifier": device_identifier,
        "duration_days": association.duration_days() if hasattr(association, 'duration_days') else None
    }

    return TrackedUnitDeviceWithDetails(**response_data)


@router.post("/", response_model=TrackedUnitDeviceResponse, status_code=status.HTTP_201_CREATED)
async def create_association(
    association_data: TrackedUnitDeviceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "write"))
):
    """
    Create new vehicle-device association.

    Business Rules:
    1. Vehicle must exist and be active
    2. Device must exist and be active
    3. User must have access to vehicle's group/subgroup
    4. User must have access to device's group
    5. Vehicle cannot already have an active device
    6. Device cannot already be associated with another active vehicle
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
            Vehicle.id == association_data.tracked_unit_id,
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
            detail="Device not found or not active"
        )

    # Validate user has access to device's group
    accessible_group_ids = [group_id for group_id, subgroup_id in current_user.group_access]
    if device.group_id not in accessible_group_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to device's group"
        )

    # Check if vehicle already has an active device (1:1 rule)
    existing_vehicle_device = await db.execute(
        select(TrackedUnitDevice).where(
            TrackedUnitDevice.tracked_unit_id == association_data.tracked_unit_id,
            TrackedUnitDevice.status == 1,
            TrackedUnitDevice.release_date.is_(None)
        )
    )
    if existing_vehicle_device.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vehicle already has an active device associated. Release it first."
        )

    # Check if device is already associated with another active vehicle
    existing_device_vehicle = await db.execute(
        select(TrackedUnitDevice).where(
            TrackedUnitDevice.device_id == association_data.device_id,
            TrackedUnitDevice.status == 1,
            TrackedUnitDevice.release_date.is_(None)
        )
    )
    if existing_device_vehicle.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device is already associated with another vehicle. Release it first."
        )

    # Create association (auto-set association_date, device_primary=1, status=1)
    association = TrackedUnitDevice(
        **association_data.model_dump(),
        association_date=datetime.now(),  # Auto-set to NOW()
        user_id=current_user.user_id,
        status=1,
        device_primary=1  # Always 1 for active associations
    )

    db.add(association)
    await db.commit()
    await db.refresh(association)

    return association


@router.put("/{association_id}", response_model=TrackedUnitDeviceResponse)
async def update_association(
    association_id: int,
    association_data: TrackedUnitDeviceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "write"))
):
    """
    Update vehicle-device association.

    Common use cases:
    - Change association_date
    - Release device (set release_date, status=-1, device_primary=0)
    - Reactivate association (clear release_date, set status=1, device_primary=1)
    """
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Get association with access control
    vehicle_access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    result = await db.execute(
        select(TrackedUnitDevice)
        .join(Vehicle, TrackedUnitDevice.tracked_unit_id == Vehicle.id)
        .where(
            TrackedUnitDevice.id == association_id,
            vehicle_access_filter,
            TrackedUnitDevice.status != -1
        )
    )
    association = result.scalar_one_or_none()

    if not association:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Association not found"
        )

    # Apply updates
    update_data = association_data.model_dump(exclude_unset=True)

    # Enforce business rule: device_primary matches status
    if 'status' in update_data:
        if update_data['status'] == 1:
            update_data['device_primary'] = 1
        else:
            update_data['device_primary'] = 0

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
async def delete_association(
    association_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "delete"))
):
    """
    Soft delete association (sets status=-1, device_primary=0, release_date=NOW).

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
        select(TrackedUnitDevice)
        .join(Vehicle, TrackedUnitDevice.tracked_unit_id == Vehicle.id)
        .where(
            TrackedUnitDevice.id == association_id,
            vehicle_access_filter,
            TrackedUnitDevice.status != -1
        )
    )
    association = result.scalar_one_or_none()

    if not association:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Association not found"
        )

    # Soft delete
    association.status = -1
    association.device_primary = 0
    if association.release_date is None:
        association.release_date = datetime.now()

    await db.commit()

    return None
