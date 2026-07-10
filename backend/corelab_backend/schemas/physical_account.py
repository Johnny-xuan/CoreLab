"""PhysicalAccount schemas (Phase 4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ._utc import UtcDatetime

LINUX_USERNAME_PATTERN = r"^[a-z_][a-z0-9_-]{0,31}$"

PaSource = Literal["agent_created", "discovered_scan", "admin_manual_register"]


class PhysicalAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    server_id: int
    linux_username: str
    uid: int | None
    gid: int | None
    home_directory: str | None
    default_shell: str | None
    source: PaSource
    is_active: bool
    created_at: UtcDatetime
    created_by_user_id: int | None
    notes: str | None
    last_seen_at: UtcDatetime | None


class PhysicalAccountCreate(BaseModel):
    linux_username: str = Field(min_length=1, max_length=32, pattern=LINUX_USERNAME_PATTERN)
    source: PaSource = "admin_manual_register"
    notes: str | None = Field(default=None, max_length=255)


class AdminDeclareOwnerRequest(BaseModel):
    owner_user_id: int
    reason: str = Field(min_length=20, max_length=500)


class ReverseLookupEntry(BaseModel):
    user_id: int
    link_id: int
    source: Literal["ssh_challenge", "password_pam", "admin_prepared_then_ssh", "admin_declared"]
    is_active: bool


class ReverseLookupResponse(BaseModel):
    physical_account_id: int | None
    linked_users: list[ReverseLookupEntry]
    is_shared: bool


# Phase K K-7 — admin one-stop "useradd + push key" provisioning.
class OnboardUserRequest(BaseModel):
    """Create a Linux account on the server, push the chosen user's SSH
    public key into authorized_keys, and bind the resulting
    physical_account to the user via an admin-declared link — all in
    one transaction."""

    linux_username: str = Field(min_length=1, max_length=32, pattern=LINUX_USERNAME_PATTERN)
    owner_user_id: int
    ssh_public_key_id: int
    reason: str = Field(min_length=20, max_length=500)


class OnboardUserResponse(BaseModel):
    physical_account_id: int
    account_link_id: int
    authorized_key_entry_id: int
    useradd_outcome: dict
    key_push_outcome: dict


class AuthorizedKeyRetryResponse(BaseModel):
    physical_account_id: int
    authorized_key_entry_id: int
    key_push_outcome: dict


AuthorizedKeyEntryStatus = Literal["active", "push_failed", "removed"]


class AuthorizedKeyInventoryEntry(BaseModel):
    entry_id: int
    physical_account_id: int
    linux_username: str
    ssh_public_key_id: int
    fingerprint_sha256: str
    key_type: str
    key_comment: str | None
    key_is_active: bool
    pushed_for_user_id: int
    pushed_for_username: str
    pushed_for_display_name: str
    pushed_by_user_id: int
    pushed_by_username: str
    pushed_by_display_name: str
    pushed_at: UtcDatetime
    is_active: bool
    removed_at: UtcDatetime | None
    removed_by_user_id: int | None
    removed_by_username: str | None
    removed_by_display_name: str | None
    status: AuthorizedKeyEntryStatus
    can_retry: bool


class AuthorizedKeyHostEntry(BaseModel):
    line_number: int
    fingerprint_sha256: str
    key_type: str | None = None
    comment: str | None = None


class AuthorizedKeyManagedReadbackEntry(BaseModel):
    entry_id: int
    ssh_public_key_id: int
    fingerprint_sha256: str
    key_type: str
    key_comment: str | None
    pushed_for_user_id: int
    pushed_for_username: str
    pushed_for_display_name: str
    pushed_at: UtcDatetime
    present_on_host: bool


class AuthorizedKeyReadbackResponse(BaseModel):
    physical_account_id: int
    server_id: int
    linux_username: str
    ok: bool
    error: str | None = None
    authorized_keys_path: str | None = None
    line_count: int = 0
    invalid_line_count: int = 0
    host_keys: list[AuthorizedKeyHostEntry] = Field(default_factory=list)
    managed_entries: list[AuthorizedKeyManagedReadbackEntry] = Field(default_factory=list)
    unknown_host_keys: list[AuthorizedKeyHostEntry] = Field(default_factory=list)
    mock_warning: str | None = None
