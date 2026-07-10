"""Phase 7 C3 — notification writer + WS push wrapper.

Single public entry point :func:`create_notification` so callers (the
reservation transitions, the account-link-request approve handler,
later alerts in Phase 8) cannot drift on field set / dedup / push.

The function is **persist-then-push**:
1. Run the dedup query (same recipient + type + payload-pk inside the
   60 s window).
2. Insert the row + flush.
3. Compose the ``/ws/user`` envelope per docs/05 §4.3 and call
   :func:`ws_user.push_to_user`. WS-side errors are caught + logged
   so a flaky socket never rolls back the DB row — at-least-once is
   the contract (browser re-fetches on reconnect via REST catch-up).

Dedup pk per planner ack 次级 3:
    pk_field = {
        "reservation.*": "reservation_id",
        "link.prepared":  "account_link_id",
        default:          no dedup
    }
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api import ws_user
from ..logging_setup import get_logger
from ..models import Notification

_log = get_logger("corelab.notification")

Severity = Literal["info", "warn", "error"]

DEFAULT_DEDUP_WINDOW_SECONDS: Final[int] = 60

# Notification ``type`` → JSON payload key that uniquely identifies
# the underlying entity for dedup purposes. Anything not listed here
# skips dedup (the row always inserts).
_DEDUP_PK_KEYS: Final[dict[str, str]] = {
    "reservation.started": "reservation_id",
    "reservation.completed": "reservation_id",
    "reservation.failed": "reservation_id",
    "reservation.cancelled_by_other": "reservation_id",
    # T89 — Phase H.1 script lifecycle events. Dedup by reservation_id
    # so a flaky WS that re-pushes ``agent.script.started`` doesn't
    # double-ring the user. Terminal-state events (completed/failed/
    # killed) are unique per reservation by construction so the 60s
    # window is just a defensive moat.
    "script.started": "reservation_id",
    "script.completed": "reservation_id",
    "script.failed": "reservation_id",
    "script.killed": "reservation_id",
    # link.prepared fires when admin approves an account_link_request +
    # agent pushes the SSH key; the AccountLink row itself is created
    # only after the user runs the SSH challenge, so we dedup by the
    # request id (always present at notification time) rather than the
    # link id (which doesn't exist yet).
    "link.prepared": "account_link_request_id",
}


async def _dedup_hit(
    session: AsyncSession,
    *,
    recipient_user_id: int,
    type_: str,
    payload_pk_value: int | str,
    window_seconds: int,
) -> Notification | None:
    """Find a row inserted in the last ``window_seconds`` whose payload
    has the expected pk value.

    The MySQL JSON dialect plus SQLAlchemy 2.0 returns ``JSON_EXTRACT``
    values wrapped in a JSON type; the most reliable cross-type compare
    is ``CAST(JSON_EXTRACT(payload, '$.<key>') AS CHAR)`` against the
    stringified expected value — works for both numeric and string pk
    fields, and naive equality on the JSON path expression can quietly
    miss matches when the JSON literal carries a quoted vs. unquoted
    representation.
    """
    from sqlalchemy import String, cast
    from sqlalchemy import func as sa_func

    cutoff = datetime.now(UTC) - timedelta(seconds=window_seconds)
    pk_key = _DEDUP_PK_KEYS[type_]
    extracted = sa_func.json_unquote(sa_func.json_extract(Notification.payload, f"$.{pk_key}"))
    result = await session.execute(
        select(Notification).where(
            Notification.recipient_user_id == recipient_user_id,
            Notification.type == type_,
            Notification.created_at >= cutoff,
            cast(extracted, String) == str(payload_pk_value),
        )
    )
    return result.scalars().first()


async def create_notification(
    session: AsyncSession,
    *,
    recipient_user_id: int,
    type: str,
    title: str,
    severity: Severity = "info",
    body: str | None = None,
    payload: dict[str, Any] | None = None,
    cta_url: str | None = None,
    dedup_window_seconds: int = DEFAULT_DEDUP_WINDOW_SECONDS,
) -> Notification:
    """Persist a notification + push the WS frame.

    Dedup is best-effort — if a recent row matches the triple
    ``(recipient_user_id, type, payload[pk_key])`` we return the
    existing row without inserting a new one **or** re-pushing the
    frame. Callers should not depend on dedup for correctness; it is
    a UX gate against spam.
    """
    pk_key = _DEDUP_PK_KEYS.get(type)
    if pk_key and payload and pk_key in payload:
        existing = await _dedup_hit(
            session,
            recipient_user_id=recipient_user_id,
            type_=type,
            payload_pk_value=payload[pk_key],
            window_seconds=dedup_window_seconds,
        )
        if existing is not None:
            _log.info(
                "notification.dedup_skip",
                recipient_user_id=recipient_user_id,
                type=type,
                pk_value=payload[pk_key],
                existing_id=existing.id,
            )
            return existing

    row = Notification(
        recipient_user_id=recipient_user_id,
        type=type,
        severity=severity,
        title=title,
        body=body,
        payload=payload,
        cta_url=cta_url,
    )
    session.add(row)
    await session.flush()

    # Compose the docs/05 §4.3 envelope and push. WS push failures are
    # logged but never raised — the row is already persisted, and the
    # browser will see it on the next REST catch-up.
    frame = {
        "type": "notification.new",
        "id": str(uuid4()),
        "ts": datetime.now(UTC).isoformat(),
        "payload": {
            "id": row.id,
            "type": row.type,
            "severity": row.severity,
            "title": row.title,
            "body": row.body,
            "payload": row.payload,
            "cta_url": row.cta_url,
            "is_read": bool(row.is_read),
            "created_at": (row.created_at or datetime.now(UTC)).isoformat(),
        },
    }
    try:
        await ws_user.push_to_user(recipient_user_id, frame)
    except Exception as exc:
        _log.warning(
            "notification.push_failed",
            recipient_user_id=recipient_user_id,
            type=type,
            error=str(exc),
        )
    return row
