"""``/api/v1/gpus/*`` — Phase L L-3 GPU usage observation surface.

server_admin / lab_admin shared read surface. Three endpoints:

* ``GET /gpus/{id}/usage?range=`` — windowed total + by_user breakdown
  + "right now" pointer (driver for the three stat cards).
* ``GET /gpus/{id}/timeline?from=&to=`` — reservation list inside the
  window (driver for the 24h timeline).
* ``GET /gpus/{id}/recent-scripts?limit=`` — scripts that have run /
  are running on this GPU (driver for the scripts list).

Permission gate: every endpoint loads the GPU's server and runs the
existing ``assert_server_admin`` (lab_admin is implicit). 404 when the
GPU is not in the caller's lab.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import AuthenticatedUser, assert_server_admin, get_current_user
from ...db import get_session
from ...models import Gpu, Reservation, Server, User

router = APIRouter(prefix="/gpus", tags=["gpus"])


# ============== Schemas ==============


class GpuUsageByUser(BaseModel):
    user_id: int
    username: str
    hours: float


class GpuUsageNow(BaseModel):
    reservation_id: int
    user_id: int
    username: str
    started_at: datetime
    ends_at: datetime
    minutes_in: int
    is_cron: bool


class GpuUsageResponse(BaseModel):
    range: str
    window_start: datetime
    window_end: datetime
    total_hours: float
    busy_pct: float
    distinct_users: int
    by_user: list[GpuUsageByUser]
    now: GpuUsageNow | None


class GpuTimelineItem(BaseModel):
    reservation_id: int
    user_id: int
    username: str
    start_at: datetime
    end_at: datetime
    status: str
    is_cron: bool
    has_script: bool


class GpuTimelineResponse(BaseModel):
    range_from: datetime
    range_to: datetime
    items: list[GpuTimelineItem]


class GpuScriptItem(BaseModel):
    reservation_id: int
    user_id: int
    username: str
    script_first_line: str | None
    start_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    status: str | None
    exit_code: int | None


class GpuScriptsResponse(BaseModel):
    items: list[GpuScriptItem]


# ============== Helpers ==============


async def _load_gpu_or_404(session: AsyncSession, gpu_id: int, lab_id: int) -> tuple[Gpu, Server]:
    row = (
        await session.execute(
            select(Gpu, Server)
            .join(Server, Server.id == Gpu.server_id)
            .where(Gpu.id == gpu_id, Server.lab_id == lab_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="gpu_not_found")
    return row[0], row[1]


def _range_window(
    range_: Literal["today", "7d", "30d"], now: datetime
) -> tuple[datetime, datetime]:
    if range_ == "today":
        start = datetime(now.year, now.month, now.day, tzinfo=UTC)
        end = start + timedelta(days=1)
    elif range_ == "7d":
        start = now - timedelta(days=7)
        end = now
    else:
        start = now - timedelta(days=30)
        end = now
    return start, end


def _first_script_line(script: str | None) -> str | None:
    if not script:
        return None
    stripped = script.strip()
    if not stripped:
        return None
    first = stripped.splitlines()[0]
    return first[:120]


# ============== Endpoints ==============


@router.get("/{gpu_id}/usage", response_model=GpuUsageResponse)
async def gpu_usage(
    gpu_id: int = Path(..., ge=1),
    range_: Literal["today", "7d", "30d"] = Query("7d", alias="range"),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GpuUsageResponse:
    """Windowed total + by_user breakdown + 'right now' active pointer."""
    gpu, srv = await _load_gpu_or_404(session, gpu_id, current.lab_id)
    await assert_server_admin(session, user=current, server_id=int(srv.id))

    now = datetime.now(UTC)
    win_start, win_end = _range_window(range_, now)

    # Total hours for the GPU in the window.
    seconds_expr = text(
        "TIMESTAMPDIFF(SECOND, "
        "GREATEST(reservation.start_at, :ws), "
        "LEAST(reservation.end_at, :now, :we))"
    ).bindparams(
        ws=win_start.replace(tzinfo=None),
        now=now.replace(tzinfo=None),
        we=win_end.replace(tzinfo=None),
    )
    total_q = await session.execute(
        select(func.coalesce(func.sum(seconds_expr), 0) / 3600.0).where(
            Reservation.gpu_id == gpu_id,
            Reservation.status.in_(("active", "completed")),
            Reservation.start_at < win_end,
            Reservation.end_at > win_start,
        )
    )
    total_hours = float(total_q.scalar_one() or 0.0)

    window_hours = max((win_end - win_start).total_seconds() / 3600.0, 1.0)
    # busy_pct = total used hours / window hours (capped at 100).
    busy_pct = min(round(total_hours * 100.0 / window_hours, 1), 100.0)

    # By-user breakdown.
    by_user_q = await session.execute(
        select(
            User.id.label("user_id"),
            User.username,
            (func.coalesce(func.sum(seconds_expr), 0) / 3600.0).label("hours"),
        )
        .join(Reservation, Reservation.user_id == User.id)
        .where(
            Reservation.gpu_id == gpu_id,
            Reservation.status.in_(("active", "completed")),
            Reservation.start_at < win_end,
            Reservation.end_at > win_start,
        )
        .group_by(User.id, User.username)
        .order_by(text("hours DESC"))
    )
    by_user = [
        GpuUsageByUser(
            user_id=int(r.user_id),
            username=r.username,
            hours=round(float(r.hours or 0.0), 2),
        )
        for r in by_user_q
    ]
    distinct_users = len([u for u in by_user if u.hours > 0])

    # "Right now" — active reservation overlapping `now`.
    now_naive = now.replace(tzinfo=None)
    now_q = await session.execute(
        select(Reservation, User)
        .join(User, User.id == Reservation.user_id)
        .where(
            Reservation.gpu_id == gpu_id,
            Reservation.status == "active",
            Reservation.start_at <= now_naive,
            Reservation.end_at > now_naive,
        )
        .order_by(Reservation.start_at.asc())
        .limit(1)
    )
    now_row = now_q.first()
    now_obj: GpuUsageNow | None = None
    if now_row is not None:
        res, u = now_row
        started = res.start_at
        ends = res.end_at
        minutes_in = max(int((now_naive - started).total_seconds() // 60), 0)
        now_obj = GpuUsageNow(
            reservation_id=int(res.id),
            user_id=int(u.id),
            username=u.username,
            started_at=started,
            ends_at=ends,
            minutes_in=minutes_in,
            is_cron=res.gpu_id is None,
        )

    # `gpu` is loaded for the 404 + auth check; keep silent reference.
    _ = gpu

    return GpuUsageResponse(
        range=range_,
        window_start=win_start,
        window_end=win_end,
        total_hours=round(total_hours, 2),
        busy_pct=busy_pct,
        distinct_users=distinct_users,
        by_user=by_user,
        now=now_obj,
    )


@router.get("/{gpu_id}/timeline", response_model=GpuTimelineResponse)
async def gpu_timeline(
    gpu_id: int = Path(..., ge=1),
    hours_ahead: int = Query(24, ge=1, le=168),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GpuTimelineResponse:
    """Reservations occupying this GPU between now and now+hours_ahead."""
    gpu, srv = await _load_gpu_or_404(session, gpu_id, current.lab_id)
    await assert_server_admin(session, user=current, server_id=int(srv.id))

    now = datetime.now(UTC).replace(tzinfo=None)
    horizon = now + timedelta(hours=hours_ahead)

    rows = (
        await session.execute(
            select(Reservation, User)
            .join(User, User.id == Reservation.user_id)
            .where(
                Reservation.gpu_id == gpu_id,
                Reservation.status.in_(("scheduled", "active")),
                Reservation.start_at < horizon,
                Reservation.end_at > now,
            )
            .order_by(Reservation.start_at.asc())
        )
    ).all()

    items = [
        GpuTimelineItem(
            reservation_id=int(r.id),
            user_id=int(u.id),
            username=u.username,
            start_at=r.start_at,
            end_at=r.end_at,
            status=r.status,
            is_cron=r.gpu_id is None,
            has_script=r.script is not None,
        )
        for r, u in rows
    ]
    _ = gpu
    return GpuTimelineResponse(range_from=now, range_to=horizon, items=items)


@router.get("/{gpu_id}/recent-scripts", response_model=GpuScriptsResponse)
async def gpu_recent_scripts(
    gpu_id: int = Path(..., ge=1),
    limit: int = Query(20, ge=1, le=100),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GpuScriptsResponse:
    """Recent reservations on this GPU that carried a script payload."""
    gpu, srv = await _load_gpu_or_404(session, gpu_id, current.lab_id)
    await assert_server_admin(session, user=current, server_id=int(srv.id))

    rows = (
        await session.execute(
            select(Reservation, User)
            .join(User, User.id == Reservation.user_id)
            .where(Reservation.gpu_id == gpu_id, Reservation.script.is_not(None))
            .order_by(
                # MySQL has no NULLS LAST; `IS NULL` ascending puts non-null first.
                Reservation.script_started_at.is_(None),
                Reservation.script_started_at.desc(),
                Reservation.start_at.desc(),
            )
            .limit(limit)
        )
    ).all()

    items: list[GpuScriptItem] = []
    for res, u in rows:
        items.append(
            GpuScriptItem(
                reservation_id=int(res.id),
                user_id=int(u.id),
                username=u.username,
                script_first_line=_first_script_line(res.script),
                start_at=res.start_at,
                started_at=res.script_started_at,
                finished_at=res.script_finished_at,
                status=res.script_status,
                exit_code=res.script_exit_code,
            )
        )
    _ = gpu
    return GpuScriptsResponse(items=items)
