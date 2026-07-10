"""Append-only audit log writer + lab-scoped read surface (Phase 9 C0).

``write`` is the only mutating entry point — ``audit_log`` rows are
immutable by service-level convention and by DB triggers that reject
runtime ``corelab_app`` UPDATE/DELETE attempts (Phase 2 invariant #8).
``list_logs`` / ``get_log`` are read-only helpers used by the Phase 9
admin REST surface (docs/05 §3.17).

Write failures fall back to a structured warning so an audit-log outage
never blocks a user action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logging_setup import get_logger
from ..models import AuditLog, Server, User

_log = get_logger("corelab.audit")


AuditScope = Literal["all", "server_admin", "user"]


@dataclass(slots=True)
class AuditAccessContext:
    """Resolved permission context for one ``/audit-logs`` call.

    * ``scope='all'`` — lab_admin, no extra filter needed (still scoped
      to caller's lab via join).
    * ``scope='server_admin'`` — only rows where ``target_server_id``
      is in ``server_ids``.
    * ``scope='user'`` — only rows where ``actor_user_id`` is the caller.
    """

    scope: AuditScope
    server_ids: list[int] = field(default_factory=list)
    forced_actor_user_id: int | None = None


@dataclass(slots=True)
class AuditFilters:
    actor_user_id: int | None = None
    action: str | None = None
    target_type: str | None = None
    target_server_id: int | None = None
    created_at_from: datetime | None = None
    created_at_to: datetime | None = None


async def write(
    session: AsyncSession,
    *,
    action: str,
    actor_user_id: int | None,
    lab_id: int | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    target_lab_id: int | None = None,
    target_server_id: int | None = None,
    payload: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    result: str = "ok",
    error_message: str | None = None,
) -> None:
    entry = AuditLog(
        action=action,
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type=target_type,
        target_id=target_id,
        target_lab_id=target_lab_id,
        target_server_id=target_server_id,
        payload=payload,
        ip_address=ip_address,
        user_agent=user_agent,
        result=result,
        error_message=error_message,
    )
    try:
        session.add(entry)
        await session.flush()
    except Exception as exc:
        _log.warning(
            "audit.write_failed",
            action=action,
            actor_user_id=actor_user_id,
            error=str(exc),
        )


def _apply_scope(stmt: Any, ctx: AuditAccessContext, lab_id: int) -> Any:
    """Apply lab scoping + caller-specific narrowing to a select stmt."""
    # All callers — restrict to rows joined to the caller's lab. Audit
    # rows have ``lab_id`` populated when the actor was in a lab (always
    # true for normal usage); system-triggered rows (actor=None) are
    # surfaced via ``target_server_id → server.lab_id`` join below.
    stmt = stmt.where(
        (AuditLog.lab_id == lab_id)
        | (AuditLog.target_server_id.in_(select(Server.id).where(Server.lab_id == lab_id)))
    )
    if ctx.scope == "server_admin":
        if not ctx.server_ids:
            # No owned servers → empty result.
            stmt = stmt.where(AuditLog.id == -1)
        else:
            stmt = stmt.where(AuditLog.target_server_id.in_(ctx.server_ids))
    elif ctx.scope == "user":
        assert ctx.forced_actor_user_id is not None
        stmt = stmt.where(AuditLog.actor_user_id == ctx.forced_actor_user_id)
    return stmt


def _apply_filters(stmt: Any, f: AuditFilters) -> Any:
    if f.actor_user_id is not None:
        stmt = stmt.where(AuditLog.actor_user_id == f.actor_user_id)
    if f.action is not None:
        stmt = stmt.where(AuditLog.action == f.action)
    if f.target_type is not None:
        stmt = stmt.where(AuditLog.target_type == f.target_type)
    if f.target_server_id is not None:
        stmt = stmt.where(AuditLog.target_server_id == f.target_server_id)
    if f.created_at_from is not None:
        stmt = stmt.where(AuditLog.created_at >= f.created_at_from)
    if f.created_at_to is not None:
        stmt = stmt.where(AuditLog.created_at <= f.created_at_to)
    return stmt


async def list_logs(
    session: AsyncSession,
    *,
    lab_id: int,
    ctx: AuditAccessContext,
    filters: AuditFilters,
    page: int,
    size: int,
) -> tuple[list[dict[str, Any]], int]:
    """Return (items, total) for the paginated audit-log read.

    ``items`` are pre-serialised dicts with the ``actor`` object
    embedded (docs/05 §3.17 response shape).
    """
    base = _apply_scope(select(AuditLog), ctx, lab_id)
    base = _apply_filters(base, filters)

    count_stmt = select(func.count()).select_from(base.subquery())
    total = int((await session.execute(count_stmt)).scalar_one())

    offset = (page - 1) * size
    stmt = base.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).offset(offset).limit(size)
    rows = (await session.execute(stmt)).scalars().all()

    actor_ids = {int(r.actor_user_id) for r in rows if r.actor_user_id is not None}
    actor_map: dict[int, User] = {}
    if actor_ids:
        actor_rows = (
            (await session.execute(select(User).where(User.id.in_(actor_ids)))).scalars().all()
        )
        actor_map = {int(u.id): u for u in actor_rows}

    return [_serialise(r, actor_map) for r in rows], total


async def get_log(
    session: AsyncSession,
    *,
    audit_id: int,
    lab_id: int,
    ctx: AuditAccessContext,
) -> dict[str, Any] | None:
    """Return one audit row honouring the same lab/scope filter."""
    stmt = _apply_scope(select(AuditLog).where(AuditLog.id == audit_id), ctx, lab_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    actor: User | None = None
    if row.actor_user_id is not None:
        actor = await session.get(User, int(row.actor_user_id))
    return _serialise(row, {int(actor.id): actor} if actor else {})


def _serialise(row: AuditLog, actor_map: dict[int, User]) -> dict[str, Any]:
    actor_obj: dict[str, Any] | None = None
    if row.actor_user_id is not None:
        u = actor_map.get(int(row.actor_user_id))
        actor_obj = {
            "id": int(row.actor_user_id),
            "username": u.username if u else None,
        }
    return {
        "id": int(row.id),
        "actor": actor_obj,
        "action": row.action,
        "target_type": row.target_type,
        "target_id": int(row.target_id) if row.target_id is not None else None,
        "target_server_id": (
            int(row.target_server_id) if row.target_server_id is not None else None
        ),
        "payload": row.payload,
        "ip_address": row.ip_address,
        "result": row.result,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
