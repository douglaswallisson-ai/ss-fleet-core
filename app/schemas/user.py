"""Pydantic schemas for User model."""

from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, EmailStr, Field


# Legacy compatibility: role is now a string property in User model
RoleType = Literal["ADMIN", "VIEWER", "OPERATOR"]


class UserBase(BaseModel):
    """Base user schema with common fields."""
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)
    role: RoleType = "VIEWER"


class UserCreate(UserBase):
    """Schema for creating a new user."""
    password: str = Field(..., min_length=8, max_length=100)


class UserUpdate(BaseModel):
    """Schema for updating user."""
    email: Optional[EmailStr] = None
    full_name: Optional[str] = Field(None, min_length=1, max_length=255)
    role: Optional[RoleType] = None
    is_active: Optional[bool] = None


class UserResponse(UserBase):
    """Schema for user response."""
    id: int
    is_active: bool
    is_verified: bool
    created_at: datetime
    last_login: Optional[datetime]

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    """Schema for login request (uses login field, not email)."""
    login: str
    password: str


class SSOHandoffRequest(BaseModel):
    """Schema for SSO handoff request (F4-02). Ticket emitido pela plataforma padrao."""
    ticket: str = Field(..., min_length=1, description="Ticket assinado (HMAC) emitido pela plataforma padrao")


class TokenResponse(BaseModel):
    """Schema for token response."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class MeResponse(BaseModel):
    """
    Schema for GET /me response.

    Propositalmente NÃO reaproveita UserResponse: aquele schema usa
    EmailStr (validação estrita de formato) e campos que não existem no
    model User real (is_verified, last_login) - quebraria a resposta
    para qualquer usuário com email legado fora do padrão, mesmo
    problema já corrigido em GroupResponse/SubgroupResponse.
    """
    id: int
    name: Optional[str] = None
    login: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None

    model_config = {"from_attributes": True}