"""Phase 6 — backend handlers for agent.script.* push events + the SP-5
cancel orchestration.

Two responsibilities, kept in one module so the pending-kill registry
that links them stays trivially scoped:

1. **Lifecycle receive** — when the agent pushes ``agent.script.started``
   / ``agent.script.output_chunk`` / ``agent.script.finished``, sync
   the reservation row's ``script_status`` (and the related columns
   for ``finished``). Reservation.status is *not* changed here — the
   scheduler decides whether end_at -> completed or failed.

2. **SP-5 cancel orchestration** — when an API call asks to cancel an
   active reservation whose script is still running, send the agent
   ``backend.script.cancel`` RPC, wait for the matching
   ``agent.script.finished{killed_by_corelab=true}`` push, then
   transition the reservation through ``reservation_service``.

The registry tying those two together is ``_PENDING_KILLS``: a module
dict keyed by reservation_id, with ``asyncio.Event`` values. The
cancel path registers an event before sending the RPC; the lifecycle
``on_script_finished`` handler sets it when the matching kill event
arrives; the cancel path either consumes the event (success) or
times out (P6-14 "reservation stays active").

Failure surfacing follows planner Phase 6 SP-5 ack:
- ``AgentUnreachableDuringCancelError``: RPC offline / timeout.
- ``LifecycleEventTimeoutError``: RPC ack OK but the lifecycle event
  never arrived within ``PENDING_KILL_TIMEOUT_SECONDS``.
Both are audited as ``reservation.cancel.attempt_failed`` and leave
the reservation in ``status='active'`` — the scheduler will see it
again at end_at like any other active row.
"""

from __future__ import annotations

import asyncio
from typing import Any, Final

from corelab_protocol import (
    ScriptFinishedEvent,
    ScriptOutputChunkEvent,
    ScriptStartedEvent,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..logging_setup import get_logger
from ..models import AccountLink, Reservation
from . import agent_rpc, audit_service, notification_service, reservation_service

_log = get_logger("corelab.script_lifecycle")


# Reservation IDs awaiting a kill confirmation. The Event is set by
# ``on_script_finished`` when ``killed_by_corelab=true``; the cancel
# orchestrator waits on it before transitioning.
_PENDING_KILLS: Final[dict[int, asyncio.Event]] = {}

PENDING_KILL_TIMEOUT_SECONDS: Final[float] = 10.0
CANCEL_RPC_TIMEOUT_SECONDS: Final[float] = 5.0


class CancelOrchestrationError(Exception):
    """Base for the SP-5 cancel orchestration failures."""


class AgentUnreachableDuringCancelError(CancelOrchestrationError):
    """RPC send / ack failed (agent offline, RPC timeout, etc.). P6-14
    says the reservation must remain active and the scheduler will
    re-check it at end_at."""


class LifecycleEventTimeoutError(CancelOrchestrationError):
    """RPC ack returned cancelled=true but the agent.script.finished
    push never arrived within ``PENDING_KILL_TIMEOUT_SECONDS``."""


# ─── user notifications ────────────────────────────────────────────────


# T89 — Phase H.1. Maps the script terminal state we derive from
# ``agent.script.finished`` to the notification type + severity + title
# the My Reservations / Scripts page surfaces to the row owner.
_FINISHED_NOTIFICATION_META: Final[dict[str, tuple[str, notification_service.Severity, str]]] = {
    reservation_service.SCRIPT_COMPLETED: ("script.completed", "info", "Script completed"),
    reservation_service.SCRIPT_FAILED: ("script.failed", "error", "Script failed"),
    reservation_service.SCRIPT_KILLED: ("script.killed", "warn", "Script killed"),
}


async def _resolve_pa_id_for_reservation(
    session: AsyncSession, *, account_link_id: int
) -> int | None:
    """AccountLink → physical_account_id reverse-lookup for the deep-link
    target in the notification cta_url. Returns None if the link row is
    gone (cascade-deleted / link was rotated) — caller falls back to a
    non-anchored URL."""
    link = await session.get(AccountLink, account_link_id)
    return link.physical_account_id if link is not None else None


async def _notify_script_event(
    session: AsyncSession,
    *,
    row: Reservation,
    type_: str,
    title: str,
    severity: notification_service.Severity,
    body: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Persist + push a script lifecycle notification to the row owner.

    Best-effort — any failure inside notification_service is already
    logged + swallowed by ``create_notification`` itself; the catch here
    only guards against import / signature drift so a wonky notification
    can't roll back the lifecycle update that called us.

    ``cta_url`` deep-links into the Scripts page so the bell click takes
    the user straight to the relevant card. Falls back to /me/reservations
    if the PA lookup fails (link cascade-deleted etc.) so the click is
    still useful.
    """
    payload: dict[str, Any] = {
        "reservation_id": row.id,
        "server_id": row.server_id,
        "gpu_id": row.gpu_id,
    }
    if extra:
        payload.update(extra)
    pa_id = await _resolve_pa_id_for_reservation(session, account_link_id=row.account_link_id)
    cta_url = (
        f"/me/accounts/{pa_id}/scripts#res-{row.id}" if pa_id is not None else "/me/reservations"
    )
    try:
        await notification_service.create_notification(
            session,
            recipient_user_id=row.user_id,
            type=type_,
            title=title,
            severity=severity,
            body=body,
            payload=payload,
            cta_url=cta_url,
        )
    except Exception as exc:
        _log.warning(
            "script.notification_failed",
            reservation_id=row.id,
            notification_type=type_,
            error=str(exc),
        )


# ─── pending-kill registry ────────────────────────────────────────────


def _register_pending_kill(reservation_id: int) -> asyncio.Event:
    event = asyncio.Event()
    _PENDING_KILLS[reservation_id] = event
    return event


def _discard_pending_kill(reservation_id: int) -> None:
    _PENDING_KILLS.pop(reservation_id, None)


def _signal_lifecycle_kill(reservation_id: int) -> None:
    event = _PENDING_KILLS.get(reservation_id)
    if event is not None:
        event.set()


# ─── lifecycle event handlers (agent_ws receive loop)──────────────────


async def on_script_started(
    session: AsyncSession,
    *,
    payload: ScriptStartedEvent,
    lab_id: int,
) -> None:
    """Handle ``agent.script.started`` from the WSS receive loop.

    Phase 7 C0 (B 方案) — there are now two valid arrival paths:

    1. **Scheduler dispatch** (production) — the scheduler already set
       ``script_status='running'`` and ``script_dispatch_started_at``
       inside the tick before sending the RPC. The agent's ack lands
       here with the row already at ``running``; we clear the dispatch
       column (so the watchdog stops watching) and write a
       ``reservation.script.lifecycle.started`` audit so the watchdog's
       self-heal grep has a single grep-able signal.

    2. **Legacy / test** — the row arrived with ``script_status=None``
       (no scheduler dispatch ran, e.g. a unit test or an out-of-band
       agent push). Walk it through ``update_script_status`` so the
       Phase 6 transition rules apply, then also write the
       ``lifecycle.started`` marker for uniformity.

    Anything outside ``None`` / ``running`` is stale (the lifecycle is
    catching up after a cancel / completion) and is logged + dropped.
    """
    row = await session.get(Reservation, payload.reservation_id)
    if row is None:
        _log.warning("script.started.unknown_reservation", reservation_id=payload.reservation_id)
        return

    if row.script_status == reservation_service.SCRIPT_RUNNING:
        # B 方案 — scheduler already flipped the status; just close the
        # dispatch loop and audit the agent's arrival.
        row.script_dispatch_started_at = None
        if row.script_started_at is None:
            row.script_started_at = payload.started_at
        if payload.log_path:
            row.script_log_path = payload.log_path
        await session.flush()
        await audit_service.write(
            session,
            action="reservation.script.lifecycle.started",
            actor_user_id=None,
            lab_id=lab_id,
            target_type="reservation",
            target_id=row.id,
            target_lab_id=lab_id,
            target_server_id=row.server_id,
            payload={
                "pid": payload.pid,
                "log_path": payload.log_path,
                "started_at": payload.started_at.isoformat(),
                "trigger": "agent.script.started",
            },
        )
        await _notify_script_event(
            session,
            row=row,
            type_="script.started",
            title="Script started",
            severity="info",
            body=f"#{row.id} on server #{row.server_id} GPU #{row.gpu_id}",
            extra={
                "pid": payload.pid,
                "log_path": payload.log_path,
                "started_at": payload.started_at.isoformat(),
            },
        )
        return

    if row.script_status is None:
        # Legacy / test path — agent ack arrives without scheduler dispatch.
        await reservation_service.update_script_status(
            session,
            reservation=row,
            new_script_status=reservation_service.SCRIPT_RUNNING,
            lab_id=lab_id,
            trigger="agent.script.started",
            started_at=payload.started_at,
        )
        if payload.log_path:
            row.script_log_path = payload.log_path
            await session.flush()
        # Mirror the B 方案 marker so the watchdog sees the same signal
        # regardless of which path set the row to running.
        await audit_service.write(
            session,
            action="reservation.script.lifecycle.started",
            actor_user_id=None,
            lab_id=lab_id,
            target_type="reservation",
            target_id=row.id,
            target_lab_id=lab_id,
            target_server_id=row.server_id,
            payload={
                "pid": payload.pid,
                "log_path": payload.log_path,
                "started_at": payload.started_at.isoformat(),
                "trigger": "agent.script.started",
            },
        )
        await _notify_script_event(
            session,
            row=row,
            type_="script.started",
            title="Script started",
            severity="info",
            body=f"#{row.id} on server #{row.server_id} GPU #{row.gpu_id}",
            extra={
                "pid": payload.pid,
                "log_path": payload.log_path,
                "started_at": payload.started_at.isoformat(),
            },
        )
        return

    _log.info(
        "script.started.stale",
        reservation_id=payload.reservation_id,
        current_script_status=row.script_status,
    )


async def on_script_output_chunk(
    session: AsyncSession,
    *,
    payload: ScriptOutputChunkEvent,
    lab_id: int,
) -> None:
    """Persist a bounded recent-output tail for platform log viewing."""
    del lab_id
    row = await session.get(Reservation, payload.reservation_id)
    if row is None:
        _log.warning(
            "script.output_chunk.unknown_reservation", reservation_id=payload.reservation_id
        )
        return
    await reservation_service.append_script_log_tail(session, reservation=row, text=payload.text)


async def on_script_finished(
    session: AsyncSession,
    *,
    payload: ScriptFinishedEvent,
    lab_id: int,
) -> None:
    row = await session.get(Reservation, payload.reservation_id)
    if row is None:
        _log.warning(
            "script.finished.unknown_reservation",
            reservation_id=payload.reservation_id,
        )
        return

    if payload.killed_by_corelab:
        new_status = reservation_service.SCRIPT_KILLED
    elif payload.exit_code == 0:
        new_status = reservation_service.SCRIPT_COMPLETED
    else:
        new_status = reservation_service.SCRIPT_FAILED

    # script_status update is idempotent + transitions are validated by
    # reservation_service.update_script_status; if the row has been
    # cancelled between the RPC and the lifecycle push, the script_status
    # update still runs (running -> killed/completed/failed are all
    # legal terminals).
    if row.script_status == reservation_service.SCRIPT_RUNNING:
        await reservation_service.update_script_status(
            session,
            reservation=row,
            new_script_status=new_status,
            lab_id=lab_id,
            trigger="agent.script.finished",
            exit_code=payload.exit_code,
            finished_at=payload.finished_at,
        )
        if payload.output_size_bytes:
            row.script_output_size_bytes = payload.output_size_bytes
        if payload.log_path:
            row.script_log_path = payload.log_path
        await session.flush()

        # Surface the terminal state to the row owner. Notification is
        # idempotent (notification_service dedups by reservation_id within
        # the 60s window).
        meta = _FINISHED_NOTIFICATION_META.get(new_status)
        if meta is not None:
            type_, severity, title = meta
            duration_text = f"{payload.duration_seconds:.0f}s" if payload.duration_seconds else "—"
            if new_status == reservation_service.SCRIPT_COMPLETED:
                body = f"#{row.id} exit 0 in {duration_text}"
            elif new_status == reservation_service.SCRIPT_FAILED:
                body = f"#{row.id} exit {payload.exit_code} after {duration_text}"
            else:  # killed
                reason = payload.killed_reason or "platform"
                body = f"#{row.id} killed ({reason}) after {duration_text}"
            await _notify_script_event(
                session,
                row=row,
                type_=type_,
                title=title,
                severity=severity,
                body=body,
                extra={
                    "exit_code": payload.exit_code,
                    "duration_seconds": payload.duration_seconds,
                    "log_path": payload.log_path,
                    "output_size_bytes": payload.output_size_bytes,
                    "killed_by_corelab": payload.killed_by_corelab,
                    "killed_reason": payload.killed_reason,
                },
            )
    else:
        _log.info(
            "script.finished.stale",
            reservation_id=payload.reservation_id,
            current_script_status=row.script_status,
            push_new_status=new_status,
        )

    # Wake any cancel orchestrator waiting on this kill confirmation —
    # only the killed_by_corelab branch counts as a cancel signal so
    # natural completed / failed paths do not falsely unblock a cancel.
    if payload.killed_by_corelab:
        _signal_lifecycle_kill(payload.reservation_id)


# ─── SP-5 cancel orchestration ────────────────────────────────────────


async def cancel_active_with_running_script(
    session: AsyncSession,
    *,
    reservation: Reservation,
    actor_user_id: int,
    actor_can_admin: bool,
    reason: str | None,
    lab_id: int,
    cancel_reason_for_agent: str,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> Reservation:
    """P6-14 — RPC + wait-for-lifecycle, then transition.

    Caller already validated ``reservation.status == 'active'`` and
    ``reservation.script_status == 'running'``. Returns the transitioned
    row on success; raises ``AgentUnreachableDuringCancelError`` or
    ``LifecycleEventTimeoutError`` to keep the row active per P6-14.
    """
    reservation_id = reservation.id
    server_id = reservation.server_id
    event = _register_pending_kill(reservation_id)
    try:
        try:
            await agent_rpc.request_response(
                server_id=server_id,
                frame_type="backend.script.cancel",
                payload={
                    "reservation_id": reservation_id,
                    "reason": cancel_reason_for_agent,
                },
                timeout_seconds=CANCEL_RPC_TIMEOUT_SECONDS,
            )
        except (agent_rpc.AgentOfflineError, agent_rpc.AgentRpcTimeoutError) as exc:
            await audit_service.write(
                session,
                action="reservation.cancel.attempt_failed",
                actor_user_id=actor_user_id,
                lab_id=lab_id,
                target_type="reservation",
                target_id=reservation_id,
                target_lab_id=lab_id,
                target_server_id=server_id,
                payload={
                    "reason": "agent_unreachable_during_cancel,kept active",
                    "error_class": type(exc).__name__,
                },
                ip_address=request_ip,
                user_agent=user_agent,
            )
            raise AgentUnreachableDuringCancelError(str(exc)) from exc

        try:
            await asyncio.wait_for(event.wait(), timeout=PENDING_KILL_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            await audit_service.write(
                session,
                action="reservation.cancel.attempt_failed",
                actor_user_id=actor_user_id,
                lab_id=lab_id,
                target_type="reservation",
                target_id=reservation_id,
                target_lab_id=lab_id,
                target_server_id=server_id,
                payload={
                    "reason": "lifecycle_event_timeout,kept active",
                    "waited_seconds": PENDING_KILL_TIMEOUT_SECONDS,
                },
                ip_address=request_ip,
                user_agent=user_agent,
            )
            raise LifecycleEventTimeoutError(
                f"agent did not push script.finished within "
                f"{PENDING_KILL_TIMEOUT_SECONDS}s for reservation {reservation_id}"
            ) from exc

        # Lifecycle confirmed kill — script_status already moved to
        # 'killed' by on_script_finished above. Now flip reservation.
        return await reservation_service.cancel_reservation(
            session,
            reservation_id=reservation_id,
            actor_user_id=actor_user_id,
            actor_can_admin=actor_can_admin,
            reason=reason,
            lab_id=lab_id,
            request_ip=request_ip,
            user_agent=user_agent,
        )
    finally:
        _discard_pending_kill(reservation_id)
