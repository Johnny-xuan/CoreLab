"""Phase 9 C1 — FU-37 threshold_value JSON migration + per-policy schema (P9-3).

Covers:
* New ``standard`` preset stores per-policy_key dicts (memory_overuse
  → {value, unit}; gpu_hang → {util_zero_seconds, mem_floor_mb};
  gpu_temp_high → {value, unit}; 5 keys NULL).
* ``validate_threshold`` accepts well-formed payloads, rejects shape
  mismatches with :class:`InvalidThresholdError`, and strips payloads
  for the 5 no-threshold keys.
* PUT /policy/{key} with a malformed threshold returns 422
  ``INVALID_THRESHOLD``.
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from corelab_backend.db import get_session_factory
from corelab_backend.models import AgentPolicy, Lab, Server, User
from corelab_backend.security import hash_password
from corelab_backend.services import agent_policy_service
from corelab_backend.services.agent_policy_service import (
    InvalidThresholdError,
    validate_threshold,
)
from httpx import AsyncClient
from sqlalchemy import select


async def _login(client: AsyncClient, *, username: str, password: str) -> str:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest_asyncio.fixture
async def world(integration_client: AsyncClient) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="P9 FU37 Lab", slug="p9-fu37")
        session.add(lab)
        await session.flush()
        admin = User(
            lab_id=lab.id,
            username="alice",
            email="alice@fu37.test",
            display_name="Alice",
            password_hash=hash_password("AlicePass!2024"),  # pragma: allowlist secret
            role="lab_admin",
        )
        session.add(admin)
        await session.flush()
        server = Server(
            lab_id=lab.id,
            hostname="gpu-fu37.test",
            display_name="FU37 GPU",
            status="online",
            created_by_user_id=admin.id,
        )
        session.add(server)
        await session.flush()
    token = await _login(
        integration_client,
        username="alice",
        password="AlicePass!2024",  # pragma: allowlist secret
    )
    return {
        "client": integration_client,
        "lab_id": lab.id,
        "admin_id": admin.id,
        "server_id": server.id,
        "token": token,
    }


class TestThresholdSchemas:
    def test_no_threshold_keys_strip_payload(self) -> None:
        for key in (
            "no_reservation_occupy",
            "preempt_others_reservation",
            "script_overrun_grace",
            "zombie_process",
            "unlinked_user_occupy",
        ):
            assert validate_threshold(key, None) is None
            # Even a passed payload is stripped — these keys do not
            # carry a threshold.
            assert validate_threshold(key, {"value": 999}) is None

    def test_memory_overuse_pct_validates(self) -> None:
        out = validate_threshold("memory_overuse", {"value": 30, "unit": "pct"})
        assert out == {"value": 30, "unit": "pct"}

    def test_memory_overuse_pct_default_unit_field(self) -> None:
        out = validate_threshold("memory_overuse", {"value": 25})
        assert out == {"value": 25, "unit": "pct"}

    def test_memory_overuse_pct_out_of_range_rejected(self) -> None:
        with pytest.raises(InvalidThresholdError):
            validate_threshold("memory_overuse", {"value": 150, "unit": "pct"})

    def test_gpu_hang_dual_threshold_validates(self) -> None:
        out = validate_threshold("gpu_hang", {"util_zero_seconds": 300, "mem_floor_mb": 2048})
        assert out == {"util_zero_seconds": 300, "mem_floor_mb": 2048}

    def test_gpu_hang_missing_field_rejected(self) -> None:
        with pytest.raises(InvalidThresholdError):
            validate_threshold("gpu_hang", {"util_zero_seconds": 600})

    def test_gpu_temp_high_celsius_validates(self) -> None:
        out = validate_threshold("gpu_temp_high", {"value": 90, "unit": "celsius"})
        assert out == {"value": 90, "unit": "celsius"}


class TestPresetsCarryDicts:
    async def test_seeded_standard_writes_per_key_dicts(self, world: dict[str, Any]) -> None:
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await agent_policy_service.seed_default_profile(
                session,
                server_id=world["server_id"],
                lab_id=world["lab_id"],
                actor_user_id=world["admin_id"],
            )
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentPolicy)
                        .where(AgentPolicy.server_id == world["server_id"])
                        .order_by(AgentPolicy.policy_key)
                    )
                )
                .scalars()
                .all()
            )
        by_key = {r.policy_key: r.threshold_value for r in rows}
        assert by_key["memory_overuse"] == {"value": 20, "unit": "pct"}
        assert by_key["gpu_hang"] == {"util_zero_seconds": 600, "mem_floor_mb": 1024}
        assert by_key["gpu_temp_high"] == {"value": 85, "unit": "celsius"}
        for k in (
            "no_reservation_occupy",
            "preempt_others_reservation",
            "script_overrun_grace",
            "zombie_process",
            "unlinked_user_occupy",
        ):
            assert by_key[k] is None


class TestPolicyApiThresholdValidation:
    async def test_put_invalid_threshold_returns_422(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await agent_policy_service.seed_default_profile(
                session,
                server_id=world["server_id"],
                lab_id=world["lab_id"],
                actor_user_id=world["admin_id"],
            )
        resp = await client.put(
            f"/api/v1/servers/{world['server_id']}/policy/gpu_hang",
            headers={"Authorization": f"Bearer {world['token']}"},
            json={"threshold_value": {"util_zero_seconds": 60}},  # missing mem_floor_mb
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "INVALID_THRESHOLD"

    async def test_put_valid_dict_threshold_persists(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await agent_policy_service.seed_default_profile(
                session,
                server_id=world["server_id"],
                lab_id=world["lab_id"],
                actor_user_id=world["admin_id"],
            )
        resp = await client.put(
            f"/api/v1/servers/{world['server_id']}/policy/gpu_hang",
            headers={"Authorization": f"Bearer {world['token']}"},
            json={"threshold_value": {"util_zero_seconds": 300, "mem_floor_mb": 4096}},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["policy"]["threshold_value"] == {
            "util_zero_seconds": 300,
            "mem_floor_mb": 4096,
        }
