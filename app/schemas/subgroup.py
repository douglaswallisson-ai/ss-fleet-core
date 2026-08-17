"""Pydantic schemas for Subgroup model."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class SubgroupBase(BaseModel):
    """Base Subgroup schema with common fields."""
    name: str = Field(..., min_length=1, max_length=255, description="Subgroup name")
    color: str = Field('#000000', pattern=r'^#[0-9A-Fa-f]{3,6}$', description="Hex color code (3 or 6 chars)")
    client_cod: Optional[str] = Field(None, max_length=50, description="External client code")
    company: Optional[str] = Field(None, max_length=255, description="Company name")
    address: Optional[str] = Field(None, max_length=500, description="Physical address")
    cnpj: Optional[str] = Field(None, max_length=20, description="CNPJ (Brazilian tax ID)")
    tolerance_before_ini: int = Field(5, ge=0, le=60, description="Tolerance before journey start (minutes)")
    tolerance_after_ini: int = Field(5, ge=0, le=60, description="Tolerance after journey start (minutes)")
    suspended: bool = Field(False, description="Is subgroup suspended")
    int_cittati: Optional[bool] = Field(None, description="Cittati integration enabled")


class SubgroupCreate(SubgroupBase):
    """Schema for creating a new Subgroup."""
    group_id: int = Field(..., gt=0, description="Group ID (parent)")

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


class SubgroupUpdate(BaseModel):
    """Schema for updating a Subgroup (all fields optional)."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    color: Optional[str] = Field(None, pattern=r'^#[0-9A-Fa-f]{3,6}$')
    client_cod: Optional[str] = Field(None, max_length=50)
    company: Optional[str] = Field(None, max_length=255)
    address: Optional[str] = Field(None, max_length=500)
    cnpj: Optional[str] = Field(None, max_length=20)
    tolerance_before_ini: Optional[int] = Field(None, ge=0, le=60)
    tolerance_after_ini: Optional[int] = Field(None, ge=0, le=60)
    suspended: Optional[bool] = None
    int_cittati: Optional[bool] = None
    group_id: Optional[int] = Field(None, gt=0)

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


class SubgroupResponse(BaseModel):
    """
    Schema for Subgroup response.

    Propositalmente NÃO herda de SubgroupBase: aquelas restrições
    (max_length, ge/le) existem para validar dados NOVOS na criação,
    não para descrever dados que já existem no banco. Um registro legado
    fora do padrão (ex.: cnpj com mais de 20 caracteres) não deve
    derrubar a resposta inteira da lista para todo mundo que tem acesso
    a ele - só reflete o dado como está.
    """
    id: int
    group_id: int
    name: str
    color: Optional[str] = None
    client_cod: Optional[str] = None
    company: Optional[str] = None
    address: Optional[str] = None
    cnpj: Optional[str] = None
    tolerance_before_ini: Optional[int] = None
    tolerance_after_ini: Optional[int] = None
    suspended: Optional[bool] = None
    int_cittati: Optional[bool] = None
    suspended_date: Optional[datetime] = None
    user_add: Optional[int] = None
    date_add: Optional[datetime] = None
    user_modif: Optional[int] = None
    date_modif: Optional[datetime] = None

    model_config = {"from_attributes": True}