"""SSH public key schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SshKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    public_key: str
    fingerprint_sha256: str
    key_type: str
    comment: str | None
    is_active: bool
    created_at: datetime


class SshKeyCreate(BaseModel):
    label: str | None = Field(default=None, max_length=255)
    public_key: str = Field(min_length=20, max_length=8192)


SshKeyDeleteResult = Literal["deleted", "already_inactive"]


class SshKeyDeleteResponse(BaseModel):
    id: int
    result: SshKeyDeleteResult
