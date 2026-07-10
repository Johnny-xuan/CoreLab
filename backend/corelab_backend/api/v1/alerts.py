"""``/api/v1/alert-events*`` — alert list + resolve REST surface (P8-11 / P8-14).

Two endpoints scoped to the calling user's lab:
* ``GET /alert-events?since=<iso>&server_id=&limit=20`` — newest-first
  list, optional since/server filters. Anyone in the lab can list
  (read-only).
* ``POST /alert-events/{id}/resolve`` — mark resolved; server_admin
  + lab_admin only.

The ``since`` query handler applies the Phase 7 lesson #7 fix:
URL-decoding turns ``+`` into space, so we accept both ``Z`` suffix
and the space→``+`` restoration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import AuthenticatedUser, get_current_user
from ...db import get_session
from ...models import AlertEvent, Server, ServerAdminGrant
from ...services import agent_rpc, alert_service, audit_service

router = APIRouter(prefix="/alert-events", tags=["alert-events"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 200


def _serialise(row: AlertEvent) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "server_id": int(row.server_id),
        "gpu_id": int(row.gpu_id) if row.gpu_id is not None else None,
        "reservation_id": int(row.reservation_id) if row.reservation_id is not None else None,
        "event_type": row.event_type,
        "severity": row.severity,
        "payload": row.payload,
        "notified_user_ids": row.notified_user_ids,
        "is_resolved": bool(row.is_resolved),
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "resolved_by_user_id": (
            int(row.resolved_by_user_id) if row.resolved_by_user_id is not None else None
        ),
        "resolution_note": row.resolution_note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _parse_since(since: str) -> datetime:
    # Phase 7 lesson #7 — URL decode flips '+' to ' '.
    normalised = since.replace("Z", "+00:00")
    if " " in normalised and "T" in normalised:
        normalised = normalised.replace(" ", "+", 1)
    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_SINCE", "expected": "ISO 8601 timestamp"},
        ) from exc
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


@router.get("")
async def list_alerts(
    since: str | None = Query(None, description="ISO 8601 lower bound"),
    server_id: int | None = Query(None, ge=1),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = (
        select(AlertEvent)
        .join(Server, AlertEvent.server_id == Server.id)
        .where(Server.lab_id == current.user.lab_id)
        .order_by(AlertEvent.created_at.desc())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(AlertEvent.created_at > _parse_since(since))
    if server_id is not None:
        stmt = stmt.where(AlertEvent.server_id == server_id)
    rows = (await session.execute(stmt)).scalars().all()
    return {"items": [_serialise(r) for r in rows]}


class ResolveRequest(BaseModel):
    resolution_note: str | None = Field(default=None, max_length=500)


async def _load_alert_with_admin_check(
    session: AsyncSession,
    *,
    alert_id: int,
    current: AuthenticatedUser,
    forbid_code: str,
) -> tuple[AlertEvent, Server]:
    """Load an alert + its server, enforcing lab_admin-or-server-admin.

    404 (not 403) when the alert/server is outside the caller's lab, so
    we don't leak existence across labs. 403 only once we've confirmed
    the row is in-lab but the caller lacks the server grant.
    """
    row = await session.get(AlertEvent, alert_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": "ALERT_NOT_FOUND"}
        )
    server = await session.get(Server, int(row.server_id))
    if server is None or server.lab_id != current.user.lab_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": "ALERT_NOT_FOUND"}
        )
    if current.role != "lab_admin":
        grant = (
            await session.execute(
                select(ServerAdminGrant).where(
                    ServerAdminGrant.server_id == int(row.server_id),
                    ServerAdminGrant.user_id == current.user.id,
                    ServerAdminGrant.is_active == 1,
                )
            )
        ).scalar_one_or_none()
        if grant is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": forbid_code})
    return row, server


@router.post("/{alert_id}/resolve")
async def resolve_alert(
    body: ResolveRequest,
    alert_id: int = Path(..., ge=1),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    # get_session() handles the transaction; no explicit begin block
    # needed.
    row, _server = await _load_alert_with_admin_check(
        session,
        alert_id=alert_id,
        current=current,
        forbid_code="ALERT_RESOLVE_NOT_PERMITTED",
    )

    resolved = await alert_service.resolve_alert(
        session,
        alert_id=alert_id,
        resolver_user_id=current.user.id,
        resolution_note=body.resolution_note,
    )
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ALERT_NOT_FOUND"},
        )
    await audit_service.write(
        session,
        action="alert.resolve",
        actor_user_id=current.user.id,
        lab_id=current.user.lab_id,
        target_type="alert_event",
        target_id=alert_id,
        target_server_id=int(row.server_id),
        payload={
            "event_type": row.event_type,
            "resolution_note": body.resolution_note,
        },
    )
    return {"alert": _serialise(resolved)}


class KillProcessRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.post("/{alert_id}/kill-process")
async def kill_process(
    body: KillProcessRequest,
    alert_id: int = Path(..., ge=1),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Admin manual kill — the human trigger behind a warn alert.

    Pulls the offending pid from the alert payload and asks the agent
    to terminate it (backend.gpu.kill_process RPC). The agent still
    gates on the gpu.kill_process capability, so a disabled switch
    turns this into a clean 'capability off' message rather than a
    silent no-op. Always writes an audit row — kill attempts are
    exactly what an operator wants on the record.
    """
    row, _server = await _load_alert_with_admin_check(
        session,
        alert_id=alert_id,
        current=current,
        forbid_code="ALERT_KILL_NOT_PERMITTED",
    )

    payload = row.payload if isinstance(row.payload, dict) else {}
    pid = payload.get("linux_pid")
    if not isinstance(pid, int):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ALERT_HAS_NO_PID",
                "message": "this alert carries no process id to kill",
            },
        )

    try:
        rpc_result = await agent_rpc.request_response(
            server_id=int(row.server_id),
            frame_type="backend.gpu.kill_process",
            payload={
                "pid": pid,
                "linux_username": payload.get("linux_username"),
                "reason": body.reason or f"manual kill via alert #{alert_id}",
            },
            timeout_seconds=15.0,
        )
        killed = bool(rpc_result.get("killed"))
        rpc_ok = bool(rpc_result.get("ok"))
        rpc_error = rpc_result.get("error")
    except agent_rpc.AgentOfflineError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "AGENT_OFFLINE", "message": str(exc)},
        ) from exc
    except agent_rpc.AgentRpcTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={"code": "AGENT_TIMEOUT", "message": str(exc)},
        ) from exc

    await audit_service.write(
        session,
        action="alert.kill_process",
        actor_user_id=current.user.id,
        lab_id=current.user.lab_id,
        target_type="alert_event",
        target_id=alert_id,
        target_server_id=int(row.server_id),
        payload={
            "event_type": row.event_type,
            "linux_pid": pid,
            "linux_username": payload.get("linux_username"),
            "killed": killed,
            "rpc_ok": rpc_ok,
            "rpc_error": rpc_error,
            "reason": body.reason,
        },
        result="ok" if rpc_ok else "error",
        error_message=None if rpc_ok else (rpc_error or "kill not confirmed"),
    )
    return {
        "killed": killed,
        "ok": rpc_ok,
        "error": rpc_error,
        "mock_warning": rpc_result.get("mock_warning") if rpc_ok else None,
    }
