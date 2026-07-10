"""Phase 6 reservation scheduler — APScheduler 30s tick.

This module owns the periodic job that drives the reservation status
state machine (docs/02 §5.13). Each tick:

1. Promotes scheduled rows whose ``start_at`` has arrived to ``active``.
2. Closes active rows whose ``end_at`` has arrived: ``completed`` when
   the attached script (if any) did not fail / get killed; ``failed``
   when it did. doc/02 §5.13 line 1148 — the reservation always
   occupies the slot until ``end_at`` regardless of script outcome.
3. **Phase 7 C0** — Dispatches scripts whose ``script_scheduled_start_at``
   has arrived. Sets ``script_status='running'`` +
   ``script_dispatch_started_at=now`` inside the SERIALIZABLE tick
   (so a second tick cannot double-dispatch), then fires the real
   ``backend.script.execute`` RPC after the tick commits.
4. **Phase 7 C0 watchdog** — Reaps rows whose
   ``script_dispatch_started_at`` has stayed non-NULL for > 60 s:
   either the agent never acked (re-fire up to 3 attempts then
   ``transition_to_failed``) or the lifecycle handler dropped the
   clear (self-heal by looking at the audit chain).

Every tick runs inside an explicit SERIALIZABLE transaction
(`SET TRANSACTION ISOLATION LEVEL SERIALIZABLE`) so the scheduler
cannot lose updates to a concurrent ``create_reservation_batch`` call
(brief P6-3 / FU-25). MySQL 8 InnoDB defaults to REPEATABLE READ —
without this opt-in, the scheduler could read stale ``in_window``
results and double-fire a transition.

The scheduler is opt-in via :attr:`Settings.scheduler_enabled` so
test fixtures do not spawn a background tick (see Phase 6 C6 for
tests that explicitly start one with a shortened interval).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from corelab_protocol import ExecuteScriptRequest
from sqlalchemy import func, select, text

from ..db import get_session_factory
from ..logging_setup import get_logger
from ..models import AccountLink, AuditLog, PhysicalAccount, Reservation, Server
from . import agent_rpc, audit_service, reservation_service
from ._serializable_retry import with_serializable_retry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_log = get_logger("corelab.scheduler")


# Job id is stable so a re-add (e.g. interval change in dev) replaces the
# previous registration rather than silently spawning a parallel one.
_JOB_ID = "reservation_tick"

# Phase 7 C0 watchdog — a row whose script_dispatch_started_at has
# stayed set this long is considered stuck and triggers a retry / fail.
WATCHDOG_STUCK_SECONDS: Final[int] = 60
# Number of dispatch_attempt_failed audit rows that flip the watchdog
# from "retry" to "transition_to_failed". The initial dispatch counts
# as attempt 1 if it fails, so the third failure ends the chain.
WATCHDOG_MAX_DISPATCH_ATTEMPTS: Final[int] = 3


async def _server_lab_id(session: AsyncSession, server_id: int) -> int | None:
    """Look up the lab_id behind a server. The scheduler is process-wide
    (no per-request lab context) so the audit row borrows the
    reservation's server.lab_id instead of hardcoding 1."""
    server = await session.get(Server, server_id)
    if server is None:
        return None
    return int(server.lab_id)


async def _promote_due_scheduled(session: AsyncSession, *, now: datetime) -> int:
    """scheduled → active for every row whose start_at <= now."""
    result = await session.execute(
        select(Reservation).where(
            Reservation.status == reservation_service.STATUS_SCHEDULED,
            Reservation.start_at <= now,
        )
    )
    rows = list(result.scalars().all())
    promoted = 0
    for row in rows:
        lab_id = await _server_lab_id(session, row.server_id)
        if lab_id is None:
            _log.warning("scheduler.skip_orphan_server", reservation_id=row.id)
            continue
        await reservation_service.transition_to_active(
            session,
            reservation=row,
            lab_id=lab_id,
            trigger="scheduler_start_at",
            actor_user_id=None,
            now=now,
        )
        promoted += 1
    return promoted


async def _close_due_active(session: AsyncSession, *, now: datetime) -> tuple[int, int]:
    """active → completed/failed for every row whose end_at <= now.

    Returns (completed_count, failed_count).
    """
    result = await session.execute(
        select(Reservation).where(
            Reservation.status == reservation_service.STATUS_ACTIVE,
            Reservation.end_at <= now,
        )
    )
    rows = list(result.scalars().all())
    completed = 0
    failed = 0
    for row in rows:
        lab_id = await _server_lab_id(session, row.server_id)
        if lab_id is None:
            _log.warning("scheduler.skip_orphan_server", reservation_id=row.id)
            continue
        if row.script_status in (
            reservation_service.SCRIPT_FAILED,
            reservation_service.SCRIPT_KILLED,
        ):
            await reservation_service.transition_to_failed(
                session,
                reservation=row,
                lab_id=lab_id,
                reason=f"script_{row.script_status}",
                trigger="scheduler_end_at",
                actor_user_id=None,
                now=now,
            )
            failed += 1
        else:
            await reservation_service.transition_to_completed(
                session,
                reservation=row,
                lab_id=lab_id,
                trigger="scheduler_end_at",
                actor_user_id=None,
                now=now,
            )
            completed += 1
    return completed, failed


def _build_execute_payload(
    *,
    reservation: Reservation,
    linux_username: str,
) -> dict[str, Any]:
    """Compose the ``backend.script.execute`` payload from a reservation row.

    ``stdout_log_path_hint`` defaults to the §12.6.4 convention
    (``/home/<user>/.corelab/jobs/<reservation_id>.log``); the agent
    overrides this if the directory is unwritable.
    """
    assert reservation.script is not None, "caller must filter script IS NOT NULL"
    request = ExecuteScriptRequest(
        reservation_id=reservation.id,
        linux_username=linux_username,
        script=reservation.script,
        max_runtime_seconds=reservation.script_max_runtime_seconds,
        stdout_log_path_hint=(f"/home/{linux_username}/.corelab/jobs/{reservation.id}.log"),
    )
    return request.model_dump(mode="json")


async def _dispatch_due_scripts(session: AsyncSession, *, now: datetime) -> int:
    """Phase 7 C0 — find active rows ready to fire and mark them dispatched.

    Predicate ``script_status IS NULL`` is the idempotent gate: once a
    row is marked ``running`` (by this function or by an earlier tick),
    later ticks will not re-dispatch. The actual RPC fires *after* the
    transaction commits in :func:`reservation_tick`; this function only
    flips the bookkeeping columns + writes the dispatched audit.

    Returns the list of (reservation_id, server_id, payload) tuples
    queued for dispatch — the caller fires the RPCs once the
    SERIALIZABLE transaction has closed.
    """
    result = await session.execute(
        select(Reservation, PhysicalAccount.linux_username)
        .join(AccountLink, Reservation.account_link_id == AccountLink.id)
        .join(PhysicalAccount, AccountLink.physical_account_id == PhysicalAccount.id)
        .where(
            Reservation.status == reservation_service.STATUS_ACTIVE,
            Reservation.script.isnot(None),
            Reservation.script_scheduled_start_at <= now,
            Reservation.script_status.is_(None),
        )
    )
    rows = list(result.all())
    pending: list[tuple[int, int, dict[str, Any]]] = []
    for reservation, linux_username in rows:
        lab_id = await _server_lab_id(session, reservation.server_id)
        if lab_id is None:
            _log.warning("scheduler.skip_orphan_server", reservation_id=reservation.id)
            continue
        await reservation_service.mark_script_dispatched(
            session,
            reservation=reservation,
            lab_id=lab_id,
            now=now,
        )
        payload = _build_execute_payload(reservation=reservation, linux_username=linux_username)
        pending.append((reservation.id, reservation.server_id, payload))
    # Stash the pending list on the session for the caller — it cannot
    # be returned through the SERIALIZABLE block boundary because
    # session.begin() async-managers swallow the return value.
    session.info["_phase7_dispatch_pending"] = pending
    return len(pending)


async def _dispatch_one(
    *,
    reservation_id: int,
    server_id: int,
    payload: dict[str, Any],
) -> None:
    """Fire-and-forget RPC for a single reservation outside the tick txn.

    On RPC failure (offline / timeout / unexpected response) or an
    explicit agent rejection (``ok=false`` / ``started=false``), write
    a ``reservation.script.dispatch_attempt_failed`` audit row so the
    watchdog can count attempts; status stays ``running`` and
    ``script_dispatch_started_at`` stays set so the watchdog finds the
    row at the next tick.
    """
    try:
        response = await agent_rpc.request_response(
            server_id=server_id,
            frame_type="backend.script.execute",
            payload=payload,
            timeout_seconds=10.0,
        )
    except (
        agent_rpc.AgentOfflineError,
        agent_rpc.AgentRpcTimeoutError,
        agent_rpc.UnexpectedResponseTypeError,
    ) as exc:
        await _record_dispatch_attempt_failed(
            reservation_id=reservation_id,
            server_id=server_id,
            error_class=type(exc).__name__,
            error_message=str(exc),
        )
        return
    if response.get("ok") is not True or response.get("started") is not True:
        await _record_dispatch_attempt_failed(
            reservation_id=reservation_id,
            server_id=server_id,
            error_class="AgentRejectedScriptExecution",
            error_message=str(response.get("error") or response.get("detail") or response),
        )


async def _record_dispatch_attempt_failed(
    *,
    reservation_id: int,
    server_id: int,
    error_class: str,
    error_message: str,
) -> None:
    """Write the dispatch_attempt_failed audit row in its own session
    so it survives a caller-side rollback (the caller is a fire-and-forget
    task spawned post-commit)."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab_id = await _server_lab_id(session, server_id)
        await audit_service.write(
            session,
            action="reservation.script.dispatch_attempt_failed",
            actor_user_id=None,
            lab_id=lab_id,
            target_type="reservation",
            target_id=reservation_id,
            target_lab_id=lab_id,
            target_server_id=server_id,
            payload={
                "reason": error_message,
                "error_class": error_class,
            },
        )
    _log.warning(
        "scheduler.dispatch_attempt_failed",
        reservation_id=reservation_id,
        server_id=server_id,
        error_class=error_class,
    )


async def _count_dispatch_failures(session: AsyncSession, *, reservation_id: int) -> int:
    """How many dispatch_attempt_failed audit rows we have for this row."""
    result = await session.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "reservation.script.dispatch_attempt_failed",
            AuditLog.target_type == "reservation",
            AuditLog.target_id == reservation_id,
        )
    )
    return int(result.scalar_one() or 0)


async def _lifecycle_started_seen(session: AsyncSession, *, reservation_id: int) -> bool:
    """Has the lifecycle handler already received agent.script.started?

    The handler writes ``reservation.script.lifecycle.started`` whenever
    the agent acks; that audit row is the canonical "agent started"
    marker for the watchdog's self-heal path.
    """
    result = await session.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "reservation.script.lifecycle.started",
            AuditLog.target_type == "reservation",
            AuditLog.target_id == reservation_id,
        )
    )
    return int(result.scalar_one() or 0) > 0


async def _retry_stuck_dispatches(session: AsyncSession, *, now: datetime) -> tuple[int, int, int]:
    """Phase 7 C0 watchdog — reap stuck dispatches.

    Returns (cleared_count, retried_count, failed_count).
    """
    cutoff = now - timedelta(seconds=WATCHDOG_STUCK_SECONDS)
    result = await session.execute(
        select(Reservation).where(
            Reservation.status == reservation_service.STATUS_ACTIVE,
            Reservation.script_status == reservation_service.SCRIPT_RUNNING,
            Reservation.script_dispatch_started_at.isnot(None),
            Reservation.script_dispatch_started_at < cutoff,
        )
    )
    rows = list(result.scalars().all())
    pending_retry: list[tuple[int, int, dict[str, Any]]] = []
    cleared = 0
    retried = 0
    failed = 0
    for row in rows:
        lab_id = await _server_lab_id(session, row.server_id)
        if lab_id is None:
            _log.warning("scheduler.skip_orphan_server", reservation_id=row.id)
            continue
        # Self-heal: lifecycle.started audit already there but dispatch
        # column never cleared (handler bug / race) — just clear.
        if await _lifecycle_started_seen(session, reservation_id=row.id):
            row.script_dispatch_started_at = None
            await session.flush()
            cleared += 1
            _log.info(
                "scheduler.dispatch_clear_orphan",
                reservation_id=row.id,
            )
            continue
        attempts = await _count_dispatch_failures(session, reservation_id=row.id)
        if attempts >= WATCHDOG_MAX_DISPATCH_ATTEMPTS:
            await reservation_service.transition_to_failed(
                session,
                reservation=row,
                lab_id=lab_id,
                reason=(
                    f"dispatch_{WATCHDOG_MAX_DISPATCH_ATTEMPTS}x_failed_"
                    f"after_{WATCHDOG_STUCK_SECONDS}s"
                ),
                trigger="scheduler_watchdog_dispatch_giveup",
                actor_user_id=None,
                now=now,
            )
            failed += 1
            continue
        # Compose retry payload — same shape as initial dispatch.
        link_row = await session.execute(
            select(PhysicalAccount.linux_username)
            .join(AccountLink, AccountLink.physical_account_id == PhysicalAccount.id)
            .where(AccountLink.id == row.account_link_id)
        )
        linux_username = link_row.scalar_one_or_none()
        if linux_username is None:
            _log.warning(
                "scheduler.watchdog_skip_no_linux_user",
                reservation_id=row.id,
            )
            continue
        payload = _build_execute_payload(reservation=row, linux_username=linux_username)
        pending_retry.append((row.id, row.server_id, payload))
        row.script_dispatch_started_at = now  # bump timer
        await session.flush()
        await audit_service.write(
            session,
            action="reservation.script.dispatch_retry",
            actor_user_id=None,
            lab_id=lab_id,
            target_type="reservation",
            target_id=row.id,
            target_lab_id=lab_id,
            target_server_id=row.server_id,
            payload={
                "attempt_number": attempts + 1,
                "dispatch_started_at": now.isoformat(),
                "trigger": "watchdog",
            },
        )
        retried += 1
    session.info["_phase7_watchdog_retry_pending"] = pending_retry
    return cleared, retried, failed


async def _tick_body() -> tuple[
    list[tuple[int, int, dict[str, Any]]],
    list[tuple[int, int, dict[str, Any]]],
]:
    """One transactional pass of the tick — extracted so the FU-31
    deadlock-retry middleware can re-invoke it on InnoDB error 1213
    without leaking session state between attempts.

    Returns the (pending_dispatch, pending_retry) RPC queues the
    outer :func:`reservation_tick` fires after commit.
    """
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("SET SESSION TRANSACTION ISOLATION LEVEL SERIALIZABLE"))
        await session.commit()
        async with session.begin():
            now = datetime.now(UTC)
            try:
                promoted = await _promote_due_scheduled(session, now=now)
                completed, failed = await _close_due_active(session, now=now)
                dispatched = await _dispatch_due_scripts(session, now=now)
                cleared, retried, watchdog_failed = await _retry_stuck_dispatches(session, now=now)
            except Exception as exc:
                _log.warning("scheduler.tick_failed", error=str(exc))
                raise
            pending_dispatch = list(session.info.pop("_phase7_dispatch_pending", []))
            pending_retry = list(session.info.pop("_phase7_watchdog_retry_pending", []))
            if (
                promoted
                or completed
                or failed
                or dispatched
                or cleared
                or retried
                or watchdog_failed
            ):
                _log.info(
                    "scheduler.tick",
                    promoted=promoted,
                    completed=completed,
                    failed=failed,
                    script_dispatched=dispatched,
                    watchdog_cleared=cleared,
                    watchdog_retried=retried,
                    watchdog_failed=watchdog_failed,
                    at=now.isoformat(),
                )
        return pending_dispatch, pending_retry


async def reservation_tick() -> None:
    """One iteration of the scheduler. Public so tests can call it directly.

    Phase 6 FU-25 — every tick runs at SERIALIZABLE so the scheduler
    cannot race a concurrent ``create_reservation_batch``. asyncmy +
    SQLAlchemy 2 default to REPEATABLE READ otherwise.

    ``SET TRANSACTION ISOLATION LEVEL`` only applies to the *next*
    transaction, but ``session.execute`` autobegins one on first use,
    so the literal SQL must run as a ``SET SESSION TRANSACTION
    ISOLATION LEVEL SERIALIZABLE`` (sticky for the connection) issued
    + committed before ``session.begin()`` opens the real working
    transaction. The grep-able literal ``SET TRANSACTION ISOLATION
    LEVEL SERIALIZABLE`` stays in place (with the leading ``SESSION``
    keyword) per brief P6-3.

    Phase 7 C0 — after the tick commits, fire the dispatch RPCs that
    ``_dispatch_due_scripts`` and ``_retry_stuck_dispatches`` queued
    on ``session.info``. Those run as fire-and-forget tasks so a slow
    or offline agent never blocks the tick loop.

    Phase 8 C7 (P8-16 / FU-31) — wraps :func:`_tick_body` in the
    serializable-retry middleware so concurrent transactions hitting
    the InnoDB deadlock detector (error 1213) get up to 5 retries
    with exponential backoff before the tick gives up.
    """
    pending_dispatch, pending_retry = await with_serializable_retry(_tick_body)
    # Fire the RPCs after the SERIALIZABLE block has closed so a slow
    # agent never blocks the tick. asyncio.create_task is the right
    # primitive — _dispatch_one writes its own audit on failure.
    for rid, sid, payload in pending_dispatch + pending_retry:
        asyncio.create_task(  # noqa: RUF006 — fire-and-forget post-tick dispatch
            _dispatch_one(reservation_id=rid, server_id=sid, payload=payload)
        )


def build_scheduler(*, tick_seconds: int) -> AsyncIOScheduler:
    """Build (but do not start) the AsyncIOScheduler used by the backend.

    Kept separate from ``start_scheduler`` so tests can build, call
    ``reservation_tick`` directly, and never enter APScheduler's
    own job loop.
    """
    scheduler = AsyncIOScheduler(event_loop=asyncio.get_event_loop())
    scheduler.add_job(
        reservation_tick,
        trigger="interval",
        seconds=tick_seconds,
        id=_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler


def start_scheduler(*, tick_seconds: int) -> AsyncIOScheduler:
    """Build + start. Returned instance is held by the lifespan context
    so it can be shut down cleanly."""
    scheduler = build_scheduler(tick_seconds=tick_seconds)
    scheduler.start()
    _log.info("scheduler.started", tick_seconds=tick_seconds, job_id=_JOB_ID)
    return scheduler


async def shutdown_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Stop the scheduler. Called from the FastAPI lifespan shutdown branch."""
    scheduler.shutdown(wait=False)
    _log.info("scheduler.stopped", job_id=_JOB_ID)
