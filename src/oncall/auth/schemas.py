"""Pydantic request and response schemas for local authentication."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


UserRole = Literal["viewer", "operator", "approver", "admin"]


class UserSummary(BaseModel):
    """The non-sensitive identity returned to API clients."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    role: UserRole


class UserResponse(UserSummary):
    """An administrator-visible user record."""

    is_active: bool
    created_at: datetime
    updated_at: datetime


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = 900
    user: UserSummary


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    password: str = Field(min_length=12, max_length=256)
    role: UserRole = "viewer"


class UserUpdateRequest(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def requires_change(self) -> "UserUpdateRequest":
        if self.role is None and self.is_active is None:
            raise ValueError("至少提供 role 或 is_active 其中一个字段")
        return self
