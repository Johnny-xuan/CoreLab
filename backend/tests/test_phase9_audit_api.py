"""Phase 9 C0 — /audit-logs REST API tests (P9-1 / P9-2 / P9-15).

Covers docs/05 §3.17 字面:
* 5-dim filter (actor / action / target_type / target_server / created_at_range)
* pagination (page / size / total / total_pages); size > 100 → 422
* URL ``+``→space decode on ``created_at_from`` (Phase 7 lesson #7)
* permission matrix (lab_admin all / server_admin own server / user self / 403)
* GET /audit-logs/{id} detail endpoint
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from corelab_backend.db import get_session_factory
from corelab_backend.models import (
    AuditLog,
    Lab,
    Server,
    ServerAdminGrant,
    User,
)
from corelab_backend.security import hash_password
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _login(client: AsyncClient, *, username: str, password: str) -> str:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def world(integration_client: AsyncClient) -> dict[str, Any]:
    """Seed two servers + three roles + 6 audit rows spread across servers."""
    factory = get_session_factory()
    base_ts = datetime(2026, 6, 5, 12, 0, 0)
    async with factory() as session, session.begin():
        lab = Lab(name="P9 Audit Lab", slug="p9-audit")
        session.add(lab)
        await session.flush()

        lab_admin = User(
            lab_id=lab.id,
            username="alice",
            email="alice@audit.test",
            display_name="Alice",
            password_hash=hash_password("AlicePass!2024"),  # pragma: allowlist secret
            role="lab_admin",
        )
        server_admin = User(
            lab_id=lab.id,
            username="bob",
            email="bob@audit.test",
            display_name="Bob",
            password_hash=hash_password("BobPass!2024"),  # pragma: allowlist secret
            # 'server_admin' status is conferred via ServerAdminGrant
            # below — the user-table role column only accepts
            # ('user', 'lab_admin').
            role="user",
        )
        normal = User(
            lab_id=lab.id,
            username="carol",
            email="carol@audit.test",
            display_name="Carol",
            password_hash=hash_password("CarolPass!2024"),  # pragma: allowlist secret
            role="user",
        )
        session.add_all([lab_admin, server_admin, normal])
        await session.flush()

        owned = Server(
            lab_id=lab.id,
            hostname="gpu-owned.audit",
            display_name="Owned GPU",
            status="online",
            created_by_user_id=lab_admin.id,
        )
        other = Server(
            lab_id=lab.id,
            hostname="gpu-other.audit",
            display_name="Other GPU",
            status="online",
            created_by_user_id=lab_admin.id,
        )
        session.add_all([owned, other])
        await session.flush()

        # Bob is server_admin only on `owned`.
        session.add(
            ServerAdminGrant(
                server_id=owned.id,
                user_id=server_admin.id,
                granted_by_user_id=lab_admin.id,
                is_active=1,
            )
        )
        await session.flush()

        # 6 audit rows.
        rows = [
            AuditLog(
                lab_id=lab.id,
                actor_user_id=lab_admin.id,
                action="reservation.create",
                target_type="reservation",
                target_id=101,
                target_server_id=owned.id,
                payload={"gpu_id": 0},
                result="ok",
                created_at=base_ts,
            ),
            AuditLog(
                lab_id=lab.id,
                actor_user_id=normal.id,
                action="reservation.create",
                target_type="reservation",
                target_id=102,
                target_server_id=owned.id,
                payload={"gpu_id": 1},
                result="ok",
                created_at=base_ts + timedelta(minutes=1),
            ),
            AuditLog(
                lab_id=lab.id,
                actor_user_id=normal.id,
                action="gpu.kill_process",
                target_type="gpu",
                target_id=5,
                target_server_id=owned.id,
                payload={"pid": 12345},
                result="ok",
                created_at=base_ts + timedelta(minutes=2),
            ),
            AuditLog(
                lab_id=lab.id,
                actor_user_id=lab_admin.id,
                action="reservation.create",
                target_type="reservation",
                target_id=103,
                target_server_id=other.id,
                payload={"gpu_id": 0},
                result="ok",
                created_at=base_ts + timedelta(minutes=3),
            ),
            AuditLog(
                lab_id=lab.id,
                actor_user_id=None,
                action="compliance.violation",
                target_type="gpu",
                target_id=7,
                target_server_id=other.id,
                payload={"policy_key": "gpu_hang"},
                result="ok",
                created_at=base_ts + timedelta(minutes=4),
            ),
            AuditLog(
                lab_id=lab.id,
                actor_user_id=server_admin.id,
                action="capability.update",
                target_type="server",
                target_id=owned.id,
                target_server_id=owned.id,
                payload={"capability_key": "gpu.kill_process"},
                result="ok",
                created_at=base_ts + timedelta(minutes=5),
            ),
        ]
        session.add_all(rows)
        await session.flush()
        row_ids = [int(r.id) for r in rows]

    return {
        "client": integration_client,
        "lab_id": lab.id,
        "lab_admin_id": lab_admin.id,
        "server_admin_id": server_admin.id,
        "normal_id": normal.id,
        "owned_server_id": owned.id,
        "other_server_id": other.id,
        "lab_admin_token": await _login(
            integration_client,
            username="alice",
            password="AlicePass!2024",  # pragma: allowlist secret
        ),
        "server_admin_token": await _login(
            integration_client,
            username="bob",
            password="BobPass!2024",  # pragma: allowlist secret
        ),
        "user_token": await _login(
            integration_client,
            username="carol",
            password="CarolPass!2024",  # pragma: allowlist secret
        ),
        "row_ids": row_ids,
        "base_ts": base_ts,
    }


class TestAuditListFilters:
    async def test_lab_admin_sees_all_6_seeded_plus_login_rows(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        # Each /auth/login call also writes an audit_log row, so the
        # fixture's 6 seeded rows are joined by 3 login rows (alice,
        # bob, carol). Filter to ``target_type='reservation'/'gpu'/
        # 'server'`` to count only the seeded rows.
        resp = await client.get("/api/v1/audit-logs", headers=_auth(world["lab_admin_token"]))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 6 seeded + 3 login rows.
        assert body["total"] == 9
        assert body["page"] == 1
        assert body["size"] == 20
        assert body["total_pages"] == 1
        # actor object embed
        for item in body["items"]:
            if item["actor"] is not None:
                assert "id" in item["actor"] and "username" in item["actor"]

    async def test_filter_5_dimensions(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        # actor_user_id filter (normal user) — 2 seeded + 1 login = 3.
        resp = await client.get(
            "/api/v1/audit-logs",
            params={"actor_user_id": world["normal_id"]},
            headers=_auth(world["lab_admin_token"]),
        )
        assert resp.json()["total"] == 3
        # action filter
        resp = await client.get(
            "/api/v1/audit-logs",
            params={"action": "reservation.create"},
            headers=_auth(world["lab_admin_token"]),
        )
        assert resp.json()["total"] == 3
        # target_type filter
        resp = await client.get(
            "/api/v1/audit-logs",
            params={"target_type": "gpu"},
            headers=_auth(world["lab_admin_token"]),
        )
        assert resp.json()["total"] == 2
        # target_server_id filter (owned)
        resp = await client.get(
            "/api/v1/audit-logs",
            params={"target_server_id": world["owned_server_id"]},
            headers=_auth(world["lab_admin_token"]),
        )
        assert resp.json()["total"] == 4
        # created_at_to filter - restrict to seeded rows in 12:00-12:02
        # window (3 seed rows; login rows are in real-now, excluded).
        cutoff = (world["base_ts"] + timedelta(minutes=2)).isoformat()
        resp = await client.get(
            "/api/v1/audit-logs",
            params={"created_at_to": cutoff},
            headers=_auth(world["lab_admin_token"]),
        )
        assert resp.json()["total"] == 3


class TestAuditPagination:
    async def test_pagination_size_2_yields_5_pages(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        # 6 seeded + 3 login rows = 9 total, ceil(9/2) = 5 pages.
        resp = await client.get(
            "/api/v1/audit-logs",
            params={"page": 1, "size": 2},
            headers=_auth(world["lab_admin_token"]),
        )
        body = resp.json()
        assert body["total"] == 9
        assert body["size"] == 2
        assert body["total_pages"] == 5
        assert len(body["items"]) == 2

        resp = await client.get(
            "/api/v1/audit-logs",
            params={"page": 5, "size": 2},
            headers=_auth(world["lab_admin_token"]),
        )
        body = resp.json()
        assert body["page"] == 5
        # Last page has the remaining 1 row.
        assert len(body["items"]) == 1

    async def test_size_over_100_returns_422(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        resp = await client.get(
            "/api/v1/audit-logs",
            params={"size": 101},
            headers=_auth(world["lab_admin_token"]),
        )
        assert resp.status_code == 422


class TestAuditUrlPlusDecode:
    async def test_created_at_from_handles_plus_decode(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        # Simulate URL +→' ' decoding: backend should restore.
        raw = (world["base_ts"] + timedelta(minutes=2, seconds=30)).isoformat() + " 00:00"
        resp = await client.get(
            "/api/v1/audit-logs",
            params={"created_at_from": raw},
            headers=_auth(world["lab_admin_token"]),
        )
        assert resp.status_code == 200, resp.text

    async def test_created_at_invalid_returns_422(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        resp = await client.get(
            "/api/v1/audit-logs",
            params={"created_at_from": "not-a-date"},
            headers=_auth(world["lab_admin_token"]),
        )
        assert resp.status_code == 422


class TestAuditPermissionMatrix:
    async def test_lab_admin_sees_all(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        resp = await client.get("/api/v1/audit-logs", headers=_auth(world["lab_admin_token"]))
        # 6 seeded + 3 login rows.
        assert resp.json()["total"] == 9

    async def test_server_admin_restricted_to_owned_server(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        resp = await client.get(
            "/api/v1/audit-logs",
            headers=_auth(world["server_admin_token"]),
        )
        body = resp.json()
        # Only 4 rows on owned server.
        assert body["total"] == 4
        for item in body["items"]:
            assert item["target_server_id"] == world["owned_server_id"]

    async def test_server_admin_passing_other_server_id_403(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        resp = await client.get(
            "/api/v1/audit-logs",
            params={"target_server_id": world["other_server_id"]},
            headers=_auth(world["server_admin_token"]),
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "NOT_YOUR_SERVER"

    async def test_user_sees_only_own_actor(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        resp = await client.get("/api/v1/audit-logs", headers=_auth(world["user_token"]))
        body = resp.json()
        # Carol authored 2 seeded rows + 1 login row.
        assert body["total"] == 3
        for item in body["items"]:
            assert item["actor"]["id"] == world["normal_id"]

    async def test_user_passing_other_actor_id_403(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        resp = await client.get(
            "/api/v1/audit-logs",
            params={"actor_user_id": world["lab_admin_id"]},
            headers=_auth(world["user_token"]),
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "ONLY_SELF_ACTOR"


class TestAuditDetail:
    async def test_get_one_lab_admin(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        audit_id = world["row_ids"][0]
        resp = await client.get(
            f"/api/v1/audit-logs/{audit_id}",
            headers=_auth(world["lab_admin_token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["audit"]["id"] == audit_id
        assert body["audit"]["action"] == "reservation.create"

    async def test_get_one_user_other_actor_404(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        # row 0 was authored by lab_admin — user 'carol' should not see it.
        audit_id = world["row_ids"][0]
        resp = await client.get(
            f"/api/v1/audit-logs/{audit_id}",
            headers=_auth(world["user_token"]),
        )
        # Scope filter hides it → 404.
        assert resp.status_code == 404
