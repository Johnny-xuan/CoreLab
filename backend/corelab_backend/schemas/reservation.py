"""Reservation API request / response schemas.

Mirrors docs/05-api-design.md §3.12 (POST /reservations), §3.13
(POST /reservations/preview-conflicts), and §3.14 (GET listing).
``account_link_id`` is required at the wire level — the per-PA route
shim in §2.17 injects it for the PA-centric UI; direct callers must
supply it themselves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ._utc import UtcDatetime

SCRIPT_MAX_LEN = 4096  # API-level guard; service re-checks bytes


class ReservationItem(BaseModel):
    """One row of an outgoing reservation batch.

    Phase J — ``gpu_id`` is now optional. NULL = Mode 3 (pure cron task,
    no GPU). When NULL, the parent batch must carry a script and
    gpu_memory_mb / gpu_compute_share_pct must both be NULL (service
    layer enforces).
    """

    server_id: int
    gpu_id: int | None = None
    start_at: datetime
    end_at: datetime
    account_link_id: int
    gpu_memory_mb: int | None = Field(default=None, ge=1)
    gpu_compute_share_pct: int | None = Field(default=None, ge=1, le=100)


class ReservationCreateRequest(BaseModel):
    items: list[ReservationItem] = Field(min_length=1, max_length=64)
    script: str | None = Field(default=None, max_length=SCRIPT_MAX_LEN)
    script_scheduled_start_at: datetime | None = None
    script_max_runtime_seconds: int | None = Field(default=None, ge=1)
    share_script: bool = True


class ReservationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    server_id: int
    gpu_id: int | None  # Phase J — NULL for Mode 3 (no-GPU cron task)
    account_link_id: int
    group_id: str | None
    start_at: UtcDatetime
    end_at: UtcDatetime
    status: Literal["scheduled", "active", "completed", "cancelled", "failed"]
    gpu_memory_mb: int | None
    gpu_compute_share_pct: int | None
    script: str | None
    script_scheduled_start_at: UtcDatetime | None
    script_max_runtime_seconds: int | None
    script_started_at: UtcDatetime | None
    script_finished_at: UtcDatetime | None
    script_exit_code: int | None
    script_status: str | None
    script_output_size_bytes: int | None
    script_log_path: str | None
    created_at: UtcDatetime
    cancelled_at: UtcDatetime | None
    cancelled_by_user_id: int | None
    cancellation_reason: str | None


class ReservationCreateResponse(BaseModel):
    group_id: str
    reservations: list[ReservationRead]


class ReservationScriptLogRead(BaseModel):
    reservation_id: int
    text: str
    truncated: bool
    log_path: str | None
    output_size_bytes: int | None
    script_status: str | None
    script_started_at: UtcDatetime | None
    script_finished_at: UtcDatetime | None


class PreviewItem(BaseModel):
    server_id: int
    gpu_id: int
    start_at: datetime
    end_at: datetime
    gpu_memory_mb: int | None = Field(default=None, ge=1)
    gpu_compute_share_pct: int | None = Field(default=None, ge=1, le=100)


class PreviewRequest(BaseModel):
    items: list[PreviewItem] = Field(min_length=1, max_length=128)
    account_link_id: int


class ConflictRead(BaseModel):
    input_index: int
    type: Literal[
        "exclusive_conflict",
        "memory_exceeded",
        "mix_exclusive_shared",
        "compute_exceeded",
        "time_too_long",
        "invalid_time",
    ]
    conflicting_reservation_ids: list[int]
    memory: dict[str, int] | None = None
    compute: dict[str, int] | None = None
    time: dict[str, float | int | None] | None = None


class TimeLimitCheck(BaseModel):
    input_index: int
    max_hours: int | None
    requested_hours: float
    would_exceed: bool


class PreviewResponse(BaseModel):
    conflicts: list[ConflictRead]
    time_limit_checks: list[TimeLimitCheck]


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


# Phase J — schedule recommender request / response.
class RecommendRequest(BaseModel):
    """Resource ask for the recommender. ``gpu_count`` ≥ 1; ``after``
    defaults to "now" when omitted."""

    gpu_count: int = Field(ge=1, le=8)
    time_limit_seconds: int = Field(ge=60, le=86_400)
    after: datetime | None = None
    top_k: int = Field(default=3, ge=1, le=10)


class RecommendCandidate(BaseModel):
    server_id: int
    gpu_ids: list[int]
    start_at: UtcDatetime
    end_at: UtcDatetime


class RecommendResponse(BaseModel):
    candidates: list[RecommendCandidate]


class ModifyRequest(BaseModel):
    start_at: datetime | None = None
    end_at: datetime | None = None
    script: str | None = Field(default=None, max_length=SCRIPT_MAX_LEN)
    # T90 — Phase H.1: surface the timing knobs the Scripts page lets
    # users edit before the agent has dispatched. Service-level guard
    # rejects both fields if script_status != null or
    # script_dispatch_started_at is set.
    script_scheduled_start_at: datetime | None = None
    script_max_runtime_seconds: int | None = Field(default=None, ge=60)


class ConflictDetail(BaseModel):
    """Body of a 409 — service maps the exception ``details``."""

    code: str
    conflicting_reservation_ids: list[int] = []
    memory: dict[str, int] | None = None
    compute: dict[str, int] | None = None


class TooLongDetail(BaseModel):
    code: Literal["RESERVATION_TOO_LONG"] = "RESERVATION_TOO_LONG"
    max_hours: int
    requested_hours: float


__all__ = [
    "CancelRequest",
    "ConflictDetail",
    "ConflictRead",
    "ModifyRequest",
    "PreviewItem",
    "PreviewRequest",
    "PreviewResponse",
    "RecommendCandidate",
    "RecommendRequest",
    "RecommendResponse",
    "ReservationCreateRequest",
    "ReservationCreateResponse",
    "ReservationItem",
    "ReservationRead",
    "ReservationScriptLogRead",
    "TimeLimitCheck",
    "TooLongDetail",
]
