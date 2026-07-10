"""Phase 8 C5 — capability x policy co-invariant backend side (P8-7).

Backend half: when admin sets policy severity = auto_kill but the
matching capability (gpu.kill_process) is off, the service returns a
non-fatal warning string so the API (C6) can surface it in the PUT
response. The agent side actually performs the downgrade — covered
by agent/tests/test_phase8_policy_handlers.py::TestAutoKillCapabilitySynergy.
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from corelab_backend.db import get_session_factory
from corelab_backend.models import AgentCapability, Lab, Server, User
from corelab_backend.security import hash_password
from corelab_backend.services import agent_policy_service
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def world(integration_client: AsyncClient) -> dict[str, Any]:
    del integration_client
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="P8 CapPol Lab", slug="p8-cappol")
        session.add(lab)
        await session.flush()
        admin = User(
            lab_id=lab.id,
            username="alice",
            email="alice@cappol.test",
            display_name="Alice",
            password_hash=hash_password("AlicePass!2024"),  # pragma: allowlist secret
            role="lab_admin",
        )
        session.add(admin)
        await session.flush()
        server = Server(
            lab_id=lab.id,
            hostname="gpu-c.cappol",
            display_name="GPU CapPol",
            status="online",
            created_by_user_id=admin.id,
        )
        session.add(server)
        await session.flush()
    return {"lab_id": lab.id, "admin_id": admin.id, "server_id": server.id}


async def test_warning_when_capability_off_and_severity_auto_kill(
    world: dict[str, Any],
) -> None:
    """server_service.create_server seeds gpu.kill_process as off by
    default (is_dangerous=1). Setting severity=auto_kill on a capable
    policy should return the warning string."""
    factory = get_session_factory()
    async with factory() as session:
        warning = await agent_policy_service.auto_kill_capability_warning(
            session,
            server_id=world["server_id"],
            policy_key="preempt_others_reservation",
            severity="auto_kill",
        )
    assert warning is not None
    assert "gpu.kill_process" in warning
    assert "downgrade" in warning


async def test_no_warning_when_capability_on(world: dict[str, Any]) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        # Flip the seeded capability ON.
        from sqlalchemy import select

        cap = (
            await session.execute(
                select(AgentCapability).where(
                    AgentCapability.server_id == world["server_id"],
                    AgentCapability.capability_key == "gpu.kill_process",
                )
            )
        ).scalar_one_or_none()
        if cap is None:
            cap = AgentCapability(
                server_id=world["server_id"],
                capability_key="gpu.kill_process",
                is_enabled=1,
                is_dangerous=1,
                notes="enabled for capxpolicy test",
                updated_by_user_id=world["admin_id"],
            )
            session.add(cap)
        else:
            cap.is_enabled = 1
            cap.notes = "enabled for capxpolicy test"
            cap.updated_by_user_id = world["admin_id"]
        await session.flush()

    async with factory() as session:
        warning = await agent_policy_service.auto_kill_capability_warning(
            session,
            server_id=world["server_id"],
            policy_key="preempt_others_reservation",
            severity="auto_kill",
        )
    assert warning is None


async def test_no_warning_when_severity_is_not_auto_kill(
    world: dict[str, Any],
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        for sev in ("log_only", "notify", "warn"):
            warning = await agent_policy_service.auto_kill_capability_warning(
                session,
                server_id=world["server_id"],
                policy_key="preempt_others_reservation",
                severity=sev,
            )
            assert warning is None, f"severity {sev} should not raise warning"


async def test_no_warning_for_non_kill_capable_policy(world: dict[str, Any]) -> None:
    """zombie_process / gpu_temp_high / unlinked_user_occupy don't kill,
    so even with capability off and severity auto_kill we don't warn —
    auto_kill on them is a misconfiguration we ignore at this layer
    (UI guards it; agent will log_only fallback)."""
    factory = get_session_factory()
    async with factory() as session:
        warning = await agent_policy_service.auto_kill_capability_warning(
            session,
            server_id=world["server_id"],
            policy_key="zombie_process",
            severity="auto_kill",
        )
    assert warning is None
