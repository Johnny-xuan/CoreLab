"""Server / GPU schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ServerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lab_id: int
    hostname: str
    display_name: str | None
    ip_address: str | None
    os_info: str | None
    kernel_version: str | None
    cpu_model: str | None
    cpu_cores: int | None
    memory_total_mb: int | None
    agent_version: str | None
    status: Literal["pending", "online", "offline", "maintenance"]
    last_heartbeat_at: datetime | None
    max_reservation_hours: int | None
    is_active: bool
    created_at: datetime
    approved_at: datetime | None
    approved_by_user_id: int | None


class ServerCreate(BaseModel):
    hostname: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=100)
    max_reservation_hours: int | None = Field(default=24, ge=1, le=168)
    expected_hostname_pattern: str | None = Field(default=None, max_length=255)


class ServerCreateResponse(BaseModel):
    server: ServerRead
    enrollment_token: str
    install_snippet: str
    expires_at: datetime


class EnrollmentTokenRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    expected_hostname_pattern: str | None
    expires_at: datetime
    used_at: datetime | None
    used_by_server_id: int | None
    created_at: datetime
    created_by_user_id: int


class EnrollmentTokenAdminItem(BaseModel):
    """Lab-admin listing view; derived ``status`` simplifies UI filters."""

    id: int
    lab_id: int
    expected_hostname_pattern: str | None
    expires_at: datetime
    used_at: datetime | None
    used_by_server_id: int | None
    created_at: datetime
    created_by_user_id: int
    status: Literal["unused", "used", "expired"]


class RegenerateEnrollmentTokenResponse(BaseModel):
    enrollment_token: str
    install_snippet: str
    expires_at: datetime
    revoked_token_ids: list[int]


class GpuRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    server_id: int
    gpu_index: int
    uuid: str | None
    model: str | None
    memory_total_mb: int | None
    compute_capability: str | None
    util_pct: int | None
    memory_used_mb: int | None
    temperature_c: int | None
    power_w: int | None
    process_snapshot: list[dict[str, Any]] | None
    last_updated_at: datetime | None
    is_active: bool


class ServerAdminGrantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    server_id: int
    granted_by_user_id: int
    granted_at: datetime
    notes: str | None
    is_active: bool


class ServerAdminGrantCreate(BaseModel):
    user_id: int
    notes: str | None = Field(default=None, max_length=255)


class MyGrantItem(BaseModel):
    """Per-server grant a user holds — sidebar render input."""

    server_id: int
    hostname: str
    display_name: str | None
    granted_at: datetime
    notes: str | None


class CapabilityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    server_id: int
    capability_key: str
    is_enabled: bool
    is_dangerous: bool
    notes: str | None
    updated_at: datetime
    updated_by_user_id: int


class CapabilityUpdate(BaseModel):
    enabled: bool
    notes: str | None = Field(default=None, max_length=500)
