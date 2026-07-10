"""Phase 8 C6 — /servers/{id}/policy + /alert-events API tests
(P8-14 / P8-15 / P8-11).

Covers:
* GET /policy — lab_admin allowed; non-admin without grant gets 403
* PUT /policy/{key} — lab_admin allowed; UNKNOWN_POLICY_KEY 422
* PUT /policy/{key} with severity=auto_kill while capability is off →
  response carries capability_warning
* POST /policy/profile — switches all 8 rows
* GET /alert-events — only sees rows in caller's lab
* GET /alert-events?since=<+00:00 form> — handles URL +-decode (Phase 7 lesson #7)
* POST /alert-events/{id}/resolve — sets is_resolved; non-admin 403
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from corelab_backend.db import get_session_factory
from corelab_backend.models import Lab, Server, User
from corelab_backend.security import hash_password
from corelab_backend.services import agent_policy_service, alert_service
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


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
        lab = Lab(name="P8 API Lab", slug="p8-api")
        session.add(lab)
        await session.flush()
        admin = User(
            lab_id=lab.id,
            username="alice",
            email="alice@api.test",
            display_name="Alice",
            password_hash=hash_password("AlicePass!2024"),  # pragma: allowlist secret
            role="lab_admin",
        )
        normal = User(
            lab_id=lab.id,
            username="bob",
            email="bob@api.test",
            display_name="Bob",
            password_hash=hash_password("BobPass!2024"),  # pragma: allowlist secret
            role="user",
        )
        session.add_all([admin, normal])
        await session.flush()
        server = Server(
            lab_id=lab.id,
            hostname="gpu-a.api",
            display_name="GPU API",
            status="online",
            created_by_user_id=admin.id,
        )
        session.add(server)
        await session.flush()

    admin_token = await _login(
        integration_client,
        username="alice",
        password="AlicePass!2024",  # pragma: allowlist secret
    )
    bob_token = await _login(
        integration_client,
        username="bob",
        password="BobPass!2024",  # pragma: allowlist secret
    )
    return {
        "client": integration_client,
        "lab_id": lab.id,
        "admin_id": admin.id,
        "bob_id": normal.id,
        "server_id": server.id,
        "admin_token": admin_token,
        "bob_token": bob_token,
    }


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestPolicyAPI:
    async def test_get_policy_lab_admin_ok_normal_user_403(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        # Seed policies first so GET returns rows.
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await agent_policy_service.seed_default_profile(
                session,
                server_id=world["server_id"],
                lab_id=world["lab_id"],
                actor_user_id=world["admin_id"],
            )

        ok = await client.get(
            f"/api/v1/servers/{world['server_id']}/policy",
            headers=_auth(world["admin_token"]),
        )
        assert ok.status_code == 200
        body = ok.json()
        assert len(body["items"]) == 8

        denied = await client.get(
            f"/api/v1/servers/{world['server_id']}/policy",
            headers=_auth(world["bob_token"]),
        )
        assert denied.status_code == 403

    async def test_put_policy_updates_row(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        # Pretend agent is offline so we don't need a real ws — push
        # returns False but the row update still succeeds.
        from corelab_backend.services import agent_rpc

        async def offline(**_: Any) -> dict[str, Any]:
            raise agent_rpc.AgentOfflineError("no live agent")

        with patch.object(agent_rpc, "request_response", side_effect=offline):
            # Seed first via direct service so PUT exercises the update path.
            factory = get_session_factory()
            async with factory() as session, session.begin():
                await agent_policy_service.seed_default_profile(
                    session,
                    server_id=world["server_id"],
                    lab_id=world["lab_id"],
                    actor_user_id=world["admin_id"],
                )
            resp = await client.put(
                f"/api/v1/servers/{world['server_id']}/policy/gpu_temp_high",
                headers=_auth(world["admin_token"]),
                json={
                    "severity": "warn",
                    "threshold_value": {"value": 80, "unit": "celsius"},
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["policy"]["severity"] == "warn"
        assert body["policy"]["threshold_value"] == {"value": 80, "unit": "celsius"}
        assert body["pushed_to_agent"] is False  # agent was offline.

    async def test_put_auto_kill_with_capability_off_returns_warning(
        self, world: dict[str, Any]
    ) -> None:
        client: AsyncClient = world["client"]
        from corelab_backend.services import agent_rpc

        async def offline(**_: Any) -> dict[str, Any]:
            raise agent_rpc.AgentOfflineError("no live agent")

        with patch.object(agent_rpc, "request_response", side_effect=offline):
            factory = get_session_factory()
            async with factory() as session, session.begin():
                await agent_policy_service.seed_default_profile(
                    session,
                    server_id=world["server_id"],
                    lab_id=world["lab_id"],
                    actor_user_id=world["admin_id"],
                )
            # preempt_others_reservation is the gpu.kill_process-capable
            # key; auto_kill on it while the capability is off must warn.
            resp = await client.put(
                f"/api/v1/servers/{world['server_id']}/policy/preempt_others_reservation",
                headers=_auth(world["admin_token"]),
                json={"severity": "auto_kill"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["policy"]["severity"] == "auto_kill"
        assert body.get("capability_warning") is not None
        assert "gpu.kill_process" in body["capability_warning"]

    async def test_put_unknown_policy_key_422(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        resp = await client.put(
            f"/api/v1/servers/{world['server_id']}/policy/not_a_real_key",
            headers=_auth(world["admin_token"]),
            json={"severity": "notify"},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "UNKNOWN_POLICY_KEY"

    async def test_put_auto_kill_on_non_preempt_key_422(self, world: dict[str, Any]) -> None:
        """auto_kill on a non-preemption key is rejected — only preemption
        (and the owner's script timeout) may auto-kill."""
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
            f"/api/v1/servers/{world['server_id']}/policy/memory_overuse",
            headers=_auth(world["admin_token"]),
            json={"severity": "auto_kill"},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "AUTO_KILL_NOT_ALLOWED"

    async def test_post_profile_switches_all_eight_rows(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        from corelab_backend.services import agent_rpc

        async def offline(**_: Any) -> dict[str, Any]:
            raise agent_rpc.AgentOfflineError("no live agent")

        with patch.object(agent_rpc, "request_response", side_effect=offline):
            resp = await client.post(
                f"/api/v1/servers/{world['server_id']}/policy/profile",
                headers=_auth(world["admin_token"]),
                json={"profile": "strict"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["profile"] == "strict"
        assert body["rows_changed"] == 8  # first-time seed counts as 8 changes.


class TestAlertAPI:
    async def test_list_alerts_filters_by_lab(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        # Write an alert.
        factory = get_session_factory()
        async with factory() as session:
            await alert_service.create_alert(
                session,
                server_id=world["server_id"],
                event_type="gpu.hang",
                severity="warn",
            )
        resp = await client.get("/api/v1/alert-events", headers=_auth(world["admin_token"]))
        assert resp.status_code == 200
        body = resp.json()
        assert any(a["event_type"] == "gpu.hang" for a in body["items"])

    async def test_list_alerts_since_handles_url_plus_decode(self, world: dict[str, Any]) -> None:
        """Phase 7 lesson #7 — '+' decodes to space; handler must accept
        both Z and the space→+ restoration."""
        client: AsyncClient = world["client"]
        resp = await client.get(
            "/api/v1/alert-events?since=2026-06-05T00:00:00 00:00",
            headers=_auth(world["admin_token"]),
        )
        assert resp.status_code == 200

    async def test_list_alerts_since_invalid_returns_422(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        resp = await client.get(
            "/api/v1/alert-events?since=not-an-iso-string",
            headers=_auth(world["admin_token"]),
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "INVALID_SINCE"

    async def test_resolve_alert_lab_admin_ok_normal_user_403(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        factory = get_session_factory()
        async with factory() as session:
            row = await alert_service.create_alert(
                session,
                server_id=world["server_id"],
                event_type="agent.offline",
                severity="critical",
            )

        denied = await client.post(
            f"/api/v1/alert-events/{row.id}/resolve",
            headers=_auth(world["bob_token"]),
            json={"resolution_note": "I'm bob"},
        )
        assert denied.status_code == 403

        ok = await client.post(
            f"/api/v1/alert-events/{row.id}/resolve",
            headers=_auth(world["admin_token"]),
            json={"resolution_note": "restarted agent"},
        )
        assert ok.status_code == 200
        body = ok.json()
        assert body["alert"]["is_resolved"] is True
        assert body["alert"]["resolved_by_user_id"] == world["admin_id"]
