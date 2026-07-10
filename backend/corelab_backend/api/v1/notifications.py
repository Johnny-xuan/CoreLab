"""``/api/v1/notifications/*`` — bell + dropdown REST surface (Phase 7 follow-up).

Three endpoints, all scoped to the calling user:
* ``GET /notifications?since=<iso>&limit=20`` — paginated bell list,
  optional ``since`` for the REST catch-up after WS reconnect.
* ``POST /notifications/{id}/mark-read`` — flip a single row's
  ``is_read=1`` + stamp ``read_at``. 404 if the row does not belong
  to the caller; the only_recipient check is enforced server-side
  so a leaked id cannot toggle another user's bell.
* ``POST /notifications/mark-all-read`` — bulk flip for "mark all
  read" UI; returns the count flipped.

The notifications themselves are written by
:mod:`notification_service.create_notification` (Phase 7 C3). This
module is the read surface that backs C6 frontend.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import AuthenticatedUser, get_current_user
from ...db import get_session
from ...models import Notification

router = APIRouter(prefix="/notifications", tags=["notifications"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def _serialise(row: Notification) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "type": row.type,
        "severity": row.severity,
        "title": row.title,
        "body": row.body,
        "payload": row.payload,
        "cta_url": row.cta_url,
        "is_read": bool(row.is_read),
        "read_at": row.read_at.isoformat() if row.read_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("")
async def list_notifications(
    since: str | None = Query(None, description="ISO 8601 lower bound on created_at"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Newest-first list of the caller's notifications.

    ``since=<iso8601>`` filters to rows strictly after that timestamp —
    used by the frontend after a WS reconnect to catch up on anything
    the browser missed. Without it, returns the most recent ``limit``
    rows.
    """
    stmt = (
        select(Notification)
        .where(Notification.recipient_user_id == current.user.id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    if since is not None:
        # Query strings URL-decode ``+`` to space, so a literal
        # ``2026-06-05T03:10+00:00`` arrives here as
        # ``2026-06-05T03:10 00:00``. Accept either the ``Z`` suffix
        # (frontend convention) or restore the space → ``+``.
        normalised = since.replace("Z", "+00:00")
        if " " in normalised and "T" in normalised:
            normalised = normalised.replace(" ", "+", 1)
        try:
            since_dt = datetime.fromisoformat(normalised)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "INVALID_SINCE", "expected": "ISO 8601 timestamp"},
            ) from exc
        if since_dt.tzinfo is not None:
            since_dt = since_dt.astimezone(UTC).replace(tzinfo=None)
        stmt = stmt.where(Notification.created_at > since_dt)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    unread_q = await session.execute(
        select(func.count(Notification.id)).where(
            Notification.recipient_user_id == current.user.id,
            Notification.is_read == 0,
        )
    )
    return {
        "items": [_serialise(r) for r in rows],
        "unread_count": int(unread_q.scalar_one() or 0),
    }


@router.post("/{notification_id}/mark-read")
async def mark_one_read(
    notification_id: int = Path(..., ge=1),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Flip ``is_read`` on a single row owned by the caller."""
    row = await session.get(Notification, notification_id)
    # The only_recipient gate runs before the 404 check on purpose so
    # an attacker probing for ids cannot distinguish "exists for
    # someone else" from "does not exist".
    if row is None or row.recipient_user_id != current.user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOTIFICATION_NOT_FOUND"},
        )
    if not row.is_read:
        row.is_read = 1
        row.read_at = datetime.now(UTC)
        await session.flush()
        await session.commit()
    return {"notification": _serialise(row)}


@router.post("/mark-all-read")
async def mark_all_read(
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Bulk-flip the caller's unread rows. Returns count of rows touched."""
    now = datetime.now(UTC)
    result = await session.execute(
        update(Notification)
        .where(
            Notification.recipient_user_id == current.user.id,
            Notification.is_read == 0,
        )
        .values(is_read=1, read_at=now)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    # CursorResult.rowcount is the right attribute on the bulk UPDATE path;
    # mypy sees the abstract Result so cast through ``getattr``.
    updated = int(getattr(result, "rowcount", 0) or 0)
    return {"updated": updated}
