"""Auth request / response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .user import UserRead


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_at: datetime
    user: UserRead


class LogoutResponse(BaseModel):
    ok: bool = True
