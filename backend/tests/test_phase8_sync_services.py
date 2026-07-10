"""Phase 8 C2 — policy_sync + link_cache_sync backend tests
(P8-9 / P8-10).

Verifies:
* compose_payload returns docs/06 §5.11b/c shape from live DB rows
* etag is stable for the same set
* push_to_server tolerates AgentOfflineError (returns False)
* link cache full vs incremental composition
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from corelab_backend.db import get_session_factory
from corelab_backend.models import (
    AccountLink,
    Gpu,
    Lab,
    PhysicalAccount,
    Reservation,
    Server,
    User,
)
from corelab_backend.security import hash_password
from corelab_backend.services import (
    agent_policy_service,
    agent_rpc,
    link_cache_sync_service,
    policy_sync_service,
)
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def world(integration_client: AsyncClient) -> dict[str, Any]:
    del integration_client
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="P8 Sync Lab", slug="p8-sync")
        session.add(lab)
        await session.flush()
        admin = User(
            lab_id=lab.id,
            username="alice",
            email="alice@sync.test",
            display_name="Alice",
            password_hash=hash_password("AlicePass!2024"),  # pragma: allowlist secret
            role="lab_admin",
        )
        ivy = User(
            lab_id=lab.id,
            username="ivy",
            email="ivy@sync.test",
            display_name="Ivy",
            password_hash=hash_password("IvyPass!2024"),  # pragma: allowlist secret
            role="user",
        )
        session.add_all([admin, ivy])
        await session.flush()
        server = Server(
            lab_id=lab.id,
            hostname="gpu-s.sync",
            display_name="GPU Sync",
            status="online",
            created_by_user_id=admin.id,
        )
        session.add(server)
        await session.flush()
        gpu = Gpu(
            server_id=server.id,
            gpu_index=0,
            uuid="GPU-aaaa-bbbb",
            model="RTX 4090",
            memory_total_mb=24576,
        )
        session.add(gpu)
        await session.flush()
        pa = PhysicalAccount(
            server_id=server.id,
            linux_username="ivy_lab",
            uid=1200,
            source="admin_manual_register",
            created_by_user_id=admin.id,
        )
        session.add(pa)
        await session.flush()
        link = AccountLink(
            user_id=ivy.id,
            physical_account_id=pa.id,
            source="ssh_challenge",
            established_by_user_id=ivy.id,
            proof_evidence={"ts": "2026-06-05T00:00:00+00:00"},
            is_active=1,
        )
        session.add(link)
        await session.flush()
        # Live reservation for ivy.
        res = Reservation(
            user_id=ivy.id,
            server_id=server.id,
            gpu_id=gpu.id,
            account_link_id=link.id,
            start_at=datetime.now(UTC) - timedelta(minutes=10),
            end_at=datetime.now(UTC) + timedelta(hours=2),
            status="active",
        )
        session.add(res)
        await session.flush()
    return {
        "lab_id": lab.id,
        "admin_id": admin.id,
        "ivy_id": ivy.id,
        "server_id": server.id,
        "gpu_id": gpu.id,
        "linux_username": pa.linux_username,
        "link_id": link.id,
        "reservation_id": res.id,
    }


class TestPolicySync:
    async def test_compose_payload_includes_all_seeded_rows(self, world: dict[str, Any]) -> None:
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await agent_policy_service.seed_default_profile(
                session,
                server_id=world["server_id"],
                lab_id=world["lab_id"],
                actor_user_id=world["admin_id"],
            )
        async with factory() as session:
            payload = await policy_sync_service.compose_payload(
                session, server_id=world["server_id"]
            )
        assert payload["server_id"] == world["server_id"]
        keys = {p["key"] for p in payload["policies"]}
        assert keys == set(agent_policy_service.POLICY_KEYS)
        assert payload["etag"].startswith("policy-")

    async def test_etag_stable_for_same_state(self, world: dict[str, Any]) -> None:
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await agent_policy_service.seed_default_profile(
                session,
                server_id=world["server_id"],
                lab_id=world["lab_id"],
                actor_user_id=world["admin_id"],
            )
        async with factory() as session:
            p1 = await policy_sync_service.compose_payload(session, server_id=world["server_id"])
        async with factory() as session:
            p2 = await policy_sync_service.compose_payload(session, server_id=world["server_id"])
        assert p1["etag"] == p2["etag"]

    async def test_push_tolerates_agent_offline(self, world: dict[str, Any]) -> None:
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await agent_policy_service.seed_default_profile(
                session,
                server_id=world["server_id"],
                lab_id=world["lab_id"],
                actor_user_id=world["admin_id"],
            )

        async def offline(**_: Any) -> dict[str, Any]:
            raise agent_rpc.AgentOfflineError("no live agent")

        with patch.object(agent_rpc, "request_response", side_effect=offline):
            async with factory() as session:
                ok = await policy_sync_service.push_to_server(session, server_id=world["server_id"])
        assert ok is False  # offline is a warn, not an exception.

    async def test_push_returns_true_on_agent_ok(self, world: dict[str, Any]) -> None:
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await agent_policy_service.seed_default_profile(
                session,
                server_id=world["server_id"],
                lab_id=world["lab_id"],
                actor_user_id=world["admin_id"],
            )

        async def ok_response(**_: Any) -> dict[str, Any]:
            return {"ok": True, "applied": True, "etag_now": "policy-zzz"}

        with patch.object(agent_rpc, "request_response", side_effect=ok_response):
            async with factory() as session:
                ok = await policy_sync_service.push_to_server(session, server_id=world["server_id"])
        assert ok is True

    async def test_send_on_connect_direct_sends_without_rpc(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sent: list[str] = []

        class FakeWs:
            async def send_text(self, frame: str) -> None:
                sent.append(frame)

        async def fake_compose(*_: Any, **__: Any) -> dict[str, Any]:
            return {
                "server_id": 7,
                "policies": [
                    {
                        "key": "no_reservation_occupy",
                        "enabled": True,
                        "severity": "notify",
                        "threshold_value": None,
                        "grace_period_seconds": None,
                        "notify_admin": True,
                    }
                ],
                "etag": "policy-test",
            }

        monkeypatch.setattr(policy_sync_service, "compose_payload", fake_compose)
        monkeypatch.setattr(policy_sync_service.agent_hub.pool, "get", lambda server_id: FakeWs())

        ok = await policy_sync_service.send_on_connect(object(), server_id=7)  # type: ignore[arg-type]

        assert ok is True
        frame = json.loads(sent[0])
        assert frame["type"] == "backend.policy.sync"
        assert frame["correlation_id"] is None


class TestLinkCacheSync:
    async def test_compose_full_includes_link_and_reservation(self, world: dict[str, Any]) -> None:
        factory = get_session_factory()
        async with factory() as session:
            payload = await link_cache_sync_service.compose_full_payload(
                session, server_id=world["server_id"]
            )
        assert payload["mode"] == "full"
        entries = payload["entries"]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["linux_username"] == world["linux_username"]
        assert world["ivy_id"] in entry["user_ids"]
        active = entry["active_reservations"][str(world["ivy_id"])]
        assert len(active) == 1
        assert active[0]["reservation_id"] == world["reservation_id"]
        assert active[0]["gpu_id"] == world["gpu_id"]
        assert active[0]["source"] == "ssh_challenge"

    async def test_send_on_connect_direct_sends_without_rpc(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sent: list[str] = []

        class FakeWs:
            async def send_text(self, frame: str) -> None:
                sent.append(frame)

        async def fake_compose(*_: Any, **__: Any) -> dict[str, Any]:
            return {
                "server_id": 7,
                "mode": "full",
                "entries": [],
                "removed_linux_usernames": [],
                "etag": "links-test",
            }

        monkeypatch.setattr(link_cache_sync_service, "compose_full_payload", fake_compose)
        monkeypatch.setattr(
            link_cache_sync_service.agent_hub.pool, "get", lambda server_id: FakeWs()
        )

        ok = await link_cache_sync_service.send_on_connect(object(), server_id=7)  # type: ignore[arg-type]

        assert ok is True
        frame = json.loads(sent[0])
        assert frame["type"] == "backend.account_link_cache.sync"
        assert frame["correlation_id"] is None

    async def test_compose_incremental_targets_one_linux_user(self, world: dict[str, Any]) -> None:
        factory = get_session_factory()
        async with factory() as session:
            payload = await link_cache_sync_service.compose_incremental_payload(
                session,
                server_id=world["server_id"],
                linux_usernames=[world["linux_username"]],
            )
        assert payload["mode"] == "incremental"
        assert len(payload["entries"]) == 1
        assert payload["removed_linux_usernames"] == []

    async def test_push_full_tolerates_offline(self, world: dict[str, Any]) -> None:
        async def offline(**_: Any) -> dict[str, Any]:
            raise agent_rpc.AgentOfflineError("no live agent")

        factory = get_session_factory()
        with patch.object(agent_rpc, "request_response", side_effect=offline):
            async with factory() as session:
                ok = await link_cache_sync_service.push_full_to_server(
                    session, server_id=world["server_id"]
                )
        assert ok is False
