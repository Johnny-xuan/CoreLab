"""``/api/v1/admin/*`` — Phase L L-5 lab-level aggregations.

Two read endpoints powering the Lab Overview page. Both are lab_admin only.

* ``GET /admin/security-map`` — every active SSH key + its deployment
  sites (which servers it ended up on via account_link → authorized_key_entry).
  Plus server_admin_grant rollup. Lets the lab admin see, at a glance,
  who can ssh where.
* ``GET /admin/lab-usage-7d`` — per-user GPU·h ranking over the past 7
  days for everyone in the lab.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import AuthenticatedUser, require_role
from ...db import get_session
from ...models import (
    AccountLink,
    AuthorizedKeyEntry,
    PhysicalAccount,
    Reservation,
    Server,
    ServerAdminGrant,
    SshPublicKey,
    User,
)

router = APIRouter(prefix="/admin", tags=["admin-overview"])


# ============== Schemas ==============


class SecurityKeyEntry(BaseModel):
    ssh_key_id: int
    fingerprint_sha256: str
    key_type: str
    comment: str | None
    user_id: int
    username: str
    server_count: int
    server_hostnames: list[str]


class SecurityGrantEntry(BaseModel):
    user_id: int
    username: str
    server_count: int
    server_hostnames: list[str]


class SecurityMapResponse(BaseModel):
    total_active_keys: int
    total_active_grants: int
    keys: list[SecurityKeyEntry]
    grants: list[SecurityGrantEntry]


class LabUsageItem(BaseModel):
    user_id: int
    username: str
    hours: float


class LabUsageResponse(BaseModel):
    window_start: datetime
    window_end: datetime
    total_hours: float
    items: list[LabUsageItem]


# ============== Endpoints ==============


@router.get("/security-map", response_model=SecurityMapResponse)
async def lab_security_map(
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> SecurityMapResponse:
    """SSH keys + server_admin grants, joined with their deployment sites.

    The K-5 ``lateral_surface`` idea expanded to the whole lab: take every
    active SSH key, find every server it has been pushed to via an active
    account_link / authorized_key_entry, and roll up. Same for
    server_admin grants (per-user list of granted servers).
    """
    # Active keys + their deployment sites.
    key_rows = (
        await session.execute(
            select(
                SshPublicKey.id.label("ssh_key_id"),
                SshPublicKey.fingerprint_sha256,
                SshPublicKey.key_type,
                SshPublicKey.comment,
                SshPublicKey.user_id,
                User.username,
                Server.hostname,
            )
            .join(User, User.id == SshPublicKey.user_id)
            .outerjoin(
                AuthorizedKeyEntry,
                (AuthorizedKeyEntry.ssh_public_key_id == SshPublicKey.id)
                & (AuthorizedKeyEntry.is_active == 1),
            )
            .outerjoin(
                PhysicalAccount, PhysicalAccount.id == AuthorizedKeyEntry.physical_account_id
            )
            .outerjoin(Server, Server.id == PhysicalAccount.server_id)
            .where(
                SshPublicKey.is_active == 1,
                User.lab_id == current.lab_id,
            )
            .order_by(SshPublicKey.id.desc())
        )
    ).all()

    # Roll up by ssh_key_id.
    keys_map: dict[int, dict[str, Any]] = {}
    for r in key_rows:
        kid = int(r.ssh_key_id)
        entry = keys_map.setdefault(
            kid,
            {
                "ssh_key_id": kid,
                "fingerprint_sha256": r.fingerprint_sha256,
                "key_type": r.key_type,
                "comment": r.comment,
                "user_id": int(r.user_id),
                "username": r.username,
                "hostnames": set(),
            },
        )
        if r.hostname is not None:
            entry["hostnames"].add(r.hostname)
    keys = [
        SecurityKeyEntry(
            ssh_key_id=v["ssh_key_id"],
            fingerprint_sha256=v["fingerprint_sha256"],
            key_type=v["key_type"],
            comment=v["comment"],
            user_id=v["user_id"],
            username=v["username"],
            server_count=len(v["hostnames"]),
            server_hostnames=sorted(v["hostnames"]),
        )
        for v in keys_map.values()
    ]
    keys.sort(key=lambda k: (-k.server_count, k.user_id))
    total_active_keys = len(keys)

    # server_admin grants — group by user.
    grant_rows = (
        await session.execute(
            select(
                User.id.label("user_id"),
                User.username,
                Server.hostname,
            )
            .join(ServerAdminGrant, ServerAdminGrant.user_id == User.id)
            .join(Server, Server.id == ServerAdminGrant.server_id)
            .where(
                ServerAdminGrant.is_active == 1,
                Server.lab_id == current.lab_id,
                User.lab_id == current.lab_id,
            )
            .order_by(User.id, Server.hostname)
        )
    ).all()

    grant_map: dict[int, dict[str, Any]] = {}
    for grant_row in grant_rows:
        uid = int(grant_row.user_id)
        entry = grant_map.setdefault(
            uid,
            {"user_id": uid, "username": grant_row.username, "hostnames": []},
        )
        entry["hostnames"].append(grant_row.hostname)
    grants = [
        SecurityGrantEntry(
            user_id=v["user_id"],
            username=v["username"],
            server_count=len(v["hostnames"]),
            server_hostnames=v["hostnames"],
        )
        for v in grant_map.values()
    ]
    grants.sort(key=lambda g: (-g.server_count, g.user_id))
    total_active_grants = sum(g.server_count for g in grants)

    return SecurityMapResponse(
        total_active_keys=total_active_keys,
        total_active_grants=total_active_grants,
        keys=keys,
        grants=grants,
    )


@router.get("/lab-usage-7d", response_model=LabUsageResponse)
async def lab_usage_7d(
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> LabUsageResponse:
    """Per-user GPU·h ranking for the entire lab over the last 7 days."""
    now = datetime.now(UTC)
    window_start = now - timedelta(days=7)

    seconds_expr = text(
        "TIMESTAMPDIFF(SECOND, "
        "GREATEST(reservation.start_at, :ws), "
        "LEAST(reservation.end_at, :now, :we))"
    ).bindparams(
        ws=window_start.replace(tzinfo=None),
        now=now.replace(tzinfo=None),
        we=now.replace(tzinfo=None),
    )

    rows = (
        await session.execute(
            select(
                User.id.label("user_id"),
                User.username,
                (func.coalesce(func.sum(seconds_expr), 0) / 3600.0).label("hours"),
            )
            .join(Reservation, Reservation.user_id == User.id)
            .where(
                User.lab_id == current.lab_id,
                Reservation.status.in_(("active", "completed")),
                Reservation.start_at < now.replace(tzinfo=None),
                Reservation.end_at > window_start.replace(tzinfo=None),
            )
            .group_by(User.id, User.username)
            .order_by(text("hours DESC"))
        )
    ).all()

    items = [
        LabUsageItem(
            user_id=int(r.user_id),
            username=r.username,
            hours=round(float(r.hours or 0.0), 2),
        )
        for r in rows
    ]
    total = round(sum(i.hours for i in items), 2)

    return LabUsageResponse(
        window_start=window_start,
        window_end=now,
        total_hours=total,
        items=items,
    )


# ============== Phase M M-2.4 — onboarding status ==============


class OnboardingStatus(BaseModel):
    """Drives the OnboardingChecklist component on Dashboard / Lab Overview.

    Each count is the relevant table cardinality scoped to the caller's
    lab; ``all_done`` is true once every step the checklist tracks has
    at least one row (so the panel collapses).
    """

    servers_count: int
    online_servers_count: int
    users_count: int
    links_count: int
    reservations_count: int
    all_done: bool


@router.get("/onboarding-status", response_model=OnboardingStatus)
async def onboarding_status(
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> OnboardingStatus:
    """Cheap counts driving the first-run checklist.

    Five booleans the UI cares about; all derive from a row-count of the
    relevant table scoped to ``lab_id``. Online servers use the same
    ``status = 'online'`` heuristic the Lab Overview already shows.
    """
    lab_id = int(current.user.lab_id)

    servers_count = int(
        (
            await session.execute(
                select(func.count(Server.id)).where(Server.lab_id == lab_id, Server.is_active == 1)
            )
        ).scalar_one()
    )
    online_servers_count = int(
        (
            await session.execute(
                select(func.count(Server.id)).where(
                    Server.lab_id == lab_id,
                    Server.is_active == 1,
                    Server.status == "online",
                )
            )
        ).scalar_one()
    )
    users_count = int(
        (
            await session.execute(
                select(func.count(User.id)).where(User.lab_id == lab_id, User.is_active == 1)
            )
        ).scalar_one()
    )
    links_count = int(
        (
            await session.execute(
                select(func.count(AccountLink.id))
                .join(PhysicalAccount, PhysicalAccount.id == AccountLink.physical_account_id)
                .join(Server, Server.id == PhysicalAccount.server_id)
                .where(Server.lab_id == lab_id, AccountLink.is_active == 1)
            )
        ).scalar_one()
    )
    reservations_count = int(
        (
            await session.execute(
                select(func.count(Reservation.id))
                .join(Server, Server.id == Reservation.server_id)
                .where(Server.lab_id == lab_id)
            )
        ).scalar_one()
    )

    # all_done = every step has at least one row. The "Lab created" /
    # "admin created" steps are implicitly true once anyone can call
    # this endpoint, so they don't need a counter.
    all_done = (
        servers_count > 0
        and online_servers_count > 0
        and users_count > 1  # invited user beyond the bootstrap admin
        and links_count > 0
        and reservations_count > 0
    )

    return OnboardingStatus(
        servers_count=servers_count,
        online_servers_count=online_servers_count,
        users_count=users_count,
        links_count=links_count,
        reservations_count=reservations_count,
        all_done=all_done,
    )
