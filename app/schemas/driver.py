"""
Driver schemas - Driver/conductor management.

Request/response schemas for driver CRUD operations.

IMPORTANT: Validation Strategy
- CREATE/UPDATE (DriverCreate, DriverUpdate): Strict validation (rejects invalid formats)
- READ (DriverResponse): No validation (accepts existing data as-is, even if invalid)
This allows querying legacy data while preventing new invalid data from being created.
"""

from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator, EmailStr
import re


class DriverBase(BaseModel):
    """Base schema for driver data (no validation - used for responses)."""
    name: Optional[str] = None
    account_id: Optional[int] = None
    email: Optional[str] = None  # Changed from EmailStr to allow invalid legacy emails
    phone: Optional[str] = None
    cpf: Optional[str] = None  # No max_length constraint for responses
    matricula: Optional[str] = None

    # Authentication
    auth: int = Field(..., description="Authentication level")
    login: Optional[str] = None
    password: Optional[str] = Field(None, description="Plain password for equipment verification")
    password_apps: Optional[str] = Field(None, description="SHA1 password for app access (will be auto-hashed)")

    # License
    cnh: Optional[str] = None
    cnh_category: Optional[str] = None  # No validation for responses
    cnh_validate: Optional[date] = None
    passport: Optional[str] = None

    # Employment
    area: Optional[str] = None
    empresa: Optional[str] = None
    gestor: Optional[str] = None
    gestor_tel: Optional[str] = None
    local: Optional[str] = None
    admission: Optional[date] = None

    # Validations
    rac_validate: Optional[date] = None
    aso_validate: Optional[date] = None

    # Organization
    group_id: Optional[int] = None
    subgroup_id: Optional[int] = None
    driver_function_id: Optional[int] = Field(19, description="Driver function ID (default: 19)")

    # Additional
    obs_driver: Optional[str] = None
    integration_id: Optional[str] = None


class DriverCreate(BaseModel):
    """
    Schema for creating a driver.

    Strict validation applied to prevent invalid data from being created.
    Note: account_id, user_add, date_add are set automatically by the system.
    """
    name: Optional[str] = None
    email: Optional[EmailStr] = None  # Strict: Valid email required
    phone: Optional[str] = None
    cpf: Optional[str] = Field(None, description="CPF (11 digits only)")
    matricula: Optional[str] = None

    # Authentication
    auth: int = Field(..., description="Authentication level")
    login: Optional[str] = None
    password: Optional[str] = Field(None, description="Plain password for equipment verification")
    password_apps: Optional[str] = Field(None, description="SHA1 password for app access (will be auto-hashed)")

    # License
    cnh: Optional[str] = None
    cnh_category: Optional[str] = Field(None, description="CNH category (A, B, C, D, E, AB, etc)")
    cnh_validate: Optional[date] = None
    passport: Optional[str] = None

    # Employment
    area: Optional[str] = None
    empresa: Optional[str] = None
    gestor: Optional[str] = None
    gestor_tel: Optional[str] = None
    local: Optional[str] = None
    admission: Optional[date] = None

    # Validations
    rac_validate: Optional[date] = None
    aso_validate: Optional[date] = None

    # Organization
    group_id: Optional[int] = None
    subgroup_id: Optional[int] = None
    driver_function_id: Optional[int] = Field(19, description="Driver function ID (default: 19)")

    # Additional
    obs_driver: Optional[str] = None
    integration_id: Optional[str] = None

    @field_validator('cpf')
    @classmethod
    def validate_cpf(cls, v):
        """Validate CPF format (11 digits)."""
        if v is not None:
            cpf_digits = re.sub(r'\D', '', v)
            if len(cpf_digits) != 11:
                raise ValueError('CPF must have exactly 11 digits')
            if not cpf_digits.isdigit():
                raise ValueError('CPF must contain only numbers')
            return cpf_digits
        return v

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        """Validate phone format."""
        if v is not None:
            phone_digits = re.sub(r'\D', '', v)
            if len(phone_digits) < 10:
                raise ValueError('Phone must have at least 10 digits')
            return v
        return v

    @field_validator('cnh')
    @classmethod
    def validate_cnh(cls, v):
        """Validate CNH format (11 digits)."""
        if v is not None:
            cnh_digits = re.sub(r'\D', '', v)
            if len(cnh_digits) != 11:
                raise ValueError('CNH must have exactly 11 digits')
            if not cnh_digits.isdigit():
                raise ValueError('CNH must contain only numbers')
            return cnh_digits
        return v

    @field_validator('cnh_category')
    @classmethod
    def validate_cnh_category(cls, v):
        """Validate CNH category."""
        if v is not None:
            valid_categories = ['A', 'B', 'C', 'D', 'E', 'AB', 'AC', 'AD', 'AE']
            v_upper = v.upper()
            if v_upper not in valid_categories:
                raise ValueError(f'Invalid CNH category. Must be one of: {", ".join(valid_categories)}')
            return v_upper
        return v


class DriverUpdate(BaseModel):
    """
    Schema for updating a driver.

    Strict validation applied to prevent invalid data from being updated.
    """
    name: Optional[str] = None
    email: Optional[EmailStr] = None  # Strict: Valid email required
    phone: Optional[str] = None
    cpf: Optional[str] = Field(None, description="CPF (11 digits only)")
    matricula: Optional[str] = None

    # Authentication
    auth: Optional[int] = None
    login: Optional[str] = None
    password: Optional[str] = None
    password_apps: Optional[str] = None

    # License
    cnh: Optional[str] = None
    cnh_category: Optional[str] = None
    cnh_validate: Optional[date] = None
    passport: Optional[str] = None

    # Employment
    area: Optional[str] = None
    empresa: Optional[str] = None
    gestor: Optional[str] = None
    gestor_tel: Optional[str] = None
    local: Optional[str] = None
    admission: Optional[date] = None

    # Validations
    rac_validate: Optional[date] = None
    aso_validate: Optional[date] = None

    # Organization
    group_id: Optional[int] = None
    subgroup_id: Optional[int] = None
    driver_function_id: Optional[int] = None

    # Status
    status: Optional[int] = None

    # Additional
    obs_driver: Optional[str] = None
    integration_id: Optional[str] = None

    @field_validator('cpf')
    @classmethod
    def validate_cpf(cls, v):
        """Validate CPF format (11 digits)."""
        if v is not None:
            cpf_digits = re.sub(r'\D', '', v)
            if len(cpf_digits) != 11:
                raise ValueError('CPF must have exactly 11 digits')
            if not cpf_digits.isdigit():
                raise ValueError('CPF must contain only numbers')
            return cpf_digits
        return v

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        """Validate phone format."""
        if v is not None:
            phone_digits = re.sub(r'\D', '', v)
            if len(phone_digits) < 10:
                raise ValueError('Phone must have at least 10 digits')
            return v
        return v

    @field_validator('cnh')
    @classmethod
    def validate_cnh(cls, v):
        """Validate CNH format (11 digits)."""
        if v is not None:
            # Remove non-digits
            cnh_digits = re.sub(r'\D', '', v)
            if len(cnh_digits) != 11:
                raise ValueError('CNH must have exactly 11 digits')
            if not cnh_digits.isdigit():
                raise ValueError('CNH must contain only numbers')
            return cnh_digits
        return v

    @field_validator('cnh_category')
    @classmethod
    def validate_cnh_category(cls, v):
        """Validate CNH category."""
        if v is not None:
            valid_categories = ['A', 'B', 'C', 'D', 'E', 'AB', 'AC', 'AD', 'AE']
            v_upper = v.upper()
            if v_upper not in valid_categories:
                raise ValueError(f'Invalid CNH category. Must be one of: {", ".join(valid_categories)}')
            return v_upper
        return v


class DriverResponse(DriverBase):
    """Schema for driver response."""
    id: int
    status: int
    user_add: Optional[int]
    date_add: Optional[datetime]
    user_modif: Optional[int]
    date_modif: Optional[datetime]

    # Virtual properties
    is_active: bool = Field(..., description="Is driver active (status=1)")
    is_deleted: bool = Field(..., description="Is driver soft-deleted (status=-1)")
    cnh_expired: Optional[bool] = Field(None, description="Is CNH expired")
    rac_expired: Optional[bool] = Field(None, description="Is RAC expired")
    aso_expired: Optional[bool] = Field(None, description="Is ASO expired")

    # Exclude sensitive data from response
    password: Optional[str] = Field(None, exclude=True)
    password_apps: Optional[str] = Field(None, exclude=True)

    class Config:
        from_attributes = True


class DriverWithFunction(DriverResponse):
    """Schema for driver response with function details."""
    function_name: Optional[str] = Field(None, description="Driver function name")


class DriverCursorResponse(BaseModel):
    """
    DS-1379: envelope de paginação por cursor para GET /drivers, no mesmo
    formato já usado pelos endpoints de relatório (data/next_cursor/
    has_more/total_returned).
    """
    data: list[DriverResponse]
    next_cursor: Optional[str] = None
    has_more: bool
    total_returned: int