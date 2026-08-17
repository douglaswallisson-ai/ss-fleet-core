"""Pydantic schemas for TrackedUnitDevice model (vehicle-device associations)."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class TrackedUnitDeviceBase(BaseModel):
    """Base schema for vehicle-device association."""
    tracked_unit_id: int = Field(..., gt=0, description="Vehicle ID")
    device_id: int = Field(..., gt=0, description="Device ID")


class TrackedUnitDeviceCreate(TrackedUnitDeviceBase):
    """
    Schema for creating vehicle-device association.

    Business Rules Enforced:
    - association_date is automatically set to NOW() (not provided in request)
    - user_id is automatically set from JWT token (not provided in request)
    - device_primary is automatically set to 1 for active associations
    - status is automatically set to 1 (active)
    """
    pass


class TrackedUnitDeviceUpdate(BaseModel):
    """
    Schema for updating vehicle-device association.

    Common operations:
    - Release device: Set release_date = NOW, status = -1, device_primary = 0
    - Reactivate: Set release_date = NULL, status = 1, device_primary = 1
    """
    association_date: Optional[datetime] = Field(None, description="Update installation date")
    release_date: Optional[datetime] = Field(None, description="Set removal date (NULL = reactivate)")
    status: Optional[int] = Field(None, ge=-1, le=1, description="-1=deleted, 0=inactive, 1=active")
    device_primary: Optional[int] = Field(None, ge=0, le=1, description="0=secondary, 1=primary")

    @field_validator('release_date')
    @classmethod
    def validate_release_date(cls, v, info):
        """Ensure release_date is after association_date if both provided."""
        if v is not None and 'association_date' in info.data and info.data['association_date'] is not None:
            if v <= info.data['association_date']:
                raise ValueError('release_date must be after association_date')
        return v


class TrackedUnitDeviceResponse(BaseModel):
    """Schema for vehicle-device association response."""
    id: int
    tracked_unit_id: int
    device_id: int
    association_date: datetime
    release_date: Optional[datetime]
    status: int
    user_id: int
    device_primary: int

    # Virtual properties for convenience
    @property
    def is_active(self) -> bool:
        """Check if association is active."""
        return self.status == 1

    @property
    def is_deleted(self) -> bool:
        """Check if association is deleted."""
        return self.status == -1

    @property
    def is_currently_associated(self) -> bool:
        """Check if device is currently installed on vehicle."""
        return self.release_date is None and self.status == 1

    @property
    def is_primary(self) -> bool:
        """Check if this is the primary device."""
        return self.device_primary == 1

    model_config = {"from_attributes": True}


class TrackedUnitDeviceWithDetails(TrackedUnitDeviceResponse):
    """
    Extended response schema including related vehicle and device information.

    Useful for detailed views showing full context of the association.
    """
    vehicle_label: Optional[str] = Field(None, description="Vehicle plate/name")
    device_identifier: Optional[str] = Field(None, description="Device identifier")
    duration_days: Optional[int] = Field(None, description="Association duration in days")

    model_config = {"from_attributes": True}
