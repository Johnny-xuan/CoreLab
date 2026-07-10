"""Reservation writer service — 4 conflict types + admin_declared gate + group.

All ``reservation`` rows are written through this module so the
invariants in docs/02-data-model.md §5.13 + docs/05-api-design.md §3.12
stay in one place. Phase 6 added the status-machine transition helpers
(``transition_to_active`` / ``transition_to_completed`` /
``transition_to_failed``) and the script-lifecycle sync
(``update_script_status``) at the bottom of the module — the scheduler
and the agent lifecycle handler are the only callers of those four.

- ``account_link_id`` runs through Phase 4's
  ``account_link_service.get_active_link_for_actas`` — admin_declared
  links raise ``LinkNotVerifiedError`` per doc §3.12 line 712.
- Conflict detection inside a SERIALIZABLE transaction:
  - exclusive_conflict (some active row in window) → 409
  - mix_exclusive_shared (window has shared + new exclusive, or
    window has exclusive + new shared) → 409
  - memory_exceeded (sum of shared MB + new > gpu.memory_total_mb) → 409
  - compute_exceeded (sum of compute_share_pct + new > 100) → 409
  (Q3 — new error added to the doc in this commit; previously only
  memory_exceeded was documented as the shared-mode cap.)
- ``(end_at - start_at) <= server.max_reservation_hours`` is the
  **唯一保留的硬限制** per doc §15.11 line 971 → 422 RESERVATION_TOO_LONG.
- Cancel permission: owner / lab_admin / server_admin (caller resolves
  the latter two through ``role`` + ``server_admin_grant`` before
  calling); the service refuses other actors with
  ``CancelNotPermittedError``.
- group_id is generated client-side per submission via UUID4 so all
  items in a single ``create_reservation_batch`` call share it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal, cast

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api import ws_user
from ..logging_setup import get_logger
from ..models import AccountLink, Gpu, Reservation, Server
from . import account_link_service, audit_service, notification_service

_log = get_logger("corelab.reservation")

SCRIPT_MAX_BYTES = 4096
SCRIPT_LOG_TAIL_MAX_CHARS: Final[int] = 65_536

# ─── Status enums (Phase 6 P6-2 — no raw string literals at call sites)
# ReservationStatus mirrors docs/02 §5.13 ck_res_status (5 values).
ReservationStatus = Literal["scheduled", "active", "completed", "cancelled", "failed"]
STATUS_SCHEDULED: Final[ReservationStatus] = "scheduled"
STATUS_ACTIVE: Final[ReservationStatus] = "active"
STATUS_COMPLETED: Final[ReservationStatus] = "completed"
STATUS_CANCELLED: Final[ReservationStatus] = "cancelled"
STATUS_FAILED: Final[ReservationStatus] = "failed"
ACTIVE_STATUSES: Final[tuple[ReservationStatus, ...]] = (STATUS_SCHEDULED, STATUS_ACTIVE)
TERMINAL_STATUSES: Final[tuple[ReservationStatus, ...]] = (
    STATUS_COMPLETED,
    STATUS_CANCELLED,
    STATUS_FAILED,
)

# ScriptStatus mirrors docs/02 §5.13 ck_res_script_status (Phase 6 added).
# NULL on the row = not yet fired (or no script attached); the helpers
# treat ``None`` as the implicit zero state — there is no string for it.
ScriptStatus = Literal["running", "completed", "failed", "killed"]
SCRIPT_RUNNING: Final[ScriptStatus] = "running"
SCRIPT_COMPLETED: Final[ScriptStatus] = "completed"
SCRIPT_FAILED: Final[ScriptStatus] = "failed"
SCRIPT_KILLED: Final[ScriptStatus] = "killed"

# Allowed reservation transitions. Source: docs/02 §5.13 state machine
# (Phase 6 SP-4 patch in this commit) + brief §3:
#   scheduled --(start_at 到)--> active
#   scheduled --(user/admin cancel)--> cancelled
#   active --(end_at 到, script not failed/killed)--> completed
#   active --(end_at 到, script failed/killed)--> failed
#   active --(user/admin cancel)--> cancelled
# Terminal statuses (cancelled / completed / failed) have no outgoing
# edge — every helper here refuses to leave them.
_ALLOWED_TRANSITIONS: Final[dict[ReservationStatus, frozenset[ReservationStatus]]] = {
    STATUS_SCHEDULED: frozenset({STATUS_ACTIVE, STATUS_CANCELLED}),
    STATUS_ACTIVE: frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED}),
    STATUS_COMPLETED: frozenset(),
    STATUS_CANCELLED: frozenset(),
    STATUS_FAILED: frozenset(),
}

# Allowed script_status transitions. ``None`` means "not yet fired";
# once fired (running) the only legal next states are the three
# terminal ones below.
_ALLOWED_SCRIPT_TRANSITIONS: Final[dict[ScriptStatus | None, frozenset[ScriptStatus]]] = {
    None: frozenset({SCRIPT_RUNNING}),
    SCRIPT_RUNNING: frozenset({SCRIPT_COMPLETED, SCRIPT_FAILED, SCRIPT_KILLED}),
    SCRIPT_COMPLETED: frozenset(),
    SCRIPT_FAILED: frozenset(),
    SCRIPT_KILLED: frozenset(),
}


class ReservationError(Exception):
    """Base for reservation-service errors. HTTP-status mapping lives in api."""


class ReservationOverlapError(ReservationError):
    """Exclusive-mode conflict: window already holds an active reservation."""

    def __init__(self, message: str, conflicting_reservation_ids: list[int]) -> None:
        super().__init__(message)
        self.conflicting_reservation_ids = conflicting_reservation_ids


class ReservationMemoryExceededError(ReservationError):
    """Shared-mode memory stack would exceed gpu.memory_total_mb."""

    def __init__(
        self,
        message: str,
        *,
        used_mb: int,
        would_use_mb: int,
        total_mb: int,
        conflicting_reservation_ids: list[int],
    ) -> None:
        super().__init__(message)
        self.used_mb = used_mb
        self.would_use_mb = would_use_mb
        self.total_mb = total_mb
        self.exceeds_by_mb = used_mb + would_use_mb - total_mb
        self.conflicting_reservation_ids = conflicting_reservation_ids


class ReservationComputeExceededError(ReservationError):
    """Shared-mode compute_share_pct stack would exceed 100.

    Q3 addition — symmetric to memory_exceeded. ``conflicting_reservation_ids``
    is the set of in-window rows that already declared a pct.
    """

    def __init__(
        self,
        message: str,
        *,
        used_pct: int,
        would_use_pct: int,
        conflicting_reservation_ids: list[int],
    ) -> None:
        super().__init__(message)
        self.used_pct = used_pct
        self.would_use_pct = would_use_pct
        self.exceeds_by_pct = used_pct + would_use_pct - 100
        self.conflicting_reservation_ids = conflicting_reservation_ids


class ReservationMixExclusiveSharedError(ReservationError):
    """Window contains exclusive + new shared, or window has shared + new exclusive."""

    def __init__(self, message: str, conflicting_reservation_ids: list[int]) -> None:
        super().__init__(message)
        self.conflicting_reservation_ids = conflicting_reservation_ids


class ReservationTooLongError(ReservationError):
    """end_at - start_at > server.max_reservation_hours."""

    def __init__(self, message: str, *, max_hours: int, requested_hours: float) -> None:
        super().__init__(message)
        self.max_hours = max_hours
        self.requested_hours = requested_hours


class InvalidTimeError(ReservationError):
    """end_at <= start_at, start_at in the past, or script_scheduled_start_at out of range."""


class LinkNotVerifiedError(ReservationError):
    """account_link_id points at an admin_declared row (cannot act-as)."""


class NoActiveLinkError(ReservationError):
    """account_link_id doesn't exist or is already revoked."""


class ScriptTooLargeError(ReservationError):
    """script body > SCRIPT_MAX_BYTES."""


class ReservationNotFoundError(ReservationError):
    pass


class CancelNotPermittedError(ReservationError):
    """Caller is neither owner nor an admin authorized to cancel for this server."""


class GroupCancelRunningScriptError(ReservationError):
    """Group cancel refused because one or more rows have running scripts."""

    def __init__(self, message: str, running_reservation_ids: list[int]) -> None:
        super().__init__(message)
        self.running_reservation_ids = running_reservation_ids


class ReservationIllegalTransitionError(ReservationError):
    """A scheduler / lifecycle transition asked for a status change that is
    not in ``_ALLOWED_TRANSITIONS`` (e.g. completed → scheduled)."""

    def __init__(self, message: str, *, current_status: str, attempted_status: str) -> None:
        super().__init__(message)
        self.current_status = current_status
        self.attempted_status = attempted_status


@dataclass(frozen=True, slots=True)
class ItemDraft:
    """One row's worth of reservation input.

    Phase J — ``gpu_id`` is now Optional. NULL = Mode 3 (pure cron task,
    no GPU occupation). When NULL, ``gpu_memory_mb`` /
    ``gpu_compute_share_pct`` must also be NULL, and a script must be
    attached on the parent batch (else the row is "empty + empty" and
    rejected). GPU conflict detection is skipped for NULL gpu_id rows.
    """

    server_id: int
    gpu_id: int | None
    start_at: datetime
    end_at: datetime
    account_link_id: int
    gpu_memory_mb: int | None = None
    gpu_compute_share_pct: int | None = None


@dataclass(frozen=True, slots=True)
class ConflictRecord:
    input_index: int
    type: str  # exclusive_conflict / memory_exceeded / mix_exclusive_shared
    # / compute_exceeded / time_too_long
    conflicting_reservation_ids: list[int]
    memory: dict[str, int] | None = None
    compute: dict[str, int] | None = None
    time: dict[str, float | int | None] | None = None


@dataclass(frozen=True, slots=True)
class PreviewResult:
    conflicts: list[ConflictRecord]
    # Mirror docs/05 §3.13 response shape so the API layer can hand
    # this straight to pydantic.
    time_limit_checks: list[dict[str, Any]]


async def _load_link_or_raise(
    session: AsyncSession, *, account_link_id: int, user_id: int
) -> AccountLink:
    link = await session.get(AccountLink, account_link_id)
    if link is None or link.is_active != 1 or link.user_id != user_id:
        raise NoActiveLinkError(f"account_link {account_link_id} not active for user {user_id}")
    if link.source == "admin_declared":
        raise LinkNotVerifiedError(
            f"account_link {account_link_id} source='admin_declared' cannot act-as"
        )
    return link


async def _validate_item_time(
    session: AsyncSession,
    *,
    item: ItemDraft,
    now: datetime,
) -> Server:
    """Validate the timing portion of an item; returns the loaded server row."""
    # Normalize tz so the API layer can hand off either aware or naive
    # datetimes without surprises — MySQL TIMESTAMP returns naive, while
    # ``datetime.now(UTC)`` and pydantic-parsed ISO+Z are aware.
    start_at = item.start_at.replace(tzinfo=None) if item.start_at.tzinfo else item.start_at
    end_at = item.end_at.replace(tzinfo=None) if item.end_at.tzinfo else item.end_at
    now_naive = now.replace(tzinfo=None) if now.tzinfo else now
    if end_at <= start_at:
        raise InvalidTimeError(f"end_at must be > start_at (got {item.start_at} -> {item.end_at})")
    # Phase J — 5 minute grace so that recommend → confirm round-trips
    # don't fail when the recommender's "start now" candidate becomes
    # "60 s ago" by the time the user clicks confirm.
    from datetime import timedelta

    if start_at < now_naive - timedelta(minutes=5):
        raise InvalidTimeError(f"start_at {item.start_at!r} is in the past")
    server = await session.get(Server, item.server_id)
    if server is None:
        raise InvalidTimeError(f"server {item.server_id} not found")
    if server.max_reservation_hours is not None:
        requested_hours = (end_at - start_at).total_seconds() / 3600.0
        if requested_hours > server.max_reservation_hours:
            raise ReservationTooLongError(
                f"reservation {requested_hours:.2f}h exceeds "
                f"server.max_reservation_hours={server.max_reservation_hours}h",
                max_hours=server.max_reservation_hours,
                requested_hours=requested_hours,
            )
    return server


async def _window_active_reservations(
    session: AsyncSession, *, gpu_id: int, start_at: datetime, end_at: datetime
) -> list[Reservation]:
    """All scheduled/active reservations on a GPU that overlap [start, end).

    ``with_for_update()`` makes this a *locking* read, which closes the
    double-booking race on the create/modify paths. The conflict check is
    application-level (no DB constraint can express range-overlap), so
    under the InnoDB default REPEATABLE READ two concurrent bookings would
    each take a non-locking snapshot, both see "no conflict", and both
    insert. A locking read instead (a) reads the latest committed rows
    (not a stale snapshot) and (b) takes next-key/gap locks on the
    ``idx_res_gpu_time`` range — even when the range is empty — so a second
    booking for the same GPU + overlapping window blocks until the first
    commits, then re-reads and correctly sees the conflict. Locks are
    scoped to the one GPU's index range, so different GPUs never contend.
    (The module comment's "caller wraps this in SERIALIZABLE" was never
    actually wired at the API layer; this lock is the real enforcement.)
    """
    result = await session.execute(
        select(Reservation)
        .where(
            and_(
                Reservation.gpu_id == gpu_id,
                Reservation.status.in_(ACTIVE_STATUSES),
                Reservation.start_at < end_at,
                Reservation.end_at > start_at,
            )
        )
        .with_for_update()
    )
    return list(result.scalars().all())


async def _lock_reservation(session: AsyncSession, reservation_id: int) -> Reservation | None:
    """Load one reservation with a *locking* current read.

    Concurrency audit cluster A: every "check status then change it" entry
    must read the row under ``FOR UPDATE`` so it serializes against the
    scheduler tick and other writers, instead of trusting the InnoDB
    REPEATABLE READ snapshot. ``populate_existing=True`` is load-bearing:
    without it the ORM returns the (possibly stale) instance already in
    the session's identity map — e.g. the SP-5 cancel path keeps the row
    cached as ``active`` across its ~10s await while the scheduler commits
    ``failed`` — so the lock would be taken but the in-memory ``status``
    would still read stale and the guard would wrongly pass. With it, the
    locked row's attributes are overwritten from the latest committed
    values.
    """
    result = await session.execute(
        select(Reservation)
        .where(Reservation.id == reservation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


def _classify_conflict(
    new_item: ItemDraft, in_window: Sequence[Reservation], gpu: Gpu
) -> ConflictRecord | None:
    """Apply the 3 (+ compute_exceeded Q3) conflict rules to one input row.

    Returns ``None`` if the row is conflict-free. ``input_index`` is left
    at -1; the caller fills it in.
    """
    if not in_window:
        return None
    if new_item.gpu_memory_mb is None:
        # exclusive new — if any in-window row is shared, this is the
        # mix case (doc §5.13 "反之亦然"); otherwise an exclusive collision.
        shared_rows = [r for r in in_window if r.gpu_memory_mb is not None]
        if shared_rows:
            return ConflictRecord(
                input_index=-1,
                type="mix_exclusive_shared",
                conflicting_reservation_ids=[r.id for r in shared_rows],
            )
        return ConflictRecord(
            input_index=-1,
            type="exclusive_conflict",
            conflicting_reservation_ids=[r.id for r in in_window],
        )
    # shared new — first refuse if any in-window row is exclusive.
    exclusive_rows = [r for r in in_window if r.gpu_memory_mb is None]
    if exclusive_rows:
        return ConflictRecord(
            input_index=-1,
            type="mix_exclusive_shared",
            conflicting_reservation_ids=[r.id for r in exclusive_rows],
        )
    # all in-window are shared — sum memory + sum compute_pct.
    used_mb = sum(r.gpu_memory_mb or 0 for r in in_window)
    total_mb = gpu.memory_total_mb or 0
    if total_mb and used_mb + new_item.gpu_memory_mb > total_mb:
        return ConflictRecord(
            input_index=-1,
            type="memory_exceeded",
            conflicting_reservation_ids=[r.id for r in in_window],
            memory={
                "used_mb": used_mb,
                "would_use_mb": new_item.gpu_memory_mb,
                "total_mb": total_mb,
                "exceeds_by_mb": used_mb + new_item.gpu_memory_mb - total_mb,
            },
        )
    if new_item.gpu_compute_share_pct is not None:
        used_pct = sum(r.gpu_compute_share_pct or 0 for r in in_window)
        if used_pct + new_item.gpu_compute_share_pct > 100:
            return ConflictRecord(
                input_index=-1,
                type="compute_exceeded",
                conflicting_reservation_ids=[
                    r.id for r in in_window if r.gpu_compute_share_pct is not None
                ],
                compute={
                    "used_pct": used_pct,
                    "would_use_pct": new_item.gpu_compute_share_pct,
                    "exceeds_by_pct": used_pct + new_item.gpu_compute_share_pct - 100,
                },
            )
    return None


# Phase H — Cross-page sync (docs/05 §4.3 reservation.status_change).
# A single ws event type carries all reservation lifecycle changes
# (created / cancelled / scheduled→active→completed/failed). The
# discriminator is ``change`` inside the payload, not the envelope
# type — this keeps docs/05 §4.3 untouched (Phase 1 sealed) while
# letting the grid / floating card / My Reservations all subscribe via
# the existing ``onReservationStatusChange`` listener.
_LifecycleChange = Literal["created", "cancelled", "transition"]


def _serialize_reservation_for_ws(row: Reservation, *, change: _LifecycleChange) -> dict[str, Any]:
    return {
        "reservation_id": row.id,
        "change": change,
        "status": row.status,
        "user_id": row.user_id,
        "server_id": row.server_id,
        "gpu_id": row.gpu_id,
        "account_link_id": row.account_link_id,
        "group_id": row.group_id,
        "start_at": row.start_at.isoformat() if row.start_at else None,
        "end_at": row.end_at.isoformat() if row.end_at else None,
        "gpu_memory_mb": row.gpu_memory_mb,
        "gpu_compute_share_pct": row.gpu_compute_share_pct,
        "cancelled_at": row.cancelled_at.isoformat() if row.cancelled_at else None,
        "cancellation_reason": row.cancellation_reason,
    }


async def _push_reservation_lifecycle(
    *,
    row: Reservation,
    change: _LifecycleChange,
) -> None:
    """Push a ``reservation.status_change`` frame to the owner's sockets.

    Soft-fails: any WS-layer error is logged but never raised — the DB
    write is the truth and the browser catches up via REST on reconnect.
    Per docs/05 §4.5 this event is unthrottled.
    """
    frame = {
        "type": "reservation.status_change",
        "id": str(uuid.uuid4()),
        "ts": datetime.now(UTC).isoformat(),
        "payload": _serialize_reservation_for_ws(row, change=change),
    }
    try:
        await ws_user.push_to_user(row.user_id, frame)
    except Exception as exc:
        _log.warning(
            "reservation.ws_push_failed",
            reservation_id=row.id,
            change=change,
            error=str(exc),
        )


async def _reservation_cta_url(session: AsyncSession, row: Reservation) -> str:
    """Best-effort route to an existing SPA reservation surface."""
    link = await session.get(AccountLink, row.account_link_id)
    if link is not None:
        return f"/me/accounts/{link.physical_account_id}/reservations?highlight={row.id}"
    return "/me/all-reservations"


async def preview_conflicts(
    session: AsyncSession,
    *,
    items: Sequence[ItemDraft],
    user_id: int,
    now: datetime,
) -> PreviewResult:
    """Non-mutating equivalent of ``create_reservation_batch``.

    Returns one ConflictRecord per *conflicting* input item (so a clean
    submission returns ``conflicts=[]``). The ``time_too_long`` check is
    emitted as a parallel record (it doesn't block UI submission but the
    UI should disable Confirm because the real POST will return 422).
    Per-item ``time_limit_checks`` mirrors docs/05 §3.13 shape.
    """
    conflicts: list[ConflictRecord] = []
    time_limit_checks: list[dict[str, Any]] = []

    # Match ``_validate_item_time`` — 5-minute grace absorbs clock skew
    # between the browser and the backend, plus the recommend → confirm
    # round-trip. Without it preview rejects rows that ``create`` would
    # accept, and the UI would show "选区有冲突" on slots the user can
    # actually book.
    from datetime import timedelta

    grace = now - timedelta(minutes=5)
    for idx, item in enumerate(items):
        # Time / server / max_hours check (non-throwing version — preview
        # collects rather than 422-bails so the UI can paint each cell).
        if item.end_at <= item.start_at or item.start_at < grace:
            conflicts.append(
                ConflictRecord(
                    input_index=idx,
                    type="invalid_time",
                    conflicting_reservation_ids=[],
                )
            )
            time_limit_checks.append(
                {
                    "input_index": idx,
                    "max_hours": None,
                    "requested_hours": 0.0,
                    "would_exceed": False,
                }
            )
            continue
        server = await session.get(Server, item.server_id)
        if server is None:
            conflicts.append(
                ConflictRecord(
                    input_index=idx,
                    type="invalid_time",
                    conflicting_reservation_ids=[],
                )
            )
            time_limit_checks.append(
                {
                    "input_index": idx,
                    "max_hours": None,
                    "requested_hours": 0.0,
                    "would_exceed": False,
                }
            )
            continue
        requested_hours = (item.end_at - item.start_at).total_seconds() / 3600.0
        max_hours = server.max_reservation_hours
        would_exceed = max_hours is not None and requested_hours > max_hours
        time_limit_checks.append(
            {
                "input_index": idx,
                "max_hours": max_hours,
                "requested_hours": round(requested_hours, 2),
                "would_exceed": would_exceed,
            }
        )
        if would_exceed:
            conflicts.append(
                ConflictRecord(
                    input_index=idx,
                    type="time_too_long",
                    conflicting_reservation_ids=[],
                    time={
                        "max_hours": max_hours,
                        "requested_hours": round(requested_hours, 2),
                    },
                )
            )
            # don't continue — still surface in-window overlap too.

        # Phase J — Mode 3 (gpu_id NULL) skips the GPU conflict matrix.
        # The row doesn't occupy a GPU so it can't overlap with anything;
        # the API layer caller separately validates the "script required"
        # rule, so a None here in preview just means "no GPU conflict".
        if item.gpu_id is None:
            continue
        gpu = await session.get(Gpu, item.gpu_id)
        if gpu is None:
            conflicts.append(
                ConflictRecord(
                    input_index=idx,
                    type="invalid_time",
                    conflicting_reservation_ids=[],
                )
            )
            continue
        in_window = await _window_active_reservations(
            session, gpu_id=item.gpu_id, start_at=item.start_at, end_at=item.end_at
        )
        conflict = _classify_conflict(item, in_window, gpu)
        if conflict is not None:
            # ConflictRecord is frozen; re-create with the populated index.
            conflicts.append(
                ConflictRecord(
                    input_index=idx,
                    type=conflict.type,
                    conflicting_reservation_ids=conflict.conflicting_reservation_ids,
                    memory=conflict.memory,
                    compute=conflict.compute,
                    time=conflict.time,
                )
            )

    del user_id  # reserved — future per-user advisory checks
    return PreviewResult(conflicts=conflicts, time_limit_checks=time_limit_checks)


async def create_reservation_batch(
    session: AsyncSession,
    *,
    items: Sequence[ItemDraft],
    user_id: int,
    lab_id: int,
    script: str | None,
    script_scheduled_start_at: datetime | None,
    script_max_runtime_seconds: int | None,
    share_script: bool,
    now: datetime,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, list[Reservation]]:
    """Validate + insert a batch. All items share one ``group_id`` UUID4.

    Raises the typed errors above on the first failing rule so the API
    layer can map to 409/422 and surface ``details``.
    """
    if not items:
        raise InvalidTimeError("at least one item is required")
    if script is not None and len(script.encode("utf-8")) > SCRIPT_MAX_BYTES:
        raise ScriptTooLargeError(f"script body exceeds {SCRIPT_MAX_BYTES} bytes (4 KB)")

    # Phase J invariants — service-layer enforcement of the "gpu_id OR
    # script" rule the DB used to do via CHECK (MySQL refuses CHECK on
    # FK referential-action columns). Also forbid memory/compute share
    # for rows that don't occupy a GPU (Mode 3) since those fields are
    # meaningless without one.
    for idx, item in enumerate(items):
        if item.gpu_id is None:
            if script is None:
                raise InvalidTimeError(
                    f"item {idx}: gpu_id NULL requires a script (empty row not allowed)"
                )
            if item.gpu_memory_mb is not None or item.gpu_compute_share_pct is not None:
                raise InvalidTimeError(
                    f"item {idx}: gpu_memory_mb / gpu_compute_share_pct must be NULL "
                    "when gpu_id is NULL (no GPU to share)"
                )

    # Pre-pass: validate every item (time / server / link / max_hours)
    # before touching the conflict matrix. Each failure raises a typed
    # error so the API can map to 422.
    servers_by_id: dict[int, Server] = {}
    link_by_id: dict[int, AccountLink] = {}
    for item in items:
        servers_by_id[item.server_id] = await _validate_item_time(session, item=item, now=now)
        link_by_id[item.account_link_id] = await _load_link_or_raise(
            session, account_link_id=item.account_link_id, user_id=user_id
        )
        if script_scheduled_start_at is not None and not (
            item.start_at <= script_scheduled_start_at < item.end_at
        ):
            raise InvalidTimeError(
                "script_scheduled_start_at must satisfy "
                "start_at <= value < end_at for every item in the group"
            )

    # Conflict check: raise the first conflict. Doc §5.13 prescribes
    # SERIALIZABLE — MySQL InnoDB default is REPEATABLE READ, so the
    # caller (api layer) wraps this in a SERIALIZABLE transaction.
    # Phase J — Mode 3 rows (gpu_id NULL) skip the GPU conflict matrix:
    # they don't occupy a GPU so they can't overlap with anything.
    for item in items:
        if item.gpu_id is None:
            continue
        gpu = await session.get(Gpu, item.gpu_id)
        if gpu is None:
            raise InvalidTimeError(f"gpu {item.gpu_id} not found")
        in_window = await _window_active_reservations(
            session,
            gpu_id=item.gpu_id,
            start_at=item.start_at,
            end_at=item.end_at,
        )
        conflict = _classify_conflict(item, in_window, gpu)
        if conflict is None:
            continue
        if conflict.type == "exclusive_conflict":
            raise ReservationOverlapError(
                f"gpu {item.gpu_id} already has an active reservation "
                f"in [{item.start_at}, {item.end_at})",
                conflicting_reservation_ids=conflict.conflicting_reservation_ids,
            )
        if conflict.type == "mix_exclusive_shared":
            raise ReservationMixExclusiveSharedError(
                f"gpu {item.gpu_id} window contains an exclusive reservation; "
                "shared mode cannot be mixed in",
                conflicting_reservation_ids=conflict.conflicting_reservation_ids,
            )
        if conflict.type == "memory_exceeded":
            mem = conflict.memory or {}
            raise ReservationMemoryExceededError(
                f"gpu {item.gpu_id} shared-memory stack would exceed total",
                used_mb=mem.get("used_mb", 0),
                would_use_mb=mem.get("would_use_mb", 0),
                total_mb=mem.get("total_mb", 0),
                conflicting_reservation_ids=conflict.conflicting_reservation_ids,
            )
        if conflict.type == "compute_exceeded":
            cmp_ = conflict.compute or {}
            raise ReservationComputeExceededError(
                f"gpu {item.gpu_id} shared-compute stack would exceed 100%",
                used_pct=cmp_.get("used_pct", 0),
                would_use_pct=cmp_.get("would_use_pct", 0),
                conflicting_reservation_ids=conflict.conflicting_reservation_ids,
            )

    group_id = str(uuid.uuid4())
    created: list[Reservation] = []
    for idx, item in enumerate(items):
        # share_script semantics per docs/05 §3.12: true → every row carries
        # the script; false → only the first row does, the rest sit
        # script-less as time-only placeholders.
        carry_script = script if (share_script or idx == 0) else None
        row = Reservation(
            user_id=user_id,
            server_id=item.server_id,
            gpu_id=item.gpu_id,
            account_link_id=item.account_link_id,
            group_id=group_id,
            start_at=item.start_at,
            end_at=item.end_at,
            status=STATUS_SCHEDULED,
            gpu_memory_mb=item.gpu_memory_mb,
            gpu_compute_share_pct=item.gpu_compute_share_pct,
            script=carry_script,
            # Phase J fix — if the caller leaves script_scheduled_start_at
            # NULL (UI hint says "留空 = 预约开始时立即触发"), fall back to
            # the item's start_at. The dispatcher query uses ``<= now`` and
            # would skip a NULL row forever otherwise. Mode 3 (no GPU) hits
            # this path the most since users rarely set an explicit trigger
            # time when they only want "cron task at this time".
            script_scheduled_start_at=(
                (
                    script_scheduled_start_at
                    if script_scheduled_start_at is not None
                    else item.start_at
                )
                if carry_script
                else None
            ),
            script_max_runtime_seconds=script_max_runtime_seconds if carry_script else None,
        )
        session.add(row)
        created.append(row)
    await session.flush()

    for row in created:
        await audit_service.write(
            session,
            action="reservation.create",
            actor_user_id=user_id,
            lab_id=lab_id,
            target_type="reservation",
            target_id=row.id,
            target_lab_id=lab_id,
            target_server_id=row.server_id,
            payload={
                "gpu_id": row.gpu_id,
                "start_at": row.start_at.isoformat(),
                "end_at": row.end_at.isoformat(),
                "account_link_id": row.account_link_id,
                "group_id": row.group_id,
                "gpu_memory_mb": row.gpu_memory_mb,
                "gpu_compute_share_pct": row.gpu_compute_share_pct,
                "has_script": row.script is not None,
            },
            ip_address=request_ip,
            user_agent=user_agent,
        )
        await _push_reservation_lifecycle(row=row, change="created")
    del link_by_id  # reserved
    return group_id, created


async def cancel_reservation(
    session: AsyncSession,
    *,
    reservation_id: int,
    actor_user_id: int,
    actor_can_admin: bool,
    reason: str | None,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> Reservation:
    """Cancel one reservation. ``actor_can_admin`` lets lab_admin or a
    matching server_admin_grant override the owner check."""
    # Cluster-A fix: locking current read, not a snapshot. On the SP-5
    # path the API transaction has held a stale ``active`` snapshot across
    # a ~10s await while the scheduler may have committed ``failed`` /
    # ``completed``; the lock + refresh make the terminal guard below see
    # the real committed status instead of silently clobbering it.
    row = await _lock_reservation(session, reservation_id)
    if row is None:
        raise ReservationNotFoundError(f"reservation {reservation_id} not found")
    if row.status in TERMINAL_STATUSES:
        raise ReservationError(f"reservation {reservation_id} is already terminal ({row.status})")
    if not actor_can_admin and row.user_id != actor_user_id:
        raise CancelNotPermittedError(
            f"user {actor_user_id} cannot cancel reservation {reservation_id} "
            "(neither owner nor admin)"
        )
    # cancel previously bypassed the state machine entirely (only the
    # TERMINAL_STATUSES guard above). Assert the edge explicitly so an
    # illegal failed/completed -> cancelled is rejected even if a future
    # caller reaches here with a non-terminal-but-disallowed status.
    await _assert_transition_allowed(
        current=cast(ReservationStatus, row.status),
        target=STATUS_CANCELLED,
    )
    row.status = STATUS_CANCELLED
    row.cancelled_at = datetime.now(UTC)
    row.cancelled_by_user_id = actor_user_id
    row.cancellation_reason = reason
    await session.flush()
    await audit_service.write(
        session,
        action="reservation.cancel",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="reservation",
        target_id=row.id,
        target_lab_id=lab_id,
        target_server_id=row.server_id,
        payload={
            "owner_user_id": row.user_id,
            "reason": reason,
            "by_admin": actor_can_admin and row.user_id != actor_user_id,
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )
    # Phase 7 C3 — admin-on-other-user cancel surfaces a notification to
    # the owner. Owner-self-cancel is a deliberate action and does not
    # spawn a notification (UI already echoes it).
    if actor_can_admin and row.user_id != actor_user_id:
        await notification_service.create_notification(
            session,
            recipient_user_id=row.user_id,
            type="reservation.cancelled_by_other",
            severity="warn",
            title=f"Your reservation #{row.id} was cancelled by admin",
            body=reason,
            payload={
                "reservation_id": row.id,
                "actor_user_id": actor_user_id,
                "reason": reason,
            },
            cta_url=await _reservation_cta_url(session, row),
        )
    # Phase H — cross-page sync (grid / floating card / My Reservations).
    await _push_reservation_lifecycle(row=row, change="cancelled")
    return row


async def cancel_group(
    session: AsyncSession,
    *,
    group_id: str,
    actor_user_id: int,
    actor_can_admin: bool,
    reason: str | None,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> list[Reservation]:
    """Cancel every still-active row of a group. Returns the rows touched."""
    # Cluster-A fix: locking read so a concurrent cancel of the same group
    # blocks, then re-reads post-commit and finds zero still-active rows
    # (raising below) instead of double-cancelling / double-auditing.
    result = await session.execute(
        select(Reservation)
        .where(
            Reservation.group_id == group_id,
            Reservation.status.in_(ACTIVE_STATUSES),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    rows = list(result.scalars().all())
    if not rows:
        raise ReservationNotFoundError(f"no active reservations found for group {group_id!r}")
    # Permission is uniform across the group (one owner per group).
    owner_id = rows[0].user_id
    if not actor_can_admin and owner_id != actor_user_id:
        raise CancelNotPermittedError(
            f"user {actor_user_id} cannot cancel group {group_id} (not owner / admin)"
        )
    running_script_ids = [
        row.id
        for row in rows
        if row.status == STATUS_ACTIVE and row.script_status == SCRIPT_RUNNING
    ]
    if running_script_ids:
        raise GroupCancelRunningScriptError(
            "group cancel refused because running scripts must be stopped individually first",
            running_reservation_ids=running_script_ids,
        )
    now = datetime.now(UTC)
    for row in rows:
        row.status = STATUS_CANCELLED
        row.cancelled_at = now
        row.cancelled_by_user_id = actor_user_id
        row.cancellation_reason = reason
    await session.flush()
    await audit_service.write(
        session,
        action="reservation.cancel.group",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="reservation_group",
        target_id=None,
        target_lab_id=lab_id,
        target_server_id=rows[0].server_id,
        payload={
            "group_id": group_id,
            "count": len(rows),
            "reason": reason,
            "owner_user_id": owner_id,
            "by_admin": actor_can_admin and owner_id != actor_user_id,
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )
    # Phase H — cross-page sync. One frame per row keeps the grid /
    # floating card / My Reservations subscribers symmetric with single
    # cancel; ``group_id`` in the payload lets the frontend coalesce.
    for row in rows:
        await _push_reservation_lifecycle(row=row, change="cancelled")
    return rows


async def modify_reservation(
    session: AsyncSession,
    *,
    reservation_id: int,
    actor_user_id: int,
    new_start_at: datetime | None,
    new_end_at: datetime | None,
    new_script: str | None,
    new_script_scheduled_start_at: datetime | None = None,
    new_script_max_runtime_seconds: int | None = None,
    lab_id: int,
    now: datetime,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> Reservation:
    """Edit time / script while still ``status='scheduled'``. Owner only.

    T90 — the two ``new_script_*`` knobs let the Scripts page edit the
    cron timing after the reservation exists. Hard guard: once the
    scheduler has begun dispatching (``script_dispatch_started_at``
    set) or the agent has acked (``script_status`` set), the timing
    fields are immutable — modifying them on the fly would race the
    outbox tick.
    """
    # Cluster-A fix: locking current read so the ``scheduled`` guard below
    # sees the latest committed status. Otherwise a snapshot taken just
    # before the scheduler promotes the row to ``active`` would let an
    # edit rewrite the time window of an already-running reservation.
    row = await _lock_reservation(session, reservation_id)
    if row is None:
        raise ReservationNotFoundError(f"reservation {reservation_id} not found")
    if row.status != STATUS_SCHEDULED:
        raise ReservationError(
            f"reservation {reservation_id} is not editable in status {row.status!r}"
        )
    if row.user_id != actor_user_id:
        raise CancelNotPermittedError(
            f"user {actor_user_id} is not the owner of reservation {reservation_id}"
        )
    # Guard: script already dispatched / running — timing knobs frozen.
    timing_edit = (
        new_script_scheduled_start_at is not None or new_script_max_runtime_seconds is not None
    )
    if timing_edit and (
        row.script_status is not None or row.script_dispatch_started_at is not None
    ):
        raise ReservationError(
            f"script for reservation {reservation_id} has already been dispatched; "
            f"timing fields cannot be modified"
        )
    if new_start_at is not None:
        row.start_at = new_start_at
    if new_end_at is not None:
        row.end_at = new_end_at
    if new_start_at is not None or new_end_at is not None:
        item = ItemDraft(
            server_id=row.server_id,
            gpu_id=row.gpu_id,
            start_at=row.start_at,
            end_at=row.end_at,
            account_link_id=row.account_link_id,
            gpu_memory_mb=row.gpu_memory_mb,
            gpu_compute_share_pct=row.gpu_compute_share_pct,
        )
        await _validate_item_time(session, item=item, now=now)
        # Phase J — Mode 3 rows have no GPU to conflict against.
        if row.gpu_id is not None:
            gpu = await session.get(Gpu, row.gpu_id)
            if gpu is None:
                raise InvalidTimeError(f"gpu {row.gpu_id} not found")
            in_window = [
                r
                for r in await _window_active_reservations(
                    session, gpu_id=row.gpu_id, start_at=row.start_at, end_at=row.end_at
                )
                if r.id != row.id
            ]
            conflict = _classify_conflict(item, in_window, gpu)
            if conflict is not None:
                raise ReservationOverlapError(
                    f"modify would conflict on gpu {row.gpu_id}",
                    conflicting_reservation_ids=conflict.conflicting_reservation_ids,
                )
    if new_script is not None:
        if len(new_script.encode("utf-8")) > SCRIPT_MAX_BYTES:
            raise ScriptTooLargeError(f"script body exceeds {SCRIPT_MAX_BYTES} bytes (4 KB)")
        row.script = new_script
        # Keep PATCH semantics aligned with create + UI copy:
        # leaving the trigger blank means "run when the reservation starts".
        if row.script_scheduled_start_at is None and new_script_scheduled_start_at is None:
            row.script_scheduled_start_at = row.start_at
    if new_script_scheduled_start_at is not None:
        # Normalize tz before comparing — MySQL TIMESTAMP returns naive
        # UTC while the API hands in aware datetimes (same as ~L270).
        if new_script_scheduled_start_at.tzinfo is not None:
            new_script_scheduled_start_at = new_script_scheduled_start_at.astimezone(UTC).replace(
                tzinfo=None
            )
        # CheckConstraint ck_res_script_time mirrors this. We check up
        # front so the API surfaces a typed error rather than a 500.
        if not (row.start_at <= new_script_scheduled_start_at < row.end_at):
            raise InvalidTimeError(
                f"script_scheduled_start_at must be in "
                f"[{row.start_at.isoformat()}, {row.end_at.isoformat()})"
            )
        row.script_scheduled_start_at = new_script_scheduled_start_at
    if new_script_max_runtime_seconds is not None:
        row.script_max_runtime_seconds = new_script_max_runtime_seconds
    await session.flush()
    await audit_service.write(
        session,
        action="reservation.modify",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="reservation",
        target_id=row.id,
        target_lab_id=lab_id,
        target_server_id=row.server_id,
        payload={
            "fields_changed": [
                k
                for k, v in (
                    ("start_at", new_start_at),
                    ("end_at", new_end_at),
                    ("script", new_script),
                    ("script_scheduled_start_at", new_script_scheduled_start_at),
                    ("script_max_runtime_seconds", new_script_max_runtime_seconds),
                )
                if v is not None
            ],
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return row


async def list_reservations(
    session: AsyncSession,
    *,
    server_id: int | None = None,
    gpu_id: int | None = None,
    user_id: int | None = None,
    starts_after: datetime | None = None,
    ends_before: datetime | None = None,
    statuses: Sequence[str] | None = None,
    limit: int = 500,
) -> list[Reservation]:
    """Generic reservation listing — powers /reservations + the grid."""
    stmt = select(Reservation)
    if server_id is not None:
        stmt = stmt.where(Reservation.server_id == server_id)
    if gpu_id is not None:
        stmt = stmt.where(Reservation.gpu_id == gpu_id)
    if user_id is not None:
        stmt = stmt.where(Reservation.user_id == user_id)
    if starts_after is not None:
        stmt = stmt.where(Reservation.end_at > starts_after)
    if ends_before is not None:
        stmt = stmt.where(Reservation.start_at < ends_before)
    if statuses:
        stmt = stmt.where(Reservation.status.in_(tuple(statuses)))
    stmt = stmt.order_by(Reservation.start_at).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_for_link(
    session: AsyncSession,
    *,
    account_link_id: int,
    statuses: Sequence[str] | None = None,
    limit: int = 200,
) -> list[Reservation]:
    """Reservations under one account_link — powers /me/accounts/:pa_id/reservations."""
    stmt = select(Reservation).where(Reservation.account_link_id == account_link_id)
    if statuses:
        stmt = stmt.where(Reservation.status.in_(tuple(statuses)))
    stmt = stmt.order_by(Reservation.start_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ─── Phase 6: scheduler + lifecycle transitions
#
# The four helpers below are the only places ``reservation.status`` /
# ``reservation.script_status`` ever change outside cancel paths. Each
# enforces ``_ALLOWED_TRANSITIONS`` so an out-of-order scheduler tick
# or stray agent push cannot drive a row into an illegal state, and
# every transition writes a ``reservation.transition`` audit row so
# the chain is reconstructable (brief P6-12).
#
# ``actor_user_id=None`` is the scheduler / system path; cancel-driven
# transitions still flow through ``cancel_reservation`` / ``cancel_group``
# above so they stay paired with the cancel audit action.


async def _assert_transition_allowed(
    *, current: ReservationStatus, target: ReservationStatus
) -> None:
    if target not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise ReservationIllegalTransitionError(
            f"reservation status transition {current} -> {target} is not allowed",
            current_status=current,
            attempted_status=target,
        )


async def _write_transition_audit(
    session: AsyncSession,
    *,
    row: Reservation,
    from_status: ReservationStatus,
    to_status: ReservationStatus,
    trigger: str,
    actor_user_id: int | None,
    lab_id: int,
    extra_payload: dict[str, Any] | None = None,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "from": from_status,
        "to": to_status,
        "trigger": trigger,
        "gpu_id": row.gpu_id,
    }
    if extra_payload is not None:
        payload.update(extra_payload)
    await audit_service.write(
        session,
        action="reservation.transition",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="reservation",
        target_id=row.id,
        target_lab_id=lab_id,
        target_server_id=row.server_id,
        payload=payload,
        ip_address=request_ip,
        user_agent=user_agent,
    )


async def transition_to_active(
    session: AsyncSession,
    *,
    reservation: Reservation,
    lab_id: int,
    trigger: str = "scheduler_start_at",
    actor_user_id: int | None = None,
    now: datetime | None = None,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> Reservation:
    """Promote a scheduled row to active. Scheduler tick path."""
    from_status: ReservationStatus = reservation.status  # type: ignore[assignment]
    await _assert_transition_allowed(current=from_status, target=STATUS_ACTIVE)
    reservation.status = STATUS_ACTIVE
    await session.flush()
    await _write_transition_audit(
        session,
        row=reservation,
        from_status=from_status,
        to_status=STATUS_ACTIVE,
        trigger=trigger,
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        extra_payload={"at": (now or datetime.now(UTC)).isoformat()},
        request_ip=request_ip,
        user_agent=user_agent,
    )
    await notification_service.create_notification(
        session,
        recipient_user_id=reservation.user_id,
        type="reservation.started",
        severity="info",
        title=f"Your reservation #{reservation.id} has started",
        payload={
            "reservation_id": reservation.id,
            "server_id": reservation.server_id,
            "gpu_id": reservation.gpu_id,
        },
        cta_url=await _reservation_cta_url(session, reservation),
    )
    await _push_reservation_lifecycle(row=reservation, change="transition")
    return reservation


async def transition_to_completed(
    session: AsyncSession,
    *,
    reservation: Reservation,
    lab_id: int,
    trigger: str = "scheduler_end_at",
    actor_user_id: int | None = None,
    now: datetime | None = None,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> Reservation:
    """Close an active row whose end_at has passed and whose script (if any)
    did not fail / get killed. Scheduler tick path."""
    from_status: ReservationStatus = reservation.status  # type: ignore[assignment]
    await _assert_transition_allowed(current=from_status, target=STATUS_COMPLETED)
    reservation.status = STATUS_COMPLETED
    await session.flush()
    await _write_transition_audit(
        session,
        row=reservation,
        from_status=from_status,
        to_status=STATUS_COMPLETED,
        trigger=trigger,
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        extra_payload={
            "at": (now or datetime.now(UTC)).isoformat(),
            "script_status": reservation.script_status,
        },
        request_ip=request_ip,
        user_agent=user_agent,
    )
    await notification_service.create_notification(
        session,
        recipient_user_id=reservation.user_id,
        type="reservation.completed",
        severity="info",
        title=f"Your reservation #{reservation.id} completed",
        payload={
            "reservation_id": reservation.id,
            "script_status": reservation.script_status,
        },
        cta_url=await _reservation_cta_url(session, reservation),
    )
    await _push_reservation_lifecycle(row=reservation, change="transition")
    return reservation


async def transition_to_failed(
    session: AsyncSession,
    *,
    reservation: Reservation,
    lab_id: int,
    reason: str,
    trigger: str = "scheduler_end_at_script_failed",
    actor_user_id: int | None = None,
    now: datetime | None = None,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> Reservation:
    """Close an active row whose end_at has passed AND whose script failed
    or was killed. doc/02 §5.13 line 1148: reservation occupies the slot
    until end_at regardless of script outcome — failure surfaces here."""
    from_status: ReservationStatus = reservation.status  # type: ignore[assignment]
    await _assert_transition_allowed(current=from_status, target=STATUS_FAILED)
    reservation.status = STATUS_FAILED
    # Borrow cancellation_reason to carry the failure reason so a single
    # column captures "why the row ended non-cleanly" for both cancelled
    # and failed terminal states.
    if not reservation.cancellation_reason:
        reservation.cancellation_reason = reason
    await session.flush()
    await _write_transition_audit(
        session,
        row=reservation,
        from_status=from_status,
        to_status=STATUS_FAILED,
        trigger=trigger,
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        extra_payload={
            "at": (now or datetime.now(UTC)).isoformat(),
            "script_status": reservation.script_status,
            "reason": reason,
        },
        request_ip=request_ip,
        user_agent=user_agent,
    )
    await notification_service.create_notification(
        session,
        recipient_user_id=reservation.user_id,
        type="reservation.failed",
        severity="warn",
        title=f"Your reservation #{reservation.id} failed: {reason}",
        payload={
            "reservation_id": reservation.id,
            "reason": reason,
            "script_status": reservation.script_status,
        },
        cta_url=await _reservation_cta_url(session, reservation),
    )
    await _push_reservation_lifecycle(row=reservation, change="transition")
    return reservation


async def update_script_status(
    session: AsyncSession,
    *,
    reservation: Reservation,
    new_script_status: ScriptStatus,
    lab_id: int,
    trigger: str,
    exit_code: int | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    actor_user_id: int | None = None,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> Reservation:
    """Sync the agent-side script lifecycle into the row.

    Reservation.status stays unchanged here — only ``script_status``
    and the matching ``script_started_at`` / ``script_finished_at`` /
    ``script_exit_code`` columns. The scheduler decides whether the
    row ultimately ends ``completed`` or ``failed`` at end_at.
    """
    current: ScriptStatus | None = reservation.script_status  # type: ignore[assignment]
    if new_script_status not in _ALLOWED_SCRIPT_TRANSITIONS.get(current, frozenset()):
        raise ReservationIllegalTransitionError(
            f"reservation.script_status {current!r} -> {new_script_status!r} not allowed",
            current_status=current or "null",
            attempted_status=new_script_status,
        )
    reservation.script_status = new_script_status
    if new_script_status == SCRIPT_RUNNING and started_at is not None:
        reservation.script_started_at = started_at
    if new_script_status in (SCRIPT_COMPLETED, SCRIPT_FAILED, SCRIPT_KILLED):
        if finished_at is not None:
            reservation.script_finished_at = finished_at
        if exit_code is not None:
            reservation.script_exit_code = exit_code
    await session.flush()
    await audit_service.write(
        session,
        action="reservation.script.lifecycle",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="reservation",
        target_id=reservation.id,
        target_lab_id=lab_id,
        target_server_id=reservation.server_id,
        payload={
            "from": current,
            "to": new_script_status,
            "trigger": trigger,
            "exit_code": exit_code,
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return reservation


async def append_script_log_tail(
    session: AsyncSession,
    *,
    reservation: Reservation,
    text: str,
    max_chars: int = SCRIPT_LOG_TAIL_MAX_CHARS,
) -> Reservation:
    """Append a script output chunk to the bounded UI-visible log tail.

    The authoritative full log remains on the agent host at
    ``script_log_path``. This field is intentionally just a recent tail
    so reservation list APIs do not become a log archive.
    """
    if text == "":
        return reservation

    current = reservation.script_log_tail_text or ""
    combined = current + text
    was_truncated = bool(reservation.script_log_tail_truncated)
    if len(combined) > max_chars:
        reservation.script_log_tail_text = combined[-max_chars:]
        reservation.script_log_tail_truncated = 1
    else:
        reservation.script_log_tail_text = combined
        reservation.script_log_tail_truncated = 1 if was_truncated else 0
    await session.flush()
    return reservation


async def mark_script_dispatched(
    session: AsyncSession,
    *,
    reservation: Reservation,
    lab_id: int,
    now: datetime,
    actor_user_id: int | None = None,
) -> Reservation:
    """Phase 7 C0 (B 方案) — scheduler-side bookkeeping for one dispatch.

    Runs inside the scheduler's SERIALIZABLE tick: flips
    ``script_status: None -> running`` *and* stamps
    ``script_dispatch_started_at = now`` together so the watchdog has a
    grep-able "dispatch in flight" marker. The actual RPC fires
    *after* the tick commits, in a fire-and-forget ``_dispatch_one``
    coroutine (see ``reservation_scheduler.py``).

    Asserts:
      * ``reservation.status == 'active'`` (only active rows dispatch);
      * ``reservation.script_status is None`` (idempotent gate — second
        tick of the same reservation never re-dispatches).

    Writes a ``reservation.script.dispatched`` audit row distinct from
    the existing ``reservation.script.lifecycle`` family so the audit
    chain reads cleanly:

      1. ``reservation.script.dispatched``     — scheduler this row.
      2. ``reservation.script.lifecycle.started`` — agent acked + dispatch
         column cleared (see ``script_lifecycle_service.on_script_started``).
      3. ``reservation.script.lifecycle``      — terminal NULL -> running
         (legacy path only) or running -> completed/failed/killed.
    """
    if reservation.status != STATUS_ACTIVE:
        raise ReservationIllegalTransitionError(
            f"mark_script_dispatched requires status='active', got {reservation.status!r}",
            current_status=reservation.status,
            attempted_status="script.dispatched",
        )
    current_script: ScriptStatus | None = reservation.script_status  # type: ignore[assignment]
    if current_script is not None:
        raise ReservationIllegalTransitionError(
            (f"mark_script_dispatched requires script_status=NULL, got {current_script!r}"),
            current_status=str(current_script),
            attempted_status=SCRIPT_RUNNING,
        )
    reservation.script_status = SCRIPT_RUNNING
    reservation.script_dispatch_started_at = now
    await session.flush()
    await audit_service.write(
        session,
        action="reservation.script.dispatched",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="reservation",
        target_id=reservation.id,
        target_lab_id=lab_id,
        target_server_id=reservation.server_id,
        payload={
            "trigger": "scheduler_dispatch",
            "dispatch_started_at": now.isoformat(),
        },
    )
    return reservation


# Re-export so callers can ``except account_link_service.X`` consistently.
AdminDeclaredCannotActAsError = account_link_service.AdminDeclaredCannotActAsError
