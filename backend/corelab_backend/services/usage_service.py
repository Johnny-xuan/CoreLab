"""Phase 7 C4 — per-user monthly GPU-hour aggregation (docs/05 §3.15).

Single public entry point :func:`monthly_usage` returning the 8-field
dict the API serialises. GPU usage is computed from ``reservation`` +
``server`` + ``physical_account``. Alert totals are computed from the
Phase 8 governance tables.

The month boundary is the **UTC half-open interval**:
``[YYYY-MM-01 00:00:00, next-month-01 00:00:00)``. The truncation
formula matches the planner ack for Catch #3:

    SUM(LEAST(end_at, NOW(), month_end) - GREATEST(start_at, month_start)) / 3600

— so a reservation that spans the month boundary contributes only the
portion that actually falls inside the window. Only ``active`` and
``completed`` rows count; ``cancelled`` / ``failed`` / ``scheduled``
are intentionally excluded.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..logging_setup import get_logger
from ..models import AccountLink, Gpu, Notification, PhysicalAccount, Reservation, Server

_log = get_logger("corelab.usage")

_COUNTED_STATUSES: Final[tuple[str, ...]] = ("active", "completed")
_TERMINAL_STATUSES: Final[tuple[str, ...]] = ("completed", "failed", "cancelled")


def month_boundaries(month: str) -> tuple[datetime, datetime]:
    """Parse ``YYYY-MM`` into ``(start_utc, end_utc)`` half-open interval."""
    year_str, month_str = month.split("-", 1)
    year = int(year_str)
    month_num = int(month_str)
    start = datetime(year, month_num, 1, tzinfo=UTC)
    if month_num == 12:
        end = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(year, month_num + 1, 1, tzinfo=UTC)
    return start, end


async def monthly_usage(
    session: AsyncSession,
    *,
    user_id: int,
    month: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compose the docs/05 §3.15 response for one user in one month."""
    month_start, month_end = month_boundaries(month)
    now_utc = now or datetime.now(UTC)

    # GPU-hours: double-truncated by start/end of month + capped at now.
    # SQLAlchemy does not expose ``TIMESTAMPDIFF(SECOND, ...)`` directly
    # because the unit is a keyword token, not a value. Use a raw text
    # fragment with bound params — the surrounding aggregate ``SUM`` and
    # ``COALESCE`` still go through the ORM expression layer.
    seconds_expr = text(
        "TIMESTAMPDIFF(SECOND, "
        "GREATEST(reservation.start_at, :month_start), "
        "LEAST(reservation.end_at, :now_utc, :month_end))"
    ).bindparams(
        month_start=month_start.replace(tzinfo=None),
        now_utc=now_utc.replace(tzinfo=None),
        month_end=month_end.replace(tzinfo=None),
    )

    gpu_hours_q = await session.execute(
        select(func.coalesce(func.sum(seconds_expr), 0) / 3600.0).where(
            Reservation.user_id == user_id,
            Reservation.status.in_(_COUNTED_STATUSES),
            Reservation.start_at < month_end,
            Reservation.end_at > month_start,
        )
    )
    gpu_hours_used = float(gpu_hours_q.scalar_one() or 0.0)

    # reservation_count: every reservation overlapping the month, regardless of status.
    count_q = await session.execute(
        select(func.count(Reservation.id)).where(
            Reservation.user_id == user_id,
            Reservation.start_at < month_end,
            Reservation.end_at > month_start,
        )
    )
    reservation_count = int(count_q.scalar_one() or 0)

    # completion_rate: completed / (completed + failed + cancelled). Only
    # terminal statuses go in the denominator — in-flight active / scheduled
    # rows do not influence the ratio.
    rates_q = await session.execute(
        select(
            func.coalesce(func.sum(case((Reservation.status == "completed", 1), else_=0)), 0).label(
                "completed"
            ),
            func.coalesce(func.sum(case((Reservation.status == "failed", 1), else_=0)), 0).label(
                "failed"
            ),
            func.coalesce(func.sum(case((Reservation.status == "cancelled", 1), else_=0)), 0).label(
                "cancelled"
            ),
        ).where(
            Reservation.user_id == user_id,
            Reservation.status.in_(_TERMINAL_STATUSES),
            Reservation.start_at < month_end,
            Reservation.end_at > month_start,
        )
    )
    rates = rates_q.one()
    total_terminal = int(rates.completed) + int(rates.failed) + int(rates.cancelled)
    completion_rate = float(rates.completed) / total_terminal if total_terminal > 0 else 0.0

    # by_server breakdown.
    by_server_q = await session.execute(
        select(
            Server.id.label("server_id"),
            Server.hostname,
            (func.coalesce(func.sum(seconds_expr), 0) / 3600.0).label("hours"),
        )
        .join(Reservation, Reservation.server_id == Server.id)
        .where(
            Reservation.user_id == user_id,
            Reservation.status.in_(_COUNTED_STATUSES),
            Reservation.start_at < month_end,
            Reservation.end_at > month_start,
        )
        .group_by(Server.id, Server.hostname)
    )
    by_server = [
        {"server_id": int(r.server_id), "hostname": r.hostname, "hours": float(r.hours or 0.0)}
        for r in by_server_q
    ]

    # by_pa breakdown — join via account_link -> physical_account.
    by_pa_q = await session.execute(
        select(
            PhysicalAccount.id.label("pa_id"),
            PhysicalAccount.linux_username,
            Server.hostname,
            (func.coalesce(func.sum(seconds_expr), 0) / 3600.0).label("hours"),
        )
        .join(AccountLink, AccountLink.physical_account_id == PhysicalAccount.id)
        .join(Reservation, Reservation.account_link_id == AccountLink.id)
        .join(Server, Reservation.server_id == Server.id)
        .where(
            Reservation.user_id == user_id,
            Reservation.status.in_(_COUNTED_STATUSES),
            Reservation.start_at < month_end,
            Reservation.end_at > month_start,
        )
        .group_by(PhysicalAccount.id, PhysicalAccount.linux_username, Server.hostname)
    )
    by_pa = [
        {
            "pa_id": int(r.pa_id),
            "linux_username": r.linux_username,
            "hostname": r.hostname,
            "hours": float(r.hours or 0.0),
        }
        for r in by_pa_q
    ]

    alerts_received, compliance_violations = await _governance_counts(
        session,
        user_id=user_id,
        month_start=month_start,
        month_end=month_end,
    )

    return {
        "month": month,
        "gpu_hours_used": round(gpu_hours_used, 2),
        "completion_rate": round(completion_rate, 4),
        "reservation_count": reservation_count,
        "by_server": by_server,
        "by_pa": by_pa,
        "alerts_received": alerts_received,
        "compliance_violations": compliance_violations,
    }


async def _governance_counts(
    session: AsyncSession,
    *,
    user_id: int,
    month_start: datetime,
    month_end: datetime,
) -> tuple[int, int]:
    """Return user-facing governance counters for the monthly usage page.

    ``notification`` is the canonical user-facing "who actually received
    this" surface. ``alert_event`` is lab/server-admin oriented and may not
    include every ordinary user who gets a compliance notification.
    """
    alerts_q = await session.execute(
        select(func.count(Notification.id)).where(
            Notification.recipient_user_id == user_id,
            Notification.created_at >= month_start,
            Notification.created_at < month_end,
            Notification.severity.in_(("warn", "error")),
        )
    )
    compliance_q = await session.execute(
        select(func.count(Notification.id)).where(
            Notification.recipient_user_id == user_id,
            Notification.created_at >= month_start,
            Notification.created_at < month_end,
            Notification.type.like("compliance.%"),
        )
    )
    return int(alerts_q.scalar_one() or 0), int(compliance_q.scalar_one() or 0)


def _windowed_seconds_expr(window_start: datetime, window_end: datetime, now_utc: datetime) -> Any:
    """Same truncation as monthly_usage but for an arbitrary half-open window."""
    return text(
        "TIMESTAMPDIFF(SECOND, "
        "GREATEST(reservation.start_at, :window_start), "
        "LEAST(reservation.end_at, :now_utc, :window_end))"
    ).bindparams(
        window_start=window_start.replace(tzinfo=None) if window_start.tzinfo else window_start,
        now_utc=now_utc.replace(tzinfo=None) if now_utc.tzinfo else now_utc,
        window_end=window_end.replace(tzinfo=None) if window_end.tzinfo else window_end,
    )


async def gpu_hours_for_user_in_window(
    session: AsyncSession,
    *,
    user_id: int,
    window_start: datetime,
    window_end: datetime,
    now: datetime | None = None,
) -> float:
    """Compute GPU·h for one user inside a half-open window, capped at ``now``.

    Phase L L-1 — reused by profile-summary for the 7d / 30d stat cards.
    """
    now_utc = now or datetime.now(UTC)
    seconds_expr = _windowed_seconds_expr(window_start, window_end, now_utc)
    q = await session.execute(
        select(func.coalesce(func.sum(seconds_expr), 0) / 3600.0).where(
            Reservation.user_id == user_id,
            Reservation.status.in_(_COUNTED_STATUSES),
            Reservation.start_at < window_end,
            Reservation.end_at > window_start,
        )
    )
    return float(q.scalar_one() or 0.0)


async def gpu_ranking_for_user_in_window(
    session: AsyncSession,
    *,
    user_id: int,
    window_start: datetime,
    window_end: datetime,
    now: datetime | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Top-N GPUs by hours used by this user in the window.

    Returns dicts {gpu_id, gpu_index, server_id, server_hostname, hours} in
    descending order. ``cron``-only reservations (gpu_id NULL) are skipped.
    """
    now_utc = now or datetime.now(UTC)
    seconds_expr = _windowed_seconds_expr(window_start, window_end, now_utc)
    q = await session.execute(
        select(
            Gpu.id.label("gpu_id"),
            Gpu.gpu_index,
            Server.id.label("server_id"),
            Server.hostname.label("server_hostname"),
            (func.coalesce(func.sum(seconds_expr), 0) / 3600.0).label("hours"),
        )
        .join(Reservation, Reservation.gpu_id == Gpu.id)
        .join(Server, Server.id == Gpu.server_id)
        .where(
            Reservation.user_id == user_id,
            Reservation.status.in_(_COUNTED_STATUSES),
            Reservation.start_at < window_end,
            Reservation.end_at > window_start,
        )
        .group_by(Gpu.id, Gpu.gpu_index, Server.id, Server.hostname)
        .order_by(text("hours DESC"))
        .limit(limit)
    )
    return [
        {
            "gpu_id": int(r.gpu_id),
            "gpu_index": int(r.gpu_index),
            "server_id": int(r.server_id),
            "server_hostname": r.server_hostname,
            "hours": round(float(r.hours or 0.0), 2),
        }
        for r in q
    ]


async def user_reservations_window(
    session: AsyncSession,
    *,
    user_id: int,
    window_start: datetime,
    window_end: datetime,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """All reservations for user that touch the window. Returns serialised rows.

    Phase L L-1 — driver for GET /users/:id/reservations. Status-agnostic
    (includes scheduled / cancelled / failed) so the UI can show both
    upcoming and history in one round-trip.
    """
    now_utc = now or datetime.now(UTC)
    seconds_expr = _windowed_seconds_expr(window_start, window_end, now_utc)
    q = await session.execute(
        select(
            Reservation,
            Gpu,
            Server,
            (func.coalesce(seconds_expr, 0) / 3600.0).label("hours"),
        )
        .join(Server, Server.id == Reservation.server_id)
        .outerjoin(Gpu, Gpu.id == Reservation.gpu_id)
        .where(
            Reservation.user_id == user_id,
            Reservation.start_at < window_end,
            Reservation.end_at > window_start,
        )
        .order_by(Reservation.start_at.desc())
    )
    out: list[dict[str, Any]] = []
    for res, gpu, srv, hours in q:
        out.append(
            {
                "id": int(res.id),
                "gpu_id": int(res.gpu_id) if res.gpu_id is not None else None,
                "gpu_index": int(gpu.gpu_index) if gpu is not None else None,
                "server_id": int(srv.id),
                "server_hostname": srv.hostname,
                "start_at": res.start_at,
                "end_at": res.end_at,
                "status": res.status,
                "hours": round(float(hours or 0.0), 2),
                "has_script": res.script is not None,
                "script_status": res.script_status,
            }
        )
    return out
