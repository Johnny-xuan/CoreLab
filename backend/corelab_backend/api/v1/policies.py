"""``/api/v1/servers/{server_id}/policy*`` — agent_policy admin surface.

Three endpoints (P8-14 / P8-15):
* ``GET  /servers/{id}/policy`` — list the 8 rows (server_admin
  + lab_admin only)
* ``PUT  /servers/{id}/policy/{policy_key}`` — single-row update
  (server_admin + lab_admin only). Response includes the optional
  ``capability_warning`` string when admin sets severity=auto_kill
  on a policy_key whose ``gpu.kill_process`` capability is off
  (P8-7 co-invariant — agent will downgrade to warn at runtime).
* ``POST /servers/{id}/policy/profile`` — one-shot switch to a named
  preset (permissive / standard / strict).

All routes call :mod:`policy_sync_service.push_to_server` *after*
the write transaction commits so the agent gets the new config
immediately (offline agents catch up on next reconnect).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import AuthenticatedUser, get_current_user
from ...db import get_session, get_session_factory
from ...models import AgentPolicy, Server, ServerAdminGrant
from ...services import agent_policy_service, policy_sync_service

router = APIRouter(prefix="/servers", tags=["policies"])


# ─── permission helper ──────────────────────────────────────────────


async def _require_policy_admin(
    server_id: int,
    current: AuthenticatedUser,
    session: AsyncSession,
) -> Server:
    """server_admin OR lab_admin only; 404 if server not in caller's lab."""
    server = await session.get(Server, server_id)
    if server is None or server.lab_id != current.user.lab_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SERVER_NOT_FOUND"},
        )
    if current.role == "lab_admin":
        return server
    # Otherwise must hold an active server_admin_grant for this server.
    grant = (
        await session.execute(
            select(ServerAdminGrant).where(
                ServerAdminGrant.server_id == server_id,
                ServerAdminGrant.user_id == current.user.id,
                ServerAdminGrant.is_active == 1,
            )
        )
    ).scalar_one_or_none()
    if grant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "POLICY_ADMIN_REQUIRED"},
        )
    return server


def _serialise(row: AgentPolicy) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "policy_key": row.policy_key,
        "enabled": bool(row.enabled),
        "severity": row.severity,
        "threshold_value": row.threshold_value,
        "grace_period_seconds": row.grace_period_seconds,
        "notify_admin": bool(row.notify_admin),
        "notes": row.notes,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "updated_by_user_id": int(row.updated_by_user_id),
    }


# ─── GET list ────────────────────────────────────────────────────────


@router.get("/{server_id}/policy")
async def list_policy(
    server_id: int = Path(..., ge=1),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _require_policy_admin(server_id, current, session)
    rows = await agent_policy_service.list_for_server(session, server_id=server_id)
    return {"items": [_serialise(r) for r in rows]}


# ─── PUT single-key update ──────────────────────────────────────────


class PolicyUpdateRequest(BaseModel):
    enabled: bool | None = None
    severity: agent_policy_service.Severity | None = None
    # FU-37 — per-policy_key schema is enforced by
    # ``agent_policy_service.validate_threshold`` (422 on mismatch).
    threshold_value: dict[str, Any] | None = None
    grace_period_seconds: int | None = Field(default=None, ge=0)
    notify_admin: bool | None = None
    notes: str | None = Field(default=None, max_length=500)


@router.put("/{server_id}/policy/{policy_key}")
async def update_policy(
    body: PolicyUpdateRequest,
    server_id: int = Path(..., ge=1),
    policy_key: str = Path(..., min_length=1),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    server = await _require_policy_admin(server_id, current, session)

    # get_session() commits the autobegin transaction on success / on
    # HTTPException, so no explicit ``async with session.begin()``
    # block is needed — and using one would conflict with the autobegin
    # already triggered by the permission check above.
    capability_warning: str | None = None
    if body.severity is not None:
        capability_warning = await agent_policy_service.auto_kill_capability_warning(
            session,
            server_id=server_id,
            policy_key=policy_key,
            severity=body.severity,
        )
    try:
        row = await agent_policy_service.update_one(
            session,
            server_id=server_id,
            policy_key=policy_key,
            lab_id=server.lab_id,
            actor_user_id=current.user.id,
            enabled=body.enabled,
            severity=body.severity,
            threshold_value=body.threshold_value,
            grace_period_seconds=body.grace_period_seconds,
            notify_admin=body.notify_admin,
            notes=body.notes,
        )
    except agent_policy_service.UnknownPolicyKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "UNKNOWN_POLICY_KEY", "policy_key": policy_key},
        ) from exc
    except agent_policy_service.InvalidThresholdError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_THRESHOLD",
                "policy_key": policy_key,
                "error": str(exc),
            },
        ) from exc
    except agent_policy_service.AutoKillNotAllowedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "AUTO_KILL_NOT_ALLOWED",
                "policy_key": policy_key,
                "error": str(exc),
            },
        ) from exc

    # Commit before pushing so the agent never sees a row we later
    # roll back. The push uses its own short-lived session because the
    # current session is mid-transaction and policy_sync_service is
    # itself idempotent (etag-based).
    await session.commit()
    factory = get_session_factory()
    async with factory() as push_session:
        push_ok = await policy_sync_service.push_to_server(push_session, server_id=server_id)

    response: dict[str, Any] = {"policy": _serialise(row), "pushed_to_agent": push_ok}
    if capability_warning is not None:
        response["capability_warning"] = capability_warning
    return response


# ─── POST profile switch ────────────────────────────────────────────


class ProfileSwitchRequest(BaseModel):
    profile: agent_policy_service.ProfileName


@router.post("/{server_id}/policy/profile")
async def switch_profile(
    body: ProfileSwitchRequest,
    server_id: int = Path(..., ge=1),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    server = await _require_policy_admin(server_id, current, session)
    # get_session() commits the autobegin transaction on exit.
    existing = await agent_policy_service.list_for_server(session, server_id=server_id)
    if not existing:
        await agent_policy_service.seed_default_profile(
            session,
            server_id=server_id,
            lab_id=server.lab_id,
            actor_user_id=current.user.id,
            profile=body.profile,
        )
        changes = 8
    else:
        try:
            changes = await agent_policy_service.switch_profile(
                session,
                server_id=server_id,
                lab_id=server.lab_id,
                actor_user_id=current.user.id,
                profile=body.profile,
            )
        except agent_policy_service.UnknownProfileError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "UNKNOWN_PROFILE", "profile": body.profile},
            ) from exc

    await session.commit()
    factory = get_session_factory()
    async with factory() as push_session:
        push_ok = await policy_sync_service.push_to_server(push_session, server_id=server_id)
    return {
        "profile": body.profile,
        "rows_changed": changes,
        "pushed_to_agent": push_ok,
    }
