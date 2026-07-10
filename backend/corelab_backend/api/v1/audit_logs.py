"""``/api/v1/audit-logs*`` — paginated audit-log read surface (Phase 9 C0).

Two endpoints, both backed by :mod:`corelab_backend.services.audit_service`:

* ``GET /audit-logs`` — 5-dim filter + pagination (docs/05 §3.17).
* ``GET /audit-logs/{id}`` — single-row detail (docs/05 §2.13).

Permission matrix (P9-2, docs/05 §2.13 字面):

* ``lab_admin`` — sees every row in the lab.
* ``server_admin`` — restricted to ``target_server_id`` ∈ owned servers.
* ``user`` — restricted to ``actor_user_id`` = self; passing another
  user's id returns 403 ``ONLY_SELF_ACTOR``.

``created_at_from`` / ``created_at_to`` reuse the Phase 7 lesson #7
``+``→space URL-decode fix (Phase 8 ``alerts.py`` set the pattern).

Phase 8 教训 #11 — no explicit ``async with session.begin()`` block;
``get_session()`` handles autobegin/commit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from math import ceil
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import AuthenticatedUser, get_current_user
from ...db import get_session
from ...models import ServerAdminGrant
from ...services import audit_service
from ...services.audit_service import AuditAccessContext, AuditFilters

router = APIRouter(prefix="/audit-logs", tags=["audit-logs"])

DEFAULT_PAGE = 1
DEFAULT_SIZE = 20
MAX_SIZE = 100


def _parse_iso8601(raw: str, *, field: str) -> datetime:
    """ISO8601 parser with Phase 7 lesson #7 URL ``+``→space fix."""
    normalised = raw.replace("Z", "+00:00")
    if " " in normalised and "T" in normalised:
        normalised = normalised.replace(" ", "+", 1)
    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_DATETIME", "field": field},
        ) from exc
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


async def _require_audit_access(
    target_server_id: int | None = Query(None, ge=1),
    actor_user_id: int | None = Query(None, ge=1),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AuditAccessContext:
    # ``server_admin`` is a per-server grant rather than a global role
    # (docs/02 §5.8 + Phase 8 ``policies.py`` precedent) — check the
    # grant table instead of ``current.role``.
    if current.role == "lab_admin":
        return AuditAccessContext(scope="all")
    owned = (
        (
            await session.execute(
                select(ServerAdminGrant.server_id).where(
                    ServerAdminGrant.user_id == current.user.id,
                    ServerAdminGrant.is_active == 1,
                )
            )
        )
        .scalars()
        .all()
    )
    owned_ids = [int(s) for s in owned]
    if owned_ids:
        if target_server_id is not None and target_server_id not in owned_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "NOT_YOUR_SERVER"},
            )
        return AuditAccessContext(scope="server_admin", server_ids=owned_ids)
    # Plain user — only their own actor rows.
    if actor_user_id is not None and actor_user_id != int(current.user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ONLY_SELF_ACTOR"},
        )
    return AuditAccessContext(scope="user", forced_actor_user_id=int(current.user.id))


@router.get("")
async def list_audit_logs(
    actor_user_id: int | None = Query(None, ge=1),
    action: str | None = Query(None, min_length=1, max_length=64),
    target_type: str | None = Query(None, min_length=1, max_length=32),
    target_server_id: int | None = Query(None, ge=1),
    created_at_from: str | None = Query(None, description="ISO 8601 lower bound"),
    created_at_to: str | None = Query(None, description="ISO 8601 upper bound"),
    page: int = Query(DEFAULT_PAGE, ge=1),
    size: int = Query(DEFAULT_SIZE, ge=1, le=MAX_SIZE),
    ctx: AuditAccessContext = Depends(_require_audit_access),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    filters = AuditFilters(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_server_id=target_server_id,
        created_at_from=_parse_iso8601(created_at_from, field="created_at_from")
        if created_at_from is not None
        else None,
        created_at_to=_parse_iso8601(created_at_to, field="created_at_to")
        if created_at_to is not None
        else None,
    )
    items, total = await audit_service.list_logs(
        session,
        lab_id=int(current.user.lab_id),
        ctx=ctx,
        filters=filters,
        page=page,
        size=size,
    )
    total_pages = ceil(total / size) if total else 0
    return {
        "items": items,
        "page": page,
        "size": size,
        "total": total,
        "total_pages": total_pages,
    }


@router.get("/{audit_id}")
async def get_audit_log(
    audit_id: int = Path(..., ge=1),
    ctx: AuditAccessContext = Depends(_require_audit_access),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await audit_service.get_log(
        session,
        audit_id=audit_id,
        lab_id=int(current.user.lab_id),
        ctx=ctx,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "AUDIT_NOT_FOUND"},
        )
    return {"audit": row}
