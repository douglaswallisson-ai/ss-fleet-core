"""
Device schemas for request/response validation.
Follows the same pattern as vehicle schemas.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class DeviceBase(BaseModel):
    """Base device schema with common fields."""

    # Required fields
    identifier: str = Field(..., min_length=1, max_length=255, description="Device identifier (unique per model+status)")
    device_model_id: int = Field(..., gt=0, description="Device model ID (required)")

    # Optional technical fields
    operadora: Optional[str] = Field(None, max_length=255, description="Network operator/carrier")
    number: Optional[str] = Field(None, max_length=255, description="Phone number")
    imei: Optional[str] = Field(None, max_length=255, description="IMEI number")
    iccid: Optional[str] = Field(None, max_length=255, description="SIM card ICCID")
    serial_number: Optional[str] = Field(None, max_length=255, description="Device serial number")
    modem: Optional[str] = Field(None, max_length=255, description="Modem information")

    # Optional reference fields
    asset: Optional[int] = Field(None, description="Associated tracked unit/vehicle ID")
    device_type: Optional[int] = Field(None, description="Device type classification")
    device_model_version_id: Optional[int] = Field(None, description="Device model version ID")
    current_product_id: Optional[int] = Field(None, description="Current product/plan ID")
    customer_id: Optional[int] = Field(None, description="Customer ID")
    manufacturing_date: Optional[datetime] = Field(None, description="Manufacturing date")


class DeviceCreate(DeviceBase):
    """
    Schema for creating a new device.

    User must provide group_id and must have access to it.
    Auto-generated fields:
    - internal_id: device_model_id + identifier
    - account_id, user_add, date_add: set automatically by the system
    - status: 1 (active)
    """
    group_id: int = Field(..., description="Group ID (user must have access)")

    @field_validator('identifier')
    @classmethod
    def identifier_not_empty(cls, v: str) -> str:
        """Validate identifier is not empty after strip."""
        if not v or not v.strip():
            raise ValueError('Identifier cannot be empty')
        return v.strip()

    class Config:
        json_schema_extra = {
            "example": {
                "identifier": "DEV001",
                "device_model_id": 1,
                "group_id": 14330,
                "imei": "123456789012345",
                "serial_number": "SN123456",
                "operadora": "Vivo",
                "number": "+5511999999999"
            }
        }


class DeviceUpdate(BaseModel):
    """Schema for updating an existing device (all fields optional)."""

    identifier: Optional[str] = Field(None, min_length=1, max_length=255)
    device_model_id: Optional[int] = Field(None, gt=0)

    # Technical fields
    operadora: Optional[str] = Field(None, max_length=255)
    number: Optional[str] = Field(None, max_length=255)
    imei: Optional[str] = Field(None, max_length=255)
    iccid: Optional[str] = Field(None, max_length=255)
    serial_number: Optional[str] = Field(None, max_length=255)
    modem: Optional[str] = Field(None, max_length=255)

    # Reference fields
    asset: Optional[int] = None
    device_type: Optional[int] = None
    device_model_version_id: Optional[int] = None
    current_product_id: Optional[int] = None
    customer_id: Optional[int] = None
    manufacturing_date: Optional[datetime] = None
    group_id: Optional[int] = Field(None, description="Group ID (user must have access)")

    @field_validator('identifier')
    @classmethod
    def identifier_not_empty(cls, v: Optional[str]) -> Optional[str]:
        """Validate identifier is not empty after strip if provided."""
        if v is not None and (not v or not v.strip()):
            raise ValueError('Identifier cannot be empty')
        return v.strip() if v else None

    class Config:
        json_schema_extra = {
            "example": {
                "number": "+5511988888888",
                "operadora": "Claro"
            }
        }


class DeviceResponse(DeviceBase):
    """Schema for device API responses."""

    id: int
    internal_id: Optional[str] = None

    # Access control
    group_id: Optional[int] = None
    account_id: Optional[int] = None

    # Status
    status: int

    # Audit fields
    user_add: Optional[int] = None
    date_add: Optional[datetime] = None
    user_modif: Optional[int] = None
    date_modif: Optional[datetime] = None
    user_removed: Optional[int] = None
    date_removed: Optional[datetime] = None

    class Config:
        from_attributes = True  # Pydantic v2 (was orm_mode in v1)
        json_schema_extra = {
            "example": {
                "id": 1,
                "identifier": "DEV001",
                "internal_id": "1_DEV001",
                "device_model_id": 1,
                "imei": "123456789012345",
                "serial_number": "SN123456",
                "operadora": "Vivo",
                "number": "+5511999999999",
                "group_id": 2977,
                "account_id": 1,
                "status": 1,
                "user_add": 123,
                "date_add": "2025-01-01T10:00:00"
            }
        }
