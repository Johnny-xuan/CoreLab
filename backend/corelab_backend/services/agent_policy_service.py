"""Phase 8 C0 — agent_policy CRUD + 3 profile preset switcher.

Single public surface so callers (server enrollment seeding + admin
PUT /servers/<id>/policy + POST profile) cannot drift on the
8-policy_key set or the 3-profile semantics (docs/02 §5.18).

Audit:
    agent_policy.update       single-key edit
    agent_policy.profile_set  preset switch (one row, 8 keys rewritten)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AgentCapability, AgentPolicy
from . import audit_service

Severity = Literal["log_only", "notify", "warn", "auto_kill"]
ProfileName = Literal["permissive", "standard", "strict"]


# Phase 9 / FU-37 — per-policy_key threshold schemas. The service
# validates ``threshold_value`` against the schema for the row's key
# before write; the dispatcher on the agent side reads the fields by
# name (see ``agent.policy_handlers``).


class _NoThreshold(BaseModel):
    """Sentinel — keys with no threshold persist ``NULL`` not ``{}``."""


class _PctThreshold(BaseModel):
    value: int = Field(ge=0, le=100)
    unit: Literal["pct"] = "pct"


class _GpuHangThreshold(BaseModel):
    util_zero_seconds: int = Field(ge=10)
    mem_floor_mb: int = Field(ge=0)


class _TempThreshold(BaseModel):
    value: int = Field(ge=0, le=200)
    unit: Literal["celsius"] = "celsius"


THRESHOLD_SCHEMAS: Final[dict[str, type[BaseModel]]] = {
    "no_reservation_occupy": _NoThreshold,
    "preempt_others_reservation": _NoThreshold,
    "script_overrun_grace": _NoThreshold,
    "memory_overuse": _PctThreshold,
    "gpu_hang": _GpuHangThreshold,
    "gpu_temp_high": _TempThreshold,
    "zombie_process": _NoThreshold,
    "unlinked_user_occupy": _NoThreshold,
}


def validate_threshold(
    policy_key: str, threshold_value: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Normalise ``threshold_value`` for the given key.

    Returns ``None`` for the 5 no-threshold keys (drops any payload).
    Raises :class:`InvalidThresholdError` if the payload does not match
    the per-key schema."""
    schema = THRESHOLD_SCHEMAS.get(policy_key)
    if schema is None:
        raise UnknownPolicyKeyError(policy_key)
    if schema is _NoThreshold:
        return None
    if threshold_value is None:
        return None
    try:
        return schema.model_validate(threshold_value).model_dump()
    except ValidationError as exc:
        raise InvalidThresholdError(str(exc)) from exc


# docs/02 §5.18 line 1459-1474 — 8 policy_key 字面.
POLICY_KEYS: Final[tuple[str, ...]] = (
    "no_reservation_occupy",
    "preempt_others_reservation",
    "script_overrun_grace",
    "memory_overuse",
    "gpu_hang",
    "gpu_temp_high",
    "zombie_process",
    "unlinked_user_occupy",
)


def _row(
    severity: Severity,
    *,
    threshold_value: dict[str, Any] | None = None,
    grace_period_seconds: int | None = None,
    notify_admin: bool = True,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "threshold_value": threshold_value,
        "grace_period_seconds": grace_period_seconds,
        "notify_admin": 1 if notify_admin else 0,
    }


# docs/02 §5.18 line 1472-1478 三 profile 字面.
# permissive: 几乎全 log_only,只 script_overrun 仍 auto_kill.
# standard:   违规 notify,不 auto_kill(除 script_overrun);hang/preempt warn.
# strict:     抢占/无预约 warn,memory_overuse/hang 在 grace 后 auto_kill.
_PROFILE_PRESETS: Final[dict[ProfileName, dict[str, dict[str, Any]]]] = {
    "permissive": {
        "no_reservation_occupy": _row("log_only", grace_period_seconds=300),
        "preempt_others_reservation": _row("log_only", grace_period_seconds=60),
        "script_overrun_grace": _row("auto_kill", grace_period_seconds=300),
        "memory_overuse": _row(
            "log_only",
            threshold_value={"value": 20, "unit": "pct"},
            grace_period_seconds=60,
        ),
        "gpu_hang": _row(
            "log_only",
            threshold_value={"util_zero_seconds": 600, "mem_floor_mb": 1024},
            notify_admin=False,
        ),
        "gpu_temp_high": _row(
            "log_only",
            threshold_value={"value": 85, "unit": "celsius"},
            notify_admin=False,
        ),
        "zombie_process": _row("log_only", notify_admin=False),
        "unlinked_user_occupy": _row("log_only", notify_admin=False),
    },
    "standard": {
        "no_reservation_occupy": _row("notify", grace_period_seconds=300),
        "preempt_others_reservation": _row("warn", grace_period_seconds=60),
        "script_overrun_grace": _row("auto_kill", grace_period_seconds=300),
        "memory_overuse": _row(
            "notify",
            threshold_value={"value": 20, "unit": "pct"},
            grace_period_seconds=60,
        ),
        "gpu_hang": _row(
            "notify",
            threshold_value={"util_zero_seconds": 600, "mem_floor_mb": 1024},
            notify_admin=False,
        ),
        "gpu_temp_high": _row(
            "notify",
            threshold_value={"value": 85, "unit": "celsius"},
            notify_admin=False,
        ),
        "zombie_process": _row("notify", notify_admin=False),
        "unlinked_user_occupy": _row("log_only", notify_admin=False),
    },
    "strict": {
        "no_reservation_occupy": _row("warn", grace_period_seconds=300),
        # strict is the "establish authority" profile — preemption is
        # the one case a machine may terminate on its own (only after
        # the 60s grace, and only if gpu.kill_process is enabled).
        "preempt_others_reservation": _row("auto_kill", grace_period_seconds=60),
        "script_overrun_grace": _row("auto_kill", grace_period_seconds=300),
        # memory_overuse / gpu_hang top out at warn even in strict —
        # killing a borderline-quota or idle-but-nobody-waiting job is
        # a human judgement call (manual kill button on the alert).
        "memory_overuse": _row(
            "warn",
            threshold_value={"value": 20, "unit": "pct"},
            grace_period_seconds=60,
        ),
        "gpu_hang": _row(
            "warn",
            threshold_value={"util_zero_seconds": 600, "mem_floor_mb": 1024},
            notify_admin=True,
        ),
        "gpu_temp_high": _row(
            "warn",
            threshold_value={"value": 85, "unit": "celsius"},
            notify_admin=True,
        ),
        "zombie_process": _row("notify", notify_admin=True),
        "unlinked_user_occupy": _row("notify", notify_admin=True),
    },
}


def profile_preset(name: ProfileName) -> dict[str, dict[str, Any]]:
    """Return a deep-ish copy of the preset so callers can't mutate the constant."""
    return {k: dict(v) for k, v in _PROFILE_PRESETS[name].items()}


def all_profile_names() -> tuple[ProfileName, ...]:
    return ("permissive", "standard", "strict")


class AgentPolicyError(Exception):
    pass


class UnknownPolicyKeyError(AgentPolicyError):
    pass


class UnknownProfileError(AgentPolicyError):
    pass


class InvalidThresholdError(AgentPolicyError):
    """Raised when the per-policy_key schema validation rejects a payload."""


class AutoKillNotAllowedError(AgentPolicyError):
    """Raised when auto_kill is set on a policy_key that may not auto-kill."""


# docs/04 §9.7.4 — every auto_kill-capable policy_key requires the
# ``gpu.kill_process`` capability on the agent. If admin sets severity
# auto_kill on a policy whose capability is currently off, backend
# returns a warning so the UI can show "agent will downgrade to warn".
# The actual downgrade happens agent-side (P8-7, agent C4 dispatcher).
_AUTO_KILL_CAPABILITY: Final[str] = "gpu.kill_process"

# Keys an admin may *set* to auto_kill. Deliberately narrow:
#   preempt_others_reservation — the one compliance case where a
#       machine may terminate a process on its own: somebody holds a
#       live reservation and the occupier's linked users do not. This
#       is true preemption, and the agent enforces the same floor.
#   script_overrun_grace — NOT a compliance kill of someone else's
#       work: it terminates a script whose own owner declared a
#       max_runtime and opted into being killed past it. Different
#       mechanism (script_runner timeout), kept settable.
# Everything else (no_reservation_occupy, memory_overuse, gpu_hang, …)
# tops out at warn — a human pulls the trigger via the manual kill
# button on the alerts page.
_AUTO_KILL_SETTABLE_KEYS: Final[frozenset[str]] = frozenset(
    {"preempt_others_reservation", "script_overrun_grace"}
)
# Subset that actually needs the gpu.kill_process capability (the
# script timeout uses script.execute_as_user, not the GPU kill cap).
_AUTO_KILL_CAPABLE_KEYS: Final[frozenset[str]] = frozenset({"preempt_others_reservation"})


async def auto_kill_capability_warning(
    session: AsyncSession,
    *,
    server_id: int,
    policy_key: str,
    severity: Severity,
) -> str | None:
    """Return a non-fatal warning string when ``severity == 'auto_kill'``
    but the matching capability is currently disabled on this server.

    Used by the policy API (C6) to expose the warning in the PUT
    response so the admin understands the agent will downgrade to
    warn at runtime (P8-7 co-invariant)."""
    if severity != "auto_kill":
        return None
    if policy_key not in _AUTO_KILL_CAPABLE_KEYS:
        return None
    result = await session.execute(
        select(AgentCapability).where(
            AgentCapability.server_id == server_id,
            AgentCapability.capability_key == _AUTO_KILL_CAPABILITY,
        )
    )
    cap = result.scalar_one_or_none()
    if cap is None or cap.is_enabled == 0:
        return (
            f"capability {_AUTO_KILL_CAPABILITY!r} is off on this server; "
            "agent will downgrade auto_kill to warn at runtime"
        )
    return None


async def list_for_server(session: AsyncSession, *, server_id: int) -> Sequence[AgentPolicy]:
    result = await session.execute(
        select(AgentPolicy)
        .where(AgentPolicy.server_id == server_id)
        .order_by(AgentPolicy.policy_key)
    )
    return result.scalars().all()


async def get(session: AsyncSession, *, server_id: int, policy_key: str) -> AgentPolicy | None:
    result = await session.execute(
        select(AgentPolicy).where(
            AgentPolicy.server_id == server_id,
            AgentPolicy.policy_key == policy_key,
        )
    )
    return result.scalar_one_or_none()


async def seed_default_profile(
    session: AsyncSession,
    *,
    server_id: int,
    lab_id: int,
    actor_user_id: int,
    profile: ProfileName = "standard",
) -> int:
    """Insert 8 rows for the given profile. Idempotent — skips if any
    row already exists for ``server_id`` (server enrollment safe-replay)."""
    existing = await list_for_server(session, server_id=server_id)
    if existing:
        return 0
    preset = profile_preset(profile)
    inserted = 0
    for key in POLICY_KEYS:
        row_data = preset[key]
        session.add(
            AgentPolicy(
                server_id=server_id,
                policy_key=key,
                enabled=1,
                severity=row_data["severity"],
                threshold_value=row_data["threshold_value"],
                grace_period_seconds=row_data["grace_period_seconds"],
                notify_admin=row_data["notify_admin"],
                updated_by_user_id=actor_user_id,
            )
        )
        inserted += 1
    await session.flush()
    await audit_service.write(
        session,
        action="agent_policy.profile_set",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="server",
        target_id=server_id,
        target_lab_id=lab_id,
        target_server_id=server_id,
        payload={"profile": profile, "seeded": True, "rows": inserted},
    )
    return inserted


async def switch_profile(
    session: AsyncSession,
    *,
    server_id: int,
    lab_id: int,
    actor_user_id: int,
    profile: ProfileName,
) -> int:
    """Overwrite all 8 rows for ``server_id`` to match the profile preset.

    All 8 updates happen in the caller's transaction so the switch is
    atomic from the audit caller's POV. Returns the number of rows
    that actually changed values.
    """
    if profile not in _PROFILE_PRESETS:
        raise UnknownProfileError(profile)
    preset = profile_preset(profile)
    existing = {row.policy_key: row for row in await list_for_server(session, server_id=server_id)}
    changes = 0
    for key in POLICY_KEYS:
        row_data = preset[key]
        row = existing.get(key)
        if row is None:
            session.add(
                AgentPolicy(
                    server_id=server_id,
                    policy_key=key,
                    enabled=1,
                    severity=row_data["severity"],
                    threshold_value=row_data["threshold_value"],
                    grace_period_seconds=row_data["grace_period_seconds"],
                    notify_admin=row_data["notify_admin"],
                    updated_by_user_id=actor_user_id,
                )
            )
            changes += 1
            continue
        before = (
            row.severity,
            row.threshold_value,
            row.grace_period_seconds,
            int(row.notify_admin),
        )
        after = (
            row_data["severity"],
            row_data["threshold_value"],
            row_data["grace_period_seconds"],
            int(row_data["notify_admin"]),
        )
        if before != after:
            row.severity = row_data["severity"]
            row.threshold_value = row_data["threshold_value"]
            row.grace_period_seconds = row_data["grace_period_seconds"]
            row.notify_admin = row_data["notify_admin"]
            row.updated_by_user_id = actor_user_id
            changes += 1
    await session.flush()
    await audit_service.write(
        session,
        action="agent_policy.profile_set",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="server",
        target_id=server_id,
        target_lab_id=lab_id,
        target_server_id=server_id,
        payload={"profile": profile, "rows_changed": changes},
    )
    return changes


async def update_one(
    session: AsyncSession,
    *,
    server_id: int,
    policy_key: str,
    lab_id: int,
    actor_user_id: int,
    enabled: bool | None = None,
    severity: Severity | None = None,
    threshold_value: dict[str, Any] | None = None,
    grace_period_seconds: int | None = None,
    notify_admin: bool | None = None,
    notes: str | None = None,
) -> AgentPolicy:
    if policy_key not in POLICY_KEYS:
        raise UnknownPolicyKeyError(policy_key)
    if severity == "auto_kill" and policy_key not in _AUTO_KILL_SETTABLE_KEYS:
        raise AutoKillNotAllowedError(
            f"policy_key {policy_key!r} may not be set to auto_kill; "
            "only preemption may auto-kill (others top out at warn)"
        )
    if threshold_value is not None:
        threshold_value = validate_threshold(policy_key, threshold_value)
    row = await get(session, server_id=server_id, policy_key=policy_key)
    if row is None:
        # Caller seeded server but key missing — auto-insert with the
        # standard preset entry then apply overrides.
        preset = profile_preset("standard")[policy_key]
        row = AgentPolicy(
            server_id=server_id,
            policy_key=policy_key,
            enabled=1,
            severity=preset["severity"],
            threshold_value=preset["threshold_value"],
            grace_period_seconds=preset["grace_period_seconds"],
            notify_admin=preset["notify_admin"],
            updated_by_user_id=actor_user_id,
        )
        session.add(row)
        await session.flush()
    before = {
        "enabled": int(row.enabled),
        "severity": row.severity,
        "threshold_value": row.threshold_value,
        "grace_period_seconds": row.grace_period_seconds,
        "notify_admin": int(row.notify_admin),
        "notes": row.notes,
    }
    if enabled is not None:
        row.enabled = 1 if enabled else 0
    if severity is not None:
        row.severity = severity
    if threshold_value is not None:
        row.threshold_value = threshold_value
    if grace_period_seconds is not None:
        row.grace_period_seconds = grace_period_seconds
    if notify_admin is not None:
        row.notify_admin = 1 if notify_admin else 0
    if notes is not None:
        row.notes = notes
    row.updated_by_user_id = actor_user_id
    await session.flush()
    after = {
        "enabled": int(row.enabled),
        "severity": row.severity,
        "threshold_value": row.threshold_value,
        "grace_period_seconds": row.grace_period_seconds,
        "notify_admin": int(row.notify_admin),
        "notes": row.notes,
    }
    await audit_service.write(
        session,
        action="agent_policy.update",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="agent_policy",
        target_id=row.id,
        target_lab_id=lab_id,
        target_server_id=server_id,
        payload={"policy_key": policy_key, "before": before, "after": after},
    )
    return row
