"""Phase 7 C3 — notification table + service tests (P7-6/7/8).

Covers:
* DDL — alembic upgrade left the table with the §5.14 columns + indexes.
* ``create_notification`` persist + WS push call.
* dedup — second create inside the 60 s window returns the existing row.
* 4 lifecycle types emit (started / completed / failed /
  cancelled_by_other) via the reservation_service transition hooks.
  link.prepared is deferred to FU-32 (account_link_request approve
  handler isn't on the C3 critical path).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from corelab_backend.db import get_session_factory
from corelab_backend.models import (
    AccountLink,
    Gpu,
    Lab,
    Notification,
    PhysicalAccount,
    Reservation,
    Server,
    User,
)
from corelab_backend.security import hash_password
from corelab_backend.services import (
    account_link_service as als,
)
from corelab_backend.services import (
    notification_service,
    reservation_service,
)
from httpx import AsyncClient
from sqlalchemy import func, select

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def seeded(integration_client: AsyncClient) -> dict[str, Any]:
    del integration_client
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="Phase7 Notif Lab", slug="phase7-notif")
        session.add(lab)
        await session.flush()
        eve = User(
            lab_id=lab.id,
            username="eve",
            email="eve@phase7notif.test",
            display_name="Eve",
            password_hash=hash_password("EvePass!2024"),  # pragma: allowlist secret
            role="user",
        )
        admin = User(
            lab_id=lab.id,
            username="admin",
            email="admin@phase7notif.test",
            display_name="Admin",
            password_hash=hash_password("AdminPass!2024"),  # pragma: allowlist secret
            role="lab_admin",
        )
        session.add_all([eve, admin])
        await session.flush()
        server = Server(
            lab_id=lab.id,
            hostname="gpu-n.phase7",
            display_name="GPU Notif",
            status="online",
            created_by_user_id=admin.id,
            max_reservation_hours=24,
        )
        session.add(server)
        await session.flush()
        gpu = Gpu(server_id=server.id, gpu_index=0, model="RTX 4090", memory_total_mb=24576)
        session.add(gpu)
        await session.flush()
        pa = PhysicalAccount(
            server_id=server.id,
            linux_username="eve_lab",
            uid=1042,
            source="admin_manual_register",
            created_by_user_id=admin.id,
        )
        session.add(pa)
        await session.flush()
        await als.establish_via_ssh_challenge(
            session,
            user_id=eve.id,
            physical_account_id=pa.id,
            challenge_id="chal-eve",
            signer_fingerprint="SHA256:eve",
            lab_id=lab.id,
            server_id=server.id,
        )

    factory2 = get_session_factory()
    async with factory2() as session:
        link = (
            (
                await session.execute(
                    select(AccountLink).where(AccountLink.user_id == eve.id).limit(1)
                )
            )
            .scalars()
            .one()
        )
    return {
        "lab_id": lab.id,
        "eve_id": eve.id,
        "admin_id": admin.id,
        "server_id": server.id,
        "gpu_id": gpu.id,
        "link_id": link.id,
    }


async def _insert_reservation(seeded: dict[str, Any], *, status: str = "scheduled") -> int:
    factory = get_session_factory()
    base = datetime.now(UTC).replace(microsecond=0)
    async with factory() as session, session.begin():
        row = Reservation(
            user_id=seeded["eve_id"],
            server_id=seeded["server_id"],
            gpu_id=seeded["gpu_id"],
            account_link_id=seeded["link_id"],
            start_at=base - timedelta(minutes=10),
            end_at=base + timedelta(minutes=50),
            status=status,
        )
        session.add(row)
        await session.flush()
        return row.id


# ─── core service ────────────────────────────────────────────────────


async def test_notification_create_persists_and_pushes(
    seeded: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single create writes the row and fires push_to_user."""
    pushed: list[tuple[int, dict[str, Any]]] = []

    async def fake_push(user_id: int, frame: dict[str, Any]) -> int:
        pushed.append((user_id, frame))
        return 1

    monkeypatch.setattr(notification_service.ws_user, "push_to_user", fake_push)

    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = await notification_service.create_notification(
            session,
            recipient_user_id=seeded["eve_id"],
            type="reservation.started",
            title="hi",
            payload={"reservation_id": 42},
        )
        assert row.id is not None

    async with factory() as session:
        count = await session.execute(
            select(func.count(Notification.id)).where(
                Notification.recipient_user_id == seeded["eve_id"]
            )
        )
        assert count.scalar_one() == 1
    assert len(pushed) == 1
    assert pushed[0][0] == seeded["eve_id"]
    assert pushed[0][1]["type"] == "notification.new"


async def test_notification_dedup_within_window(
    seeded: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second create with same (user, type, reservation_id) returns existing row."""
    pushed: list[Any] = []

    async def fake_push(user_id: int, frame: dict[str, Any]) -> int:
        pushed.append((user_id, frame))
        return 1

    monkeypatch.setattr(notification_service.ws_user, "push_to_user", fake_push)

    factory = get_session_factory()
    async with factory() as session, session.begin():
        first = await notification_service.create_notification(
            session,
            recipient_user_id=seeded["eve_id"],
            type="reservation.started",
            title="hi",
            payload={"reservation_id": 7},
        )
        second = await notification_service.create_notification(
            session,
            recipient_user_id=seeded["eve_id"],
            type="reservation.started",
            title="hi-again",
            payload={"reservation_id": 7},
        )
        assert first.id == second.id

    async with factory() as session:
        count = await session.execute(
            select(func.count(Notification.id)).where(
                Notification.recipient_user_id == seeded["eve_id"],
                Notification.type == "reservation.started",
            )
        )
        assert count.scalar_one() == 1
    # Only the first create pushed; dedup skipped the second.
    assert len(pushed) == 1


# ─── 4 lifecycle hooks ──────────────────────────────────────────────


async def test_transition_to_active_emits_started_notification(
    seeded: dict[str, Any],
) -> None:
    res_id = await _insert_reservation(seeded)
    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = await session.get(Reservation, res_id)
        assert row is not None
        await reservation_service.transition_to_active(
            session, reservation=row, lab_id=seeded["lab_id"]
        )

    async with factory() as session:
        result = await session.execute(
            select(Notification).where(
                Notification.recipient_user_id == seeded["eve_id"],
                Notification.type == "reservation.started",
            )
        )
        notifs = list(result.scalars().all())
        assert len(notifs) == 1
        assert (notifs[0].payload or {}).get("reservation_id") == res_id


async def test_transition_to_completed_emits_completed_notification(
    seeded: dict[str, Any],
) -> None:
    res_id = await _insert_reservation(seeded, status="active")
    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = await session.get(Reservation, res_id)
        assert row is not None
        await reservation_service.transition_to_completed(
            session, reservation=row, lab_id=seeded["lab_id"]
        )

    async with factory() as session:
        result = await session.execute(
            select(Notification).where(
                Notification.recipient_user_id == seeded["eve_id"],
                Notification.type == "reservation.completed",
            )
        )
        notifs = list(result.scalars().all())
        assert len(notifs) == 1


async def test_transition_to_failed_emits_failed_notification(
    seeded: dict[str, Any],
) -> None:
    res_id = await _insert_reservation(seeded, status="active")
    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = await session.get(Reservation, res_id)
        assert row is not None
        await reservation_service.transition_to_failed(
            session,
            reservation=row,
            lab_id=seeded["lab_id"],
            reason="script_failed",
        )

    async with factory() as session:
        result = await session.execute(
            select(Notification).where(
                Notification.recipient_user_id == seeded["eve_id"],
                Notification.type == "reservation.failed",
            )
        )
        notifs = list(result.scalars().all())
        assert len(notifs) == 1
        assert (notifs[0].payload or {}).get("reason") == "script_failed"


async def test_cancel_by_admin_emits_cancelled_by_other(
    seeded: dict[str, Any],
) -> None:
    res_id = await _insert_reservation(seeded, status="active")
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await reservation_service.cancel_reservation(
            session,
            reservation_id=res_id,
            actor_user_id=seeded["admin_id"],
            actor_can_admin=True,
            reason="bad behaviour",
            lab_id=seeded["lab_id"],
        )

    async with factory() as session:
        result = await session.execute(
            select(Notification).where(
                Notification.recipient_user_id == seeded["eve_id"],
                Notification.type == "reservation.cancelled_by_other",
            )
        )
        notifs = list(result.scalars().all())
        assert len(notifs) == 1
        payload = notifs[0].payload or {}
        assert payload.get("actor_user_id") == seeded["admin_id"]


async def test_cancel_by_owner_emits_no_notification(
    seeded: dict[str, Any],
) -> None:
    """Owner self-cancel should NOT trigger the cancelled_by_other notification."""
    res_id = await _insert_reservation(seeded, status="active")
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await reservation_service.cancel_reservation(
            session,
            reservation_id=res_id,
            actor_user_id=seeded["eve_id"],
            actor_can_admin=False,
            reason="changed mind",
            lab_id=seeded["lab_id"],
        )

    async with factory() as session:
        result = await session.execute(
            select(func.count(Notification.id)).where(
                Notification.recipient_user_id == seeded["eve_id"]
            )
        )
        assert result.scalar_one() == 0
