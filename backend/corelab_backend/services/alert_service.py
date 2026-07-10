"""Phase 8 C1 — alert_event writer + WS push (P8-11).

Pattern: **persist-then-push at-least-once**.

Unlike :func:`notification_service.create_notification` which lets the
caller control the surrounding transaction, alerts own their own commit
boundary. The rationale (docs/04 §9.7.5):

* alerts are independent event-log rows — they must survive even if the
  compliance handler's surrounding transaction rolls back
* downstream WS push failures must not roll back the row — the bell
  and the REST ``/alert-events`` list are the user-facing surfaces and
  the row presence is what they read

Contract:

1. Determine recipients (server admins + holders of an active
   reservation on this server / gpu / reservation_id).
2. Run app-layer dedup — if ``(server_id, gpu_id, event_type)`` already
   has a row in the last hour, return that row + skip push.
3. ``session.add`` + ``flush`` + ``commit`` — row is now durable.
4. Fan out ``alert.new`` frames to each recipient via ``/ws/user``;
   each push wrapped in try/except — failures warn-log only, never
   raise (at-least-once).
5. If commit raises, propagate to caller and *do not* push (we never
   push a row that didn't persist).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api import ws_user
from ..logging_setup import get_logger
from ..models import AlertEvent, Reservation, ServerAdminGrant, User

_log = get_logger("corelab.alert")

Severity = Literal["info", "warn", "critical"]

DEFAULT_DEDUP_WINDOW_SECONDS: Final[int] = 3600  # docs/02 §5.16 — 1 hour


async def _dedup_hit(
    session: AsyncSession,
    *,
    server_id: int,
    gpu_id: int | None,
    event_type: str,
    window_seconds: int,
) -> AlertEvent | None:
    cutoff = datetime.now(UTC) - timedelta(seconds=window_seconds)
    stmt = select(AlertEvent).where(
        AlertEvent.server_id == server_id,
        AlertEvent.event_type == event_type,
        AlertEvent.created_at >= cutoff,
    )
    # gpu_id NULL has to match NULL — SQL ``= NULL`` is false, so split.
    if gpu_id is None:
        stmt = stmt.where(AlertEvent.gpu_id.is_(None))
    else:
        stmt = stmt.where(AlertEvent.gpu_id == gpu_id)
    result = await session.execute(stmt.order_by(AlertEvent.created_at.desc()).limit(1))
    return result.scalars().first()


async def _determine_recipients(
    session: AsyncSession,
    *,
    server_id: int,
    reservation_id: int | None,
) -> list[int]:
    """Server admins (per-server grant + lab_admin) + the reservation
    owner if known. Lab admins of the server's lab are also included.

    docs/02 §5.16 left the recipient rule open (worker noted in
    Phase 8 brief §6) — this implementation:
    * server admins: anyone with an active ``server_admin_grant`` for
      this server, plus lab_admin role users of the server's lab
    * reservation owner: if ``reservation_id`` is set, the row owner
    """
    recipients: set[int] = set()

    grants_q = await session.execute(
        select(ServerAdminGrant.user_id).where(
            ServerAdminGrant.server_id == server_id,
            ServerAdminGrant.is_active == 1,
        )
    )
    for uid in grants_q.scalars():
        recipients.add(int(uid))

    # lab_admin role users of the server's lab.
    from ..models import Server  # local import to avoid circular tighten.

    server = await session.get(Server, server_id)
    if server is not None:
        admins_q = await session.execute(
            select(User.id).where(
                User.lab_id == server.lab_id,
                User.role == "lab_admin",
                or_(User.is_active.is_(None), User.is_active == 1),
            )
        )
        for uid in admins_q.scalars():
            recipients.add(int(uid))

    if reservation_id is not None:
        res = await session.get(Reservation, reservation_id)
        if res is not None:
            recipients.add(int(res.user_id))

    return sorted(recipients)


async def create_alert(
    session: AsyncSession,
    *,
    server_id: int,
    event_type: str,
    severity: Severity,
    gpu_id: int | None = None,
    reservation_id: int | None = None,
    payload: dict[str, Any] | None = None,
    dedup_window_seconds: int = DEFAULT_DEDUP_WINDOW_SECONDS,
) -> AlertEvent:
    """Persist an alert + fan out ``alert.new`` WS frames (P8-11).

    Service owns the commit boundary — caller must not have an open
    transaction on this ``session``.
    """
    # 1) Dedup before any side-effect.
    existing = await _dedup_hit(
        session,
        server_id=server_id,
        gpu_id=gpu_id,
        event_type=event_type,
        window_seconds=dedup_window_seconds,
    )
    if existing is not None:
        _log.info(
            "alert.dedup_skip",
            server_id=server_id,
            gpu_id=gpu_id,
            event_type=event_type,
            existing_id=existing.id,
        )
        return existing

    # 2) Decide recipients before insert so the row carries them.
    recipients = await _determine_recipients(
        session, server_id=server_id, reservation_id=reservation_id
    )

    # 3) Insert + flush + COMMIT (at-least-once durability).
    row = AlertEvent(
        server_id=server_id,
        gpu_id=gpu_id,
        reservation_id=reservation_id,
        event_type=event_type,
        severity=severity,
        payload=payload,
        notified_user_ids=recipients,
        is_resolved=0,
    )
    session.add(row)
    await session.flush()
    await session.commit()

    # 4) Push after commit — failures warn-log, never raise.
    frame = {
        "type": "alert.new",
        "id": str(uuid4()),
        "ts": datetime.now(UTC).isoformat(),
        "payload": {
            "alert_event_id": row.id,
            "server_id": server_id,
            "gpu_id": gpu_id,
            "reservation_id": reservation_id,
            "event_type": event_type,
            "severity": severity,
            "payload": payload,
            "created_at": (row.created_at or datetime.now(UTC)).isoformat(),
        },
    }
    for user_id in recipients:
        try:
            await ws_user.push_to_user(user_id, frame)
        except Exception as exc:
            _log.warning(
                "alert.push_failed",
                user_id=user_id,
                alert_event_id=row.id,
                event_type=event_type,
                error=str(exc),
            )
    return row


async def resolve_alert(
    session: AsyncSession,
    *,
    alert_id: int,
    resolver_user_id: int,
    resolution_note: str | None = None,
) -> AlertEvent | None:
    row = await session.get(AlertEvent, alert_id)
    if row is None:
        return None
    if row.is_resolved == 1:
        return row
    row.is_resolved = 1
    row.resolved_at = datetime.now(UTC)
    row.resolved_by_user_id = resolver_user_id
    row.resolution_note = resolution_note
    await session.flush()
    return row
