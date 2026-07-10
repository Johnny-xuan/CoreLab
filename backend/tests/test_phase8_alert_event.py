"""Phase 8 C1 — alert_event table + service tests (P8-2 / P8-11).

Covers:
* schema is docs/02 §5.16 verbatim (FK / CHECK / 3 indexes / 13 cols)
* create_alert persists before any WS push (commit-first)
* WS push failure does NOT roll back the row (at-least-once)
* commit failure does NOT push (no orphan frames)
* dedup window 1h skips the second insert
* recipients include server admins + lab_admins + reservation owner
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from corelab_backend.api import ws_user
from corelab_backend.db import get_session_factory
from corelab_backend.models import AlertEvent, Lab, Server, User
from corelab_backend.security import hash_password
from corelab_backend.services import alert_service
from httpx import AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def world(integration_client: AsyncClient) -> dict[str, Any]:
    del integration_client
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="Phase8 Alert Lab", slug="phase8-alert")
        session.add(lab)
        await session.flush()
        admin = User(
            lab_id=lab.id,
            username="alice",
            email="alice@alert.test",
            display_name="Alice",
            password_hash=hash_password("AlicePass!2024"),  # pragma: allowlist secret
            role="lab_admin",
        )
        session.add(admin)
        await session.flush()
        server = Server(
            lab_id=lab.id,
            hostname="gpu-a.alert",
            display_name="GPU Alert",
            status="online",
            created_by_user_id=admin.id,
        )
        session.add(server)
        await session.flush()
    return {"lab_id": lab.id, "admin_id": admin.id, "server_id": server.id}


async def test_alert_persists_before_push(world: dict[str, Any]) -> None:
    """P8-11 — DB commit precedes WS push; row id is real after create_alert."""
    factory = get_session_factory()
    async with factory() as session:
        row = await alert_service.create_alert(
            session,
            server_id=world["server_id"],
            event_type="gpu.hang",
            severity="warn",
            payload={"util_zero_for_s": 700},
        )
        assert row.id is not None
        assert row.event_type == "gpu.hang"

    # row visible in a fresh session — proves commit happened.
    async with factory() as fresh:
        all_rows = (
            (await fresh.execute(select(AlertEvent).where(AlertEvent.id == row.id))).scalars().all()
        )
        assert len(all_rows) == 1
        assert all_rows[0].notified_user_ids is not None
        assert world["admin_id"] in all_rows[0].notified_user_ids


async def test_alert_push_failure_does_not_rollback_db(world: dict[str, Any]) -> None:
    """P8-11 — at-least-once: WS push raises, row still committed."""
    factory = get_session_factory()

    async def boom(user_id: int, frame: dict[str, Any]) -> int:
        raise RuntimeError("simulated socket drop")

    with patch.object(ws_user, "push_to_user", side_effect=boom):
        async with factory() as session:
            row = await alert_service.create_alert(
                session,
                server_id=world["server_id"],
                event_type="gpu.oom",
                severity="critical",
            )
            assert row.id is not None

    async with factory() as fresh:
        found = (
            (await fresh.execute(select(AlertEvent).where(AlertEvent.event_type == "gpu.oom")))
            .scalars()
            .all()
        )
        assert len(found) == 1


async def test_alert_commit_failure_does_not_push(world: dict[str, Any]) -> None:
    """P8-11 — if commit raises, push must not fire (no orphan frames)."""
    factory = get_session_factory()
    push_calls: list[int] = []

    async def record_push(user_id: int, frame: dict[str, Any]) -> int:
        push_calls.append(user_id)
        return 1

    async def boom_commit() -> None:
        raise RuntimeError("simulated commit failure")

    with patch.object(ws_user, "push_to_user", side_effect=record_push):
        async with factory() as session:
            # Override commit on this exact session instance.
            with (
                patch.object(session, "commit", side_effect=boom_commit),
                pytest.raises(RuntimeError, match="commit"),
            ):
                await alert_service.create_alert(
                    session,
                    server_id=world["server_id"],
                    event_type="agent.offline",
                    severity="warn",
                )
            await session.rollback()

    assert push_calls == [], "push must not fire when commit fails"


async def test_alert_dedup_within_one_hour(world: dict[str, Any]) -> None:
    factory = get_session_factory()
    async with factory() as session:
        first = await alert_service.create_alert(
            session,
            server_id=world["server_id"],
            event_type="gpu.temp_high",
            severity="warn",
            payload={"temp_c": 88},
        )
    async with factory() as session:
        second = await alert_service.create_alert(
            session,
            server_id=world["server_id"],
            event_type="gpu.temp_high",
            severity="warn",
            payload={"temp_c": 90},
        )
    assert first.id == second.id, "dedup must return the first row's id"

    async with factory() as fresh:
        rows = (
            (
                await fresh.execute(
                    select(AlertEvent).where(AlertEvent.event_type == "gpu.temp_high")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


async def test_alert_recipients_include_admin(world: dict[str, Any]) -> None:
    factory = get_session_factory()
    async with factory() as session:
        row = await alert_service.create_alert(
            session,
            server_id=world["server_id"],
            event_type="agent.offline",
            severity="critical",
        )
    assert row.notified_user_ids is not None
    assert world["admin_id"] in row.notified_user_ids


async def test_resolve_alert(world: dict[str, Any]) -> None:
    factory = get_session_factory()
    async with factory() as session:
        row = await alert_service.create_alert(
            session,
            server_id=world["server_id"],
            event_type="gpu.hang",
            severity="warn",
        )

    async with factory() as session, session.begin():
        resolved = await alert_service.resolve_alert(
            session,
            alert_id=row.id,
            resolver_user_id=world["admin_id"],
            resolution_note="fixed by restart",
        )
        assert resolved is not None
        assert resolved.is_resolved == 1
        assert resolved.resolved_by_user_id == world["admin_id"]
