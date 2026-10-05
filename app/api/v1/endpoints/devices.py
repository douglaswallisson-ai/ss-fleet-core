"""
Device endpoints.
CRUD operations for device management following system standards.
"""

from typing import List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db, get_db_read
from app.core.access_control import build_group_subgroup_filter
from app.middleware.auth import AuthenticatedUser, require_permission
from app.models.device import Device
from app.models.tracked_unit_device import TrackedUnitDevice
from app.models.vcms_unit_device import VcmsUnitDevice
from app.schemas.device import DeviceCreate, DeviceUpdate, DeviceResponse

router = APIRouter()


@router.get("/", response_model=List[DeviceResponse])
async def list_devices(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    include_deleted: bool = Query(False, description="Include soft-deleted devices (status=-1)"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("devices", "read"))
):
    """
    List all devices accessible by current user based on group permissions.

    Access logic:
    - User sees devices where group_id matches
    - Devices have NO subgroup_id (group-only access control)
    - Excludes soft-deleted (status=-1) by default

    Supports pagination via skip and limit parameters.
    """
    if not current_user.group_access:
        return []

    # Use middleware to build access filter (automatically detects no subgroup_id)
    access_filter = build_group_subgroup_filter(current_user.group_access)(Device)

    query = select(Device).where(access_filter)

    # Filter out soft-deleted unless explicitly requested
    if not include_deleted:
        query = query.where(Device.status != -1)

    query = query.order_by(Device.date_add.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    devices = result.scalars().all()

    return devices


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: int,
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("devices", "read"))
):
    """Get device by ID (only if user has access to device's group)."""
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Use middleware to build access filter
    access_filter = build_group_subgroup_filter(current_user.group_access)(Device)

    result = await db.execute(
        select(Device).where(
            Device.id == device_id,
            access_filter,
            Device.status != -1  # Exclude soft-deleted
        )
    )
    device = result.scalar_one_or_none()

    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found"
        )

    return device


@router.post("/", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
async def create_device(
    device_data: DeviceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("devices", "create"))
):
    """Create a new device in a group the user has access to."""
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Validate user has access to target group
    accessible_group_ids = [group_id for group_id, subgroup_id in current_user.group_access]
    if device_data.group_id not in accessible_group_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to target group"
        )

    # Check if device with same identifier and device_model_id already exists (active only)
    # Constraint: identifier unique per (device_model_id, status != -1)
    result = await db.execute(
        select(Device).where(
            Device.identifier == device_data.identifier,
            Device.device_model_id == device_data.device_model_id,
            Device.status != -1  # Only check active devices
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Device with identifier '{device_data.identifier}' and model ID {device_data.device_model_id} already exists"
        )

    # Generate internal_id: device_model_id + identifier
    internal_id = f"{device_data.device_model_id}_{device_data.identifier}"

    device = Device(
        **device_data.model_dump(),
        account_id=539,  # Fixed account_id
        internal_id=internal_id,
        status=1,  # Active by default
        user_add=current_user.user_id
    )

    db.add(device)
    await db.commit()
    await db.refresh(device)

    return device


@router.put("/{device_id}", response_model=DeviceResponse)
async def update_device(
    device_id: int,
    device_data: DeviceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("devices", "update"))
):
    """Update device by ID (only if user has access to device's group)."""
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Use middleware to build access filter
    access_filter = build_group_subgroup_filter(current_user.group_access)(Device)

    # Get device (with group access check, exclude soft-deleted)
    result = await db.execute(
        select(Device).where(
            Device.id == device_id,
            access_filter,
            Device.status != -1
        )
    )
    device = result.scalar_one_or_none()

    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found"
        )

    # Check for duplicate if changing identifier or device_model_id
    update_data = device_data.model_dump(exclude_unset=True)

    # If changing group_id, validate user has access to new group
    if 'group_id' in update_data:
        accessible_group_ids = [group_id for group_id, subgroup_id in current_user.group_access]
        if update_data['group_id'] not in accessible_group_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No access to target group"
            )

    if 'identifier' in update_data or 'device_model_id' in update_data:
        new_identifier = update_data.get('identifier', device.identifier)
        new_model_id = update_data.get('device_model_id', device.device_model_id)

        # Only check if actually changing
        if new_identifier != device.identifier or new_model_id != device.device_model_id:
            dup_result = await db.execute(
                select(Device).where(
                    Device.identifier == new_identifier,
                    Device.device_model_id == new_model_id,
                    Device.status != -1,
                    Device.id != device_id  # Exclude current device
                )
            )
            duplicate = dup_result.scalar_one_or_none()

            if duplicate:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Device with identifier '{new_identifier}' and model ID {new_model_id} already exists"
                )

            # Update internal_id if identifier or model changed
            update_data['internal_id'] = f"{new_model_id}_{new_identifier}"

    # Update fields
    for field, value in update_data.items():
        setattr(device, field, value)

    # Update audit fields
    device.user_modif = current_user.user_id
    device.date_modif = datetime.now()

    await db.commit()
    await db.refresh(device)

    return device


@router.delete("/{device_id}", response_model=DeviceResponse)
async def delete_device(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_permission("devices", "delete"))
):
    """
    Soft delete device by ID (only if user has access to device's group).
    Sets status to -1 and updates removal audit fields.
    """
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Use middleware to build access filter
    access_filter = build_group_subgroup_filter(current_user.group_access)(Device)

    result = await db.execute(
        select(Device).where(
            Device.id == device_id,
            access_filter,
            Device.status != -1  # Cannot delete already deleted
        )
    )
    device = result.scalar_one_or_none()

    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found"
        )

    # Cascade soft delete: Release any active vehicle associations
    now = datetime.now()

    # Update any active TrackedUnitDevice associations (status=1) for this device
    associations_result = await db.execute(
        select(TrackedUnitDevice).where(
            TrackedUnitDevice.device_id == device_id,
            TrackedUnitDevice.status == 1
        )
    )
    active_associations = associations_result.scalars().all()

    for association in active_associations:
        association.status = -1
        association.device_primary = 0
        if association.release_date is None:
            association.release_date = now

    # Update any active VcmsUnitDevice associations (video devices)
    vcms_associations_result = await db.execute(
        select(VcmsUnitDevice).where(
            VcmsUnitDevice.device_id == device_id,
            VcmsUnitDevice.status == 1
        )
    )
    active_vcms_associations = vcms_associations_result.scalars().all()

    for association in active_vcms_associations:
        association.status = -1
        if association.release_date is None:
            association.release_date = now

    # Soft delete: update status and audit fields
    device.status = -1
    device.user_modif = current_user.user_id
    device.date_modif = now
    device.user_removed = current_user.user_id
    device.date_removed = now

    await db.commit()
    await db.refresh(device)

    return device
