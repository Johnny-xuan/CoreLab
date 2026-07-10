"""Setup wizard + activation flow schemas."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .user import EmailStr, UsernamePattern, UserRead

LabSlugPattern = re.compile(r"^[a-z][a-z0-9-]{1,49}$")


class SetupStatus(BaseModel):
    initialized: bool


class SetupInitRequest(BaseModel):
    lab_name: str = Field(min_length=1, max_length=100)
    # Phase M M-2.2 — slug is now optional. When omitted (or blank),
    # the server derives one from lab_name; falls back to lab-{8 hex}
    # when derivation can't produce a valid ASCII slug (e.g. lab_name
    # is pure Chinese). Frontend Setup wizard pre-fills the derived
    # slug so the user can override before submit.
    lab_slug: str | None = Field(default=None, max_length=50)
    admin_username: str = Field(min_length=3, max_length=64)
    admin_email: EmailStr
    admin_display_name: str = Field(min_length=1, max_length=100)
    admin_password: str = Field(min_length=8, max_length=256)

    @field_validator("lab_slug")
    @classmethod
    def _slug_pattern(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not LabSlugPattern.fullmatch(v):
            raise ValueError("lab_slug must match ^[a-z][a-z0-9-]{1,49}$")
        return v

    @field_validator("admin_username")
    @classmethod
    def _username_pattern(cls, v: str) -> str:
        if not UsernamePattern.fullmatch(v):
            raise ValueError("admin_username must match ^[a-z][a-z0-9_-]{2,63}$")
        return v


class SetupInitResponse(BaseModel):
    lab_id: int
    admin: UserRead


class ActivateValidate(BaseModel):
    """Public preview returned by ?token=… so the frontend can show
    "Complete registration for <username>". Never includes hash or token data.
    """

    model_config = ConfigDict(from_attributes=True)

    user_id: int | None = None
    username: str | None = None
    email: EmailStr | None = None
    display_name: str | None = None
    purpose: Literal["registration", "activation", "password_reset"]
    role: Literal["user", "lab_admin"] = "user"


class ActivateSubmit(BaseModel):
    token: str = Field(min_length=8, max_length=128)
    username: str | None = Field(default=None, min_length=3, max_length=64)
    email: EmailStr | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=256)
    ssh_key_label: str | None = Field(default=None, max_length=255)
    ssh_key_public_key: str | None = Field(default=None, min_length=20, max_length=8192)

    @field_validator("username")
    @classmethod
    def _username_pattern(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not UsernamePattern.fullmatch(v):
            raise ValueError("username must match ^[a-z][a-z0-9_-]{2,63}$")
        return v

    @field_validator("ssh_key_public_key", "ssh_key_label", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        if not isinstance(v, str):
            return v
        stripped = v.strip()
        return stripped or None
