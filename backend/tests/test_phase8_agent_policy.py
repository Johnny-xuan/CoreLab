"""Phase 8 C0 — agent_policy table + service + 3 profile preset tests.

Covers P8-1 / P8-4 / P8-15:
* schema is docs/02 §5.18 verbatim (FK / UNIQUE / CHECK / 11 columns)
* the 3 profiles seed exactly the 8 policy_key set
* profile switch overwrites all 8 rows atomically (single audit row)
* update_one writes before/after diff into audit payload
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from corelab_backend.db import get_session_factory
from corelab_backend.models import AgentPolicy, AuditLog, Lab, Server, User
from corelab_backend.security import hash_password
from corelab_backend.services import agent_policy_service
from httpx import AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def world(integration_client: AsyncClient) -> dict[str, Any]:
    del integration_client
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="Phase8 Policy Lab", slug="phase8-policy")
        session.add(lab)
        await session.flush()
        admin = User(
            lab_id=lab.id,
            username="alice",
            email="alice@phase8.test",
            display_name="Alice",
            password_hash=hash_password("AlicePass!2024"),  # pragma: allowlist secret
            role="lab_admin",
        )
        session.add(admin)
        await session.flush()
        server = Server(
            lab_id=lab.id,
            hostname="gpu-p.policy",
            display_name="GPU Policy",
            status="online",
            created_by_user_id=admin.id,
        )
        session.add(server)
        await session.flush()
    return {"lab_id": lab.id, "admin_id": admin.id, "server_id": server.id}


async def test_seed_default_inserts_eight_rows(world: dict[str, Any]) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        inserted = await agent_policy_service.seed_default_profile(
            session,
            server_id=world["server_id"],
            lab_id=world["lab_id"],
            actor_user_id=world["admin_id"],
        )
        assert inserted == 8

    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(AgentPolicy).where(AgentPolicy.server_id == world["server_id"])
                )
            )
            .scalars()
            .all()
        )
        assert sorted(r.policy_key for r in rows) == sorted(agent_policy_service.POLICY_KEYS)
        # standard profile: no_reservation_occupy = notify (docs/02 §5.18 default).
        nrocc = next(r for r in rows if r.policy_key == "no_reservation_occupy")
        assert nrocc.severity == "notify"
        assert nrocc.grace_period_seconds == 300


async def test_seed_default_is_idempotent(world: dict[str, Any]) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        first = await agent_policy_service.seed_default_profile(
            session,
            server_id=world["server_id"],
            lab_id=world["lab_id"],
            actor_user_id=world["admin_id"],
        )
        assert first == 8
    async with factory() as session, session.begin():
        second = await agent_policy_service.seed_default_profile(
            session,
            server_id=world["server_id"],
            lab_id=world["lab_id"],
            actor_user_id=world["admin_id"],
        )
        assert second == 0


async def test_switch_profile_overwrites_all_eight_rows(world: dict[str, Any]) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await agent_policy_service.seed_default_profile(
            session,
            server_id=world["server_id"],
            lab_id=world["lab_id"],
            actor_user_id=world["admin_id"],
        )

    async with factory() as session, session.begin():
        changes = await agent_policy_service.switch_profile(
            session,
            server_id=world["server_id"],
            lab_id=world["lab_id"],
            actor_user_id=world["admin_id"],
            profile="strict",
        )
        # standard → strict changes severity for the majority of rows.
        assert changes >= 5

    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(AgentPolicy).where(AgentPolicy.server_id == world["server_id"])
                )
            )
            .scalars()
            .all()
        )
        sev = {r.policy_key: r.severity for r in rows}
        # strict profile: preemption is the one compliance case that
        # auto-kills; memory/hang top out at warn (human pulls trigger).
        assert sev["no_reservation_occupy"] == "warn"
        assert sev["preempt_others_reservation"] == "auto_kill"
        assert sev["memory_overuse"] == "warn"
        assert sev["gpu_hang"] == "warn"
        # exactly one profile_set audit for the switch (seed wrote another).
        audit_count = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "agent_policy.profile_set",
                        AuditLog.target_server_id == world["server_id"],
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(audit_count) == 2  # seed + switch


async def test_switch_profile_reverse_strict_to_permissive(world: dict[str, Any]) -> None:
    """Symmetric reverse — strict back down to permissive flips everything."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await agent_policy_service.seed_default_profile(
            session,
            server_id=world["server_id"],
            lab_id=world["lab_id"],
            actor_user_id=world["admin_id"],
            profile="strict",
        )

    async with factory() as session, session.begin():
        changes = await agent_policy_service.switch_profile(
            session,
            server_id=world["server_id"],
            lab_id=world["lab_id"],
            actor_user_id=world["admin_id"],
            profile="permissive",
        )
        assert changes >= 5

    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(AgentPolicy).where(AgentPolicy.server_id == world["server_id"])
                )
            )
            .scalars()
            .all()
        )
        sev = {r.policy_key: r.severity for r in rows}
        # permissive: nearly all log_only except script_overrun.
        assert sev["script_overrun_grace"] == "auto_kill"
        assert sev["no_reservation_occupy"] == "log_only"
        assert sev["memory_overuse"] == "log_only"


async def test_update_one_writes_diff_audit(world: dict[str, Any]) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await agent_policy_service.seed_default_profile(
            session,
            server_id=world["server_id"],
            lab_id=world["lab_id"],
            actor_user_id=world["admin_id"],
        )

    async with factory() as session, session.begin():
        await agent_policy_service.update_one(
            session,
            server_id=world["server_id"],
            policy_key="gpu_temp_high",
            lab_id=world["lab_id"],
            actor_user_id=world["admin_id"],
            severity="warn",
            threshold_value={"value": 82, "unit": "celsius"},
            notes="dataset hall is hot in summer",
        )

    async with factory() as session:
        row = (
            await session.execute(
                select(AgentPolicy).where(
                    AgentPolicy.server_id == world["server_id"],
                    AgentPolicy.policy_key == "gpu_temp_high",
                )
            )
        ).scalar_one()
        assert row.severity == "warn"
        assert row.threshold_value == {"value": 82, "unit": "celsius"}
        assert row.notes is not None and "dataset" in row.notes

        audit = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "agent_policy.update")
                )
            )
            .scalars()
            .all()
        )
        assert len(audit) == 1
        payload = audit[0].payload or {}
        assert payload.get("policy_key") == "gpu_temp_high"
        assert payload.get("before", {}).get("severity") == "notify"
        assert payload.get("after", {}).get("severity") == "warn"


async def test_unknown_policy_key_raises(world: dict[str, Any]) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        with pytest.raises(agent_policy_service.UnknownPolicyKeyError):
            await agent_policy_service.update_one(
                session,
                server_id=world["server_id"],
                policy_key="not_a_real_key",
                lab_id=world["lab_id"],
                actor_user_id=world["admin_id"],
                severity="notify",
            )


async def test_auto_kill_rejected_on_non_preempt_key(world: dict[str, Any]) -> None:
    """Only preemption (+ script_overrun_grace) may be set to auto_kill;
    memory_overuse=auto_kill is refused at the service layer."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await agent_policy_service.seed_default_profile(
            session,
            server_id=world["server_id"],
            lab_id=world["lab_id"],
            actor_user_id=world["admin_id"],
        )
    async with factory() as session, session.begin():
        with pytest.raises(agent_policy_service.AutoKillNotAllowedError):
            await agent_policy_service.update_one(
                session,
                server_id=world["server_id"],
                policy_key="memory_overuse",
                lab_id=world["lab_id"],
                actor_user_id=world["admin_id"],
                severity="auto_kill",
            )


async def test_auto_kill_allowed_on_preempt_key(world: dict[str, Any]) -> None:
    """preempt_others_reservation=auto_kill is accepted."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await agent_policy_service.seed_default_profile(
            session,
            server_id=world["server_id"],
            lab_id=world["lab_id"],
            actor_user_id=world["admin_id"],
        )
    async with factory() as session, session.begin():
        row = await agent_policy_service.update_one(
            session,
            server_id=world["server_id"],
            policy_key="preempt_others_reservation",
            lab_id=world["lab_id"],
            actor_user_id=world["admin_id"],
            severity="auto_kill",
        )
        assert row.severity == "auto_kill"
