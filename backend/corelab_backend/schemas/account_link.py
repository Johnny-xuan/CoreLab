"""AccountLink + SSH challenge schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

LINUX_USERNAME_PATTERN = r"^[a-z_][a-z0-9_-]{0,31}$"


class ChallengeRequest(BaseModel):
    server_id: int
    linux_username: str = Field(min_length=1, max_length=32, pattern=LINUX_USERNAME_PATTERN)
    ssh_public_key_id: int


class ChallengeIssued(BaseModel):
    challenge_id: str
    nonce: str
    expires_at: datetime
    sign_command: str
    signing_namespace: str


class VerifyRequest(BaseModel):
    challenge_id: str
    signature_armored: str = Field(min_length=1, max_length=8192)


class AccountLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    physical_account_id: int
    source: Literal["ssh_challenge", "password_pam", "admin_prepared_then_ssh", "admin_declared"]
    proof_evidence: dict[str, Any]
    established_at: datetime
    is_active: bool
    revoked_at: datetime | None
    revoke_reason: (
        Literal["self", "admin_force", "user_disabled", "pa_disabled", "upgraded_to_verified"]
        | None
    )


class VerifyResponse(BaseModel):
    account_link: AccountLinkRead
    signer_fingerprint: str


class LinkRevokeRequest(BaseModel):
    reason: Literal["self", "admin_force", "user_disabled", "pa_disabled"] = "self"
    revoke_key: bool = True
    """Phase K — when true (default), also call agent ``backend.authorized_key.revoke``
    for every authorized_key_entry CoreLab pushed for this user-PA pair. False
    leaves the keys in place (e.g. user revokes a SSH-challenge link where the
    key was their own, not pushed by CoreLab)."""


class UpgradeViaChallengeRequest(BaseModel):
    challenge_id: str
    signature_armored: str = Field(min_length=1, max_length=8192)


class UpgradeResponse(BaseModel):
    account_link: AccountLinkRead
    signer_fingerprint: str
    upgraded_from_link_id: int


class PamVerifyEndpointRequest(BaseModel):
    server_id: int
    linux_username: str = Field(min_length=1, max_length=32, pattern=LINUX_USERNAME_PATTERN)
    password: str = Field(min_length=1, max_length=512)


class PamVerifyEndpointResponse(BaseModel):
    account_link: AccountLinkRead


# Phase K — admin-decision context for account_link_request review UI.
class RequesterRequestStats(BaseModel):
    """Aggregate counts of past requests this requester has made."""

    total: int
    approved: int
    denied: int
    withdrawn: int


class KeyLateralSurface(BaseModel):
    """Where else CoreLab has pushed this key (helps spot horizontal infection)."""

    fingerprint_sha256: str
    label: str | None
    physical_account_id: int
    linux_username: str
    server_hostname: str
    pushed_at: datetime


class RequestContext(BaseModel):
    """All the signals admin needs to make the approve/deny call.

    The phase_K_plan §7 mockup defines what shows up on the review card —
    first-time vs renewal, the user's history with both this PA and
    requests in general, and the lateral surface of every key we'd be
    pushing on approval.
    """

    request_id: int
    requester_user_id: int
    requester_username: str
    requester_display_name: str
    physical_account_id: int
    linux_username: str
    server_id: int
    server_hostname: str
    server_display_name: str | None
    is_first_time_for_this_pa: bool
    """True iff this requester has no prior link history (active OR
    revoked) with this PA. False = renewal of a past relationship."""
    requester_stats: RequesterRequestStats
    requester_active_keys: list[dict[str, str | int]]
    lateral_surface: list[KeyLateralSurface]


class AccountLinkRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requester_user_id: int
    physical_account_id: int
    status: Literal["pending", "approved", "denied", "withdrawn"]
    request_note: str | None
    decided_by_user_id: int | None
    decided_at: datetime | None
    decision_note: str | None
    created_at: datetime
    updated_at: datetime


class AccountLinkRequestCreate(BaseModel):
    physical_account_id: int
    request_note: str | None = Field(default=None, max_length=500)


class AccountLinkRequestDecision(BaseModel):
    decision_note: str | None = Field(default=None, max_length=500)


class AccountLinkRequestRetryPushResponse(BaseModel):
    request: AccountLinkRequestRead
    key_push_outcome: dict[str, Any]
