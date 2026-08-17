"""
VcmsUnitDevice schemas - Video monitoring device-vehicle associations.

Request/response schemas for video device association management.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class VcmsUnitDeviceBase(BaseModel):
    """Base schema for video device-vehicle associations."""
    unit_id: int = Field(..., description="Vehicle ID")
    device_id: int = Field(..., description="Video monitoring device ID")


class VcmsUnitDeviceCreate(VcmsUnitDeviceBase):
    """
    Schema for creating video device-vehicle association.

    Business Rules Enforced:
    - association_date is automatically set to NOW() (not provided in request)
    - user_id is automatically set from JWT token (not provided in request)
    - status is automatically set to 1 (active)
    """
    pass


class VcmsUnitDeviceUpdate(BaseModel):
    """Schema for updating video device-vehicle association."""
    association_date: Optional[datetime] = None
    release_date: Optional[datetime] = None
    status: Optional[int] = None

    @field_validator('release_date')
    @classmethod
    def validate_release_date(cls, v, info):
        """Validate that release_date is after association_date."""
        if v is not None and 'association_date' in info.data:
            association_date = info.data['association_date']
            if association_date and v <= association_date:
                raise ValueError('release_date must be after association_date')
        return v


class VcmsUnitDeviceResponse(VcmsUnitDeviceBase):
    """Schema for video device-vehicle association response."""
    id: int
    status: int
    user_id: int

    # Virtual properties from model
    is_active: bool = Field(..., description="Is association active (status=1)")
    is_deleted: bool = Field(..., description="Is association soft-deleted (status=-1)")
    is_currently_associated: bool = Field(..., description="Is currently associated (active and not released)")

    class Config:
        from_attributes = True


class VcmsUnitDeviceWithDetails(VcmsUnitDeviceResponse):
    """Schema for video device-vehicle association with vehicle and device details."""
    vehicle_label: str = Field(..., description="Vehicle label/plate")
    device_identifier: str = Field(..., description="Video device identifier")
    duration_days: Optional[int] = Field(None, description="Association duration in days")
