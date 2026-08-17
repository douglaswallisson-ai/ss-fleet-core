"""Pydantic schemas for Group model."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class GroupBase(BaseModel):
    """Base Group schema with common fields."""
    name: str = Field(..., min_length=1, max_length=255, description="Group name")
    corporate_name: Optional[str] = Field(None, max_length=255, description="Corporate/legal name")
    cnpj: Optional[str] = Field(None, max_length=20, description="CNPJ (Brazilian tax ID)")
    client_cod: Optional[str] = Field(None, max_length=50, description="External client code")
    address: Optional[str] = Field(None, max_length=500, description="Physical address")
    contact: Optional[str] = Field(None, max_length=255, description="Contact information")
    max_speed: Optional[int] = Field(90, ge=0, le=300, description="Default max speed (km/h)")
    vcms_day_off: int = Field(3, ge=0, le=365, description="VCMS days off configuration")
    general: Optional[bool] = Field(False, description="Is general/shared group")
    pro_rata: bool = Field(False, description="Pro-rata billing enabled")


class GroupCreate(GroupBase):
    """Schema for creating a new Group."""
    account_id: int = Field(..., gt=0, description="Account ID (tenant)")
    cli_fat: int = Field(0, ge=0, description="Billing ID")

    @field_validator('cnpj')
    @classmethod
    def validate_cnpj(cls, v: Optional[str]) -> Optional[str]:
        """Validate CNPJ format (basic validation)."""
        if v is not None and v.strip():
            # Remove non-digits
            cnpj = ''.join(filter(str.isdigit, v))
            if len(cnpj) not in [0, 11, 14]:  # Empty, CPF, or CNPJ
                raise ValueError('CNPJ must have 14 digits (or 11 for CPF)')
            return cnpj if cnpj else None
        return None


class GroupUpdate(BaseModel):
    """Schema for updating a Group (all fields optional)."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    corporate_name: Optional[str] = Field(None, max_length=255)
    cnpj: Optional[str] = Field(None, max_length=20)
    client_cod: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = Field(None, max_length=500)
    contact: Optional[str] = Field(None, max_length=255)
    max_speed: Optional[int] = Field(None, ge=0, le=300)
    vcms_day_off: Optional[int] = Field(None, ge=0, le=365)
    general: Optional[bool] = None
    pro_rata: Optional[bool] = None
    cli_fat: Optional[int] = Field(None, ge=0)
    account_id: Optional[int] = Field(None, gt=0)

    @field_validator('cnpj')
    @classmethod
    def validate_cnpj(cls, v: Optional[str]) -> Optional[str]:
        """Validate CNPJ format (basic validation)."""
        if v is not None and v.strip():
            cnpj = ''.join(filter(str.isdigit, v))
            if len(cnpj) not in [0, 11, 14]:
                raise ValueError('CNPJ must have 14 digits (or 11 for CPF)')
            return cnpj if cnpj else None
        return None


class GroupResponse(BaseModel):
    """
    Schema for Group response.

    Propositalmente NÃO herda de GroupBase: aquelas restrições
    (max_length, ge/le) existem para validar dados NOVOS na criação,
    não para descrever dados que já existem no banco. Um registro legado
    fora do padrão não deve derrubar a resposta inteira da lista para
    todo mundo que tem acesso a ele - só reflete o dado como está.
    """
    id: int
    account_id: int
    cli_fat: int
    name: str
    corporate_name: Optional[str] = None
    cnpj: Optional[str] = None
    client_cod: Optional[str] = None
    address: Optional[str] = None
    contact: Optional[str] = None
    max_speed: Optional[int] = None
    vcms_day_off: Optional[int] = None
    general: Optional[bool] = None
    pro_rata: Optional[bool] = None
    user_add: Optional[int] = None
    date_add: Optional[datetime] = None
    user_modif: Optional[int] = None
    date_modif: Optional[datetime] = None

    model_config = {"from_attributes": True}