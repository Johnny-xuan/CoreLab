"""Phase 7 follow-up — /api/v1/notifications REST tests.

Covers the C6 backend gap (planner verify §6.2 ack):
* GET /notifications — newest-first list + unread_count.
* GET /notifications?since= — REST catch-up after WS reconnect.
* POST /{id}/mark-read — single row + only_recipient gate.
* POST /mark-all-read — bulk + count returned.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from corelab_backend.db import get_session_factory
from corelab_backend.models import Lab, Notification, User
from corelab_backend.security import hash_password
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def two_users(integration_client: AsyncClient) -> dict[str, Any]:
    """Two users in one lab + a real login bearer for each."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="Phase7 Notif API Lab", slug="phase7-notif-api")
        session.add(lab)
        await session.flush()
        gina = User(
            lab_id=lab.id,
            username="gina",
            email="gina@notif.test",
            display_name="Gina",
            password_hash=hash_password("GinaPass!2024"),  # pragma: allowlist secret
            role="user",
        )
        henry = User(
            lab_id=lab.id,
            username="henry",
            email="henry@notif.test",
            display_name="Henry",
            password_hash=hash_password("HenryPass!2024"),  # pragma: allowlist secret
            role="user",
        )
        session.add_all([gina, henry])
        await session.flush()

    async def _login(client: AsyncClient, username: str, password: str) -> str:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert resp.status_code == 200, resp.text
        return str(resp.json()["access_token"])

    gina_token = await _login(
        integration_client, "gina", "GinaPass!2024"
    )  # pragma: allowlist secret
    henry_token = await _login(
        integration_client, "henry", "HenryPass!2024"
    )  # pragma: allowlist secret

    return {
        "lab_id": lab.id,
        "gina_id": gina.id,
        "henry_id": henry.id,
        "gina_token": gina_token,
        "henry_token": henry_token,
    }


# ─── tests ──────────────────────────────────────────────────────────


async def test_list_notifications_newest_first_with_unread_count(
    integration_client: AsyncClient,
    two_users: dict[str, Any],
) -> None:
    """3 unread + 1 read → unread_count=3, items sorted newest first."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        for i in range(3):
            session.add(
                Notification(
                    recipient_user_id=two_users["gina_id"],
                    type="reservation.started",
                    severity="info",
                    title=f"#{i}",
                    payload={"reservation_id": i},
                    is_read=0,
                )
            )
        # Already-read one (must still appear in list but not unread_count).
        session.add(
            Notification(
                recipient_user_id=two_users["gina_id"],
                type="reservation.completed",
                severity="info",
                title="old",
                payload={"reservation_id": 999},
                is_read=1,
                read_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )

    resp = await integration_client.get(
        "/api/v1/notifications?limit=20",
        headers={"Authorization": f"Bearer {two_users['gina_token']}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["unread_count"] == 3
    assert len(body["items"]) == 4
    # Newest first.
    types = [item["type"] for item in body["items"]]
    assert types[0] == "reservation.completed" or types[0] == "reservation.started"


async def test_list_notifications_since_filter(
    integration_client: AsyncClient,
    two_users: dict[str, Any],
) -> None:
    """?since=<iso> returns only rows strictly after the cutoff."""
    factory = get_session_factory()
    cutoff_id: int = -1
    async with factory() as session, session.begin():
        # Old row (should be filtered out).
        old = Notification(
            recipient_user_id=two_users["gina_id"],
            type="reservation.started",
            severity="info",
            title="old",
            payload={"reservation_id": 1},
        )
        session.add(old)
        await session.flush()
        cutoff_id = int(old.id)

    # Choose a cutoff ~1s into the future — old row should be excluded.
    import asyncio as _aio

    await _aio.sleep(1.1)
    cutoff_iso = datetime.now(UTC).isoformat()

    async with factory() as session, session.begin():
        # New row inserted AFTER the cutoff.
        session.add(
            Notification(
                recipient_user_id=two_users["gina_id"],
                type="reservation.completed",
                severity="info",
                title="new",
                payload={"reservation_id": 2},
            )
        )

    resp = await integration_client.get(
        f"/api/v1/notifications?since={cutoff_iso}",
        headers={"Authorization": f"Bearer {two_users['gina_token']}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [item["id"] for item in body["items"]]
    assert cutoff_id not in ids
    assert len(body["items"]) == 1
    assert body["items"][0]["title"] == "new"


async def test_mark_read_single_only_recipient(
    integration_client: AsyncClient,
    two_users: dict[str, Any],
) -> None:
    """gina's row cannot be marked-read by henry."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = Notification(
            recipient_user_id=two_users["gina_id"],
            type="reservation.started",
            severity="info",
            title="x",
            payload={"reservation_id": 5},
        )
        session.add(row)
        await session.flush()
        gina_notif_id = int(row.id)

    # Henry tries to mark Gina's row read → 404 (gate hidden as missing).
    resp = await integration_client.post(
        f"/api/v1/notifications/{gina_notif_id}/mark-read",
        headers={"Authorization": f"Bearer {two_users['henry_token']}"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "NOTIFICATION_NOT_FOUND"

    # Gina succeeds.
    resp = await integration_client.post(
        f"/api/v1/notifications/{gina_notif_id}/mark-read",
        headers={"Authorization": f"Bearer {two_users['gina_token']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["notification"]["is_read"] is True
    assert resp.json()["notification"]["read_at"] is not None


async def test_mark_all_read_bulk(
    integration_client: AsyncClient,
    two_users: dict[str, Any],
) -> None:
    """mark-all-read returns updated count + leaves other users untouched."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        for i in range(4):
            session.add(
                Notification(
                    recipient_user_id=two_users["gina_id"],
                    type="reservation.started",
                    severity="info",
                    title=f"g-{i}",
                    payload={"reservation_id": i},
                )
            )
        # Henry has 1 unread that must NOT be flipped.
        session.add(
            Notification(
                recipient_user_id=two_users["henry_id"],
                type="reservation.started",
                severity="info",
                title="h-1",
                payload={"reservation_id": 100},
            )
        )

    resp = await integration_client.post(
        "/api/v1/notifications/mark-all-read",
        headers={"Authorization": f"Bearer {two_users['gina_token']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 4

    # Confirm Henry's row still unread.
    list_resp = await integration_client.get(
        "/api/v1/notifications",
        headers={"Authorization": f"Bearer {two_users['henry_token']}"},
    )
    assert list_resp.json()["unread_count"] == 1


async def test_list_notifications_invalid_since_returns_422(
    integration_client: AsyncClient,
    two_users: dict[str, Any],
) -> None:
    resp = await integration_client.get(
        "/api/v1/notifications?since=not-a-date",
        headers={"Authorization": f"Bearer {two_users['gina_token']}"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_SINCE"
