"""User-facing pydantic schemas (request / response)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

UsernamePattern = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")

# Permissive email pattern (RFC 5322 in full is overkill; this rejects
# the worst typos without pulling in email-validator).
EmailStr = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=255,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    ),
]


class UserRead(BaseModel):
    """User as returned by the API. Never includes the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    lab_id: int
    username: str
    email: EmailStr
    display_name: str
    role: Literal["user", "lab_admin"]
    is_active: bool
    # True once the user set a password (vs invited-but-pending). Derived
    # from password_hash on the ORM model; never exposes the hash itself.
    is_activated: bool
    last_login_at: datetime | None
    created_at: datetime


class UserCreate(BaseModel):
    """Deprecated compatibility payload for old admin-precreated invites.

    The visible product flow now uses ``UserInviteCreate`` and lets invitees
    fill username/email/display name on /register. The old shape is still
    accepted so stale browser bundles do not create pending users.
    """

    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=100)
    role: Literal["user", "lab_admin"] = "user"

    @field_validator("username")
    @classmethod
    def _username_pattern(cls, v: str) -> str:
        if not UsernamePattern.fullmatch(v):
            raise ValueError("username must match ^[a-z][a-z0-9_-]{2,63}$")
        return v


class UserInviteCreate(BaseModel):
    """Lab admin creates a registration link before the user exists."""

    role: Literal["user", "lab_admin"] = "user"


class RegistrationInviteUserRef(BaseModel):
    id: int
    username: str
    display_name: str


class RegistrationInviteRead(BaseModel):
    id: int
    role: Literal["user", "lab_admin"]
    status: Literal["active", "used", "expired", "revoked"]
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    created_by: RegistrationInviteUserRef | None
    used_by: RegistrationInviteUserRef | None
    can_revoke: bool


class UserUpdate(BaseModel):
    """Self-service profile edits (display_name / email)."""

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailStr | None = None


class UserRoleUpdate(BaseModel):
    role: Literal["user", "lab_admin"]


class PasswordChange(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class UserInviteResponse(BaseModel):
    """Returned to the lab admin after a successful invite.

    ``setup_token`` is the plaintext registration token — shown exactly
    once; the API never returns it again. Phase 7 will instead email
    the activation URL directly to the user.
    """

    user: UserRead | None = None
    invitation_id: int | None = None
    role: Literal["user", "lab_admin"] = "user"
    setup_token: str
    activation_url: str
    expires_at: datetime


class PasswordResetResponse(BaseModel):
    """Returned to a lab admin who generates a password-reset link for a user.

    Same one-shot semantics as ``UserInviteResponse``: ``setup_token`` is the
    plaintext reset token, shown once. The user opens ``reset_url`` to choose
    a new password (reuses the activation flow in ``reset`` mode).
    """

    user: UserRead
    setup_token: str
    reset_url: str
    expires_at: datetime


# Phase K — admin picture of a user (drives the AdminUsers drawer).
class ProfileLinkItem(BaseModel):
    link_id: int
    physical_account_id: int
    linux_username: str
    server_hostname: str
    source: str
    is_active: bool
    established_at: datetime
    revoked_at: datetime | None


class ProfilePendingRequest(BaseModel):
    request_id: int
    physical_account_id: int
    linux_username: str
    server_hostname: str
    request_note: str | None
    created_at: datetime


class ProfileSshKey(BaseModel):
    id: int
    fingerprint_sha256: str
    key_type: str
    comment: str | None
    is_active: bool
    created_at: datetime


class ProfileReservationStats(BaseModel):
    active_count: int
    last_30d_count: int
    gpu_hours_7d: float
    gpu_hours_30d: float


class ProfileGpuRanking(BaseModel):
    gpu_id: int
    gpu_index: int
    server_id: int
    server_hostname: str
    hours: float


class ProfileRecentAudit(BaseModel):
    id: int
    action: str
    target_type: str | None
    target_id: int | None
    target_server_id: int | None
    result: str
    created_at: datetime


class UserProfileSummary(BaseModel):
    user: UserRead
    active_links: list[ProfileLinkItem]
    revoked_links: list[ProfileLinkItem]
    pending_requests: list[ProfilePendingRequest]
    ssh_keys: list[ProfileSshKey]
    # Phase L L-1
    reservation_stats: ProfileReservationStats
    top_gpu_7d: list[ProfileGpuRanking]
    recent_audit: list[ProfileRecentAudit]


# Phase L L-1 — GET /users/:id/reservations response


class UserReservationItem(BaseModel):
    id: int
    gpu_id: int | None
    gpu_index: int | None
    server_id: int
    server_hostname: str
    start_at: datetime
    end_at: datetime
    status: str
    hours: float
    has_script: bool
    script_status: str | None


class UserReservationsResponse(BaseModel):
    upcoming: list[UserReservationItem]
    last_30d: list[UserReservationItem]
    gpu_hours_30d: float
    gpu_hours_by_server_30d: list[ProfileGpuRanking]
