"""Phase 5 reservation_service integration tests.

Drives the service directly through a real session — no HTTP — so the
assertions stay close to the invariants in docs/02-data-model.md §5.13
+ docs/05-api-design.md §3.12-§3.13:

- 4 conflict types raise the right typed exception
  (exclusive / mix_exclusive_shared / memory_exceeded / compute_exceeded)
- ``account_link_id`` source='admin_declared' is refused
  (LinkNotVerifiedError) — Phase 4 invariant #5 carry-through
- ``max_reservation_hours`` is hard 422 (ReservationTooLongError) — Q1
- preview_conflicts collects rather than raises, surfaces time_too_long
- cancel + cancel_group + permission gate
- group_id is shared across batch + script obeys ``share_script``
- modify_reservation re-runs the conflict matrix excluding own row
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
    PhysicalAccount,
    Server,
    User,
)
from corelab_backend.security import hash_password
from corelab_backend.services import account_link_service as als
from corelab_backend.services import reservation_service as rs
from httpx import AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def seeded_world(integration_client: AsyncClient) -> dict[str, Any]:
    """Seed a Phase 5 world: lab + 2 users + server (max=24h) + 2 GPUs + 2 PA + 2 links."""
    del integration_client
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="Phase5 Lab", slug="phase5")
        session.add(lab)
        await session.flush()
        alice = User(
            lab_id=lab.id,
            username="alice",
            email="alice@phase5.test",
            display_name="Alice",
            password_hash=hash_password("AlicePass!2024"),
            role="lab_admin",
        )
        bob = User(
            lab_id=lab.id,
            username="bob",
            email="bob@phase5.test",
            display_name="Bob",
            password_hash=hash_password("BobPass!2024"),
            role="user",
        )
        session.add_all([alice, bob])
        await session.flush()
        server = Server(
            lab_id=lab.id,
            hostname="gpu-01.phase5",
            display_name="GPU 01",
            status="online",
            created_by_user_id=alice.id,
            max_reservation_hours=24,
        )
        session.add(server)
        await session.flush()
        gpu0 = Gpu(server_id=server.id, gpu_index=0, model="RTX 4090", memory_total_mb=24576)
        gpu1 = Gpu(server_id=server.id, gpu_index=1, model="RTX 4090", memory_total_mb=24576)
        session.add_all([gpu0, gpu1])
        pa_alice = PhysicalAccount(
            server_id=server.id,
            linux_username="alice_lab",
            uid=1001,
            source="admin_manual_register",
            created_by_user_id=alice.id,
        )
        pa_shared = PhysicalAccount(
            server_id=server.id,
            linux_username="yang_lab",
            uid=1002,
            source="admin_manual_register",
            created_by_user_id=alice.id,
        )
        session.add_all([pa_alice, pa_shared])
        await session.flush()
        # Verified SSH link for Bob → yang_lab (can act-as)
        await als.establish_via_ssh_challenge(
            session,
            user_id=bob.id,
            physical_account_id=pa_shared.id,
            challenge_id="chal-bob",
            signer_fingerprint="SHA256:bob",
            lab_id=lab.id,
            server_id=server.id,
        )
        # admin_declared link for Alice → yang_lab (cannot act-as)
        await als.admin_declare_link(
            session,
            physical_account_id=pa_shared.id,
            owner_user_id=alice.id,
            reason="Alice was the original owner before CoreLab existed.",
            declared_by_user_id=alice.id,
            lab_id=lab.id,
            server_id=server.id,
        )

    async with factory() as session:
        links_by_user: dict[int, dict[str, int]] = {}
        rows = (await session.execute(select(AccountLink))).scalars().all()
        for r in rows:
            links_by_user.setdefault(r.user_id, {})[r.source] = r.id

    return {
        "lab_id": lab.id,
        "alice_id": alice.id,
        "bob_id": bob.id,
        "server_id": server.id,
        "gpu0_id": gpu0.id,
        "gpu1_id": gpu1.id,
        "pa_alice_id": pa_alice.id,
        "pa_shared_id": pa_shared.id,
        "bob_link_id": links_by_user[bob.id]["ssh_challenge"],
        "alice_admin_link_id": links_by_user[alice.id]["admin_declared"],
    }


def _draft(
    *,
    server_id: int,
    gpu_id: int,
    account_link_id: int,
    start_offset_min: int,
    duration_min: int,
    gpu_memory_mb: int | None = None,
    gpu_compute_share_pct: int | None = None,
) -> rs.ItemDraft:
    base = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=2)
    start = base + timedelta(minutes=start_offset_min)
    end = start + timedelta(minutes=duration_min)
    return rs.ItemDraft(
        server_id=server_id,
        gpu_id=gpu_id,
        start_at=start,
        end_at=end,
        account_link_id=account_link_id,
        gpu_memory_mb=gpu_memory_mb,
        gpu_compute_share_pct=gpu_compute_share_pct,
    )


async def test_exclusive_conflict_raises(seeded_world: dict[str, Any]) -> None:
    factory = get_session_factory()
    item = _draft(
        server_id=seeded_world["server_id"],
        gpu_id=seeded_world["gpu0_id"],
        account_link_id=seeded_world["bob_link_id"],
        start_offset_min=0,
        duration_min=60,
    )
    async with factory() as session, session.begin():
        await rs.create_reservation_batch(
            session,
            items=[item],
            user_id=seeded_world["bob_id"],
            lab_id=seeded_world["lab_id"],
            script=None,
            script_scheduled_start_at=None,
            script_max_runtime_seconds=None,
            share_script=True,
            now=datetime.now(UTC),
        )
    overlap = _draft(
        server_id=seeded_world["server_id"],
        gpu_id=seeded_world["gpu0_id"],
        account_link_id=seeded_world["bob_link_id"],
        start_offset_min=30,
        duration_min=30,
    )
    async with factory() as session, session.begin():
        with pytest.raises(rs.ReservationOverlapError):
            await rs.create_reservation_batch(
                session,
                items=[overlap],
                user_id=seeded_world["bob_id"],
                lab_id=seeded_world["lab_id"],
                script=None,
                script_scheduled_start_at=None,
                script_max_runtime_seconds=None,
                share_script=True,
                now=datetime.now(UTC),
            )


async def test_admin_declared_link_refused(seeded_world: dict[str, Any]) -> None:
    """Q1/Phase 4 invariant #5: admin_declared can't act-as → 422 LinkNotVerified."""
    factory = get_session_factory()
    item = _draft(
        server_id=seeded_world["server_id"],
        gpu_id=seeded_world["gpu0_id"],
        account_link_id=seeded_world["alice_admin_link_id"],
        start_offset_min=120,
        duration_min=60,
    )
    async with factory() as session, session.begin():
        with pytest.raises(rs.LinkNotVerifiedError):
            await rs.create_reservation_batch(
                session,
                items=[item],
                user_id=seeded_world["alice_id"],
                lab_id=seeded_world["lab_id"],
                script=None,
                script_scheduled_start_at=None,
                script_max_runtime_seconds=None,
                share_script=True,
                now=datetime.now(UTC),
            )


async def test_max_hours_hard_422(seeded_world: dict[str, Any]) -> None:
    """Q1: server.max_reservation_hours=24h is hard, not soft."""
    factory = get_session_factory()
    item = _draft(
        server_id=seeded_world["server_id"],
        gpu_id=seeded_world["gpu0_id"],
        account_link_id=seeded_world["bob_link_id"],
        start_offset_min=0,
        duration_min=25 * 60,  # 25h > 24h limit
    )
    async with factory() as session, session.begin():
        with pytest.raises(rs.ReservationTooLongError) as exc_info:
            await rs.create_reservation_batch(
                session,
                items=[item],
                user_id=seeded_world["bob_id"],
                lab_id=seeded_world["lab_id"],
                script=None,
                script_scheduled_start_at=None,
                script_max_runtime_seconds=None,
                share_script=True,
                now=datetime.now(UTC),
            )
        assert exc_info.value.max_hours == 24


async def test_memory_exceeded(seeded_world: dict[str, Any]) -> None:
    """Q3 sibling — shared memory stack > total raises memory_exceeded."""
    factory = get_session_factory()
    # First reservation: 18 GB shared on gpu1
    item1 = _draft(
        server_id=seeded_world["server_id"],
        gpu_id=seeded_world["gpu1_id"],
        account_link_id=seeded_world["bob_link_id"],
        start_offset_min=200,
        duration_min=60,
        gpu_memory_mb=18432,
    )
    async with factory() as session, session.begin():
        await rs.create_reservation_batch(
            session,
            items=[item1],
            user_id=seeded_world["bob_id"],
            lab_id=seeded_world["lab_id"],
            script=None,
            script_scheduled_start_at=None,
            script_max_runtime_seconds=None,
            share_script=True,
            now=datetime.now(UTC),
        )
    # Second reservation: 12 GB shared on gpu1 same window → 30720 MB > 24576 MB
    item2 = _draft(
        server_id=seeded_world["server_id"],
        gpu_id=seeded_world["gpu1_id"],
        account_link_id=seeded_world["bob_link_id"],
        start_offset_min=210,
        duration_min=30,
        gpu_memory_mb=12288,
    )
    async with factory() as session, session.begin():
        with pytest.raises(rs.ReservationMemoryExceededError):
            await rs.create_reservation_batch(
                session,
                items=[item2],
                user_id=seeded_world["bob_id"],
                lab_id=seeded_world["lab_id"],
                script=None,
                script_scheduled_start_at=None,
                script_max_runtime_seconds=None,
                share_script=True,
                now=datetime.now(UTC),
            )


async def test_compute_exceeded(seeded_world: dict[str, Any]) -> None:
    """Q3 — shared compute_share_pct stack > 100 raises compute_exceeded."""
    factory = get_session_factory()
    item1 = _draft(
        server_id=seeded_world["server_id"],
        gpu_id=seeded_world["gpu1_id"],
        account_link_id=seeded_world["bob_link_id"],
        start_offset_min=400,
        duration_min=60,
        gpu_memory_mb=6000,
        gpu_compute_share_pct=70,
    )
    async with factory() as session, session.begin():
        await rs.create_reservation_batch(
            session,
            items=[item1],
            user_id=seeded_world["bob_id"],
            lab_id=seeded_world["lab_id"],
            script=None,
            script_scheduled_start_at=None,
            script_max_runtime_seconds=None,
            share_script=True,
            now=datetime.now(UTC),
        )
    item2 = _draft(
        server_id=seeded_world["server_id"],
        gpu_id=seeded_world["gpu1_id"],
        account_link_id=seeded_world["bob_link_id"],
        start_offset_min=410,
        duration_min=30,
        gpu_memory_mb=4000,
        gpu_compute_share_pct=40,  # 70 + 40 = 110 > 100
    )
    async with factory() as session, session.begin():
        with pytest.raises(rs.ReservationComputeExceededError) as exc_info:
            await rs.create_reservation_batch(
                session,
                items=[item2],
                user_id=seeded_world["bob_id"],
                lab_id=seeded_world["lab_id"],
                script=None,
                script_scheduled_start_at=None,
                script_max_runtime_seconds=None,
                share_script=True,
                now=datetime.now(UTC),
            )
        assert exc_info.value.exceeds_by_pct == 10


async def test_mix_exclusive_shared(seeded_world: dict[str, Any]) -> None:
    """Window holds shared rows + we ask for exclusive → 409."""
    factory = get_session_factory()
    item1 = _draft(
        server_id=seeded_world["server_id"],
        gpu_id=seeded_world["gpu0_id"],
        account_link_id=seeded_world["bob_link_id"],
        start_offset_min=600,
        duration_min=60,
        gpu_memory_mb=8000,
    )
    async with factory() as session, session.begin():
        await rs.create_reservation_batch(
            session,
            items=[item1],
            user_id=seeded_world["bob_id"],
            lab_id=seeded_world["lab_id"],
            script=None,
            script_scheduled_start_at=None,
            script_max_runtime_seconds=None,
            share_script=True,
            now=datetime.now(UTC),
        )
    item2 = _draft(
        server_id=seeded_world["server_id"],
        gpu_id=seeded_world["gpu0_id"],
        account_link_id=seeded_world["bob_link_id"],
        start_offset_min=610,
        duration_min=30,
    )  # exclusive
    async with factory() as session, session.begin():
        with pytest.raises(rs.ReservationMixExclusiveSharedError):
            await rs.create_reservation_batch(
                session,
                items=[item2],
                user_id=seeded_world["bob_id"],
                lab_id=seeded_world["lab_id"],
                script=None,
                script_scheduled_start_at=None,
                script_max_runtime_seconds=None,
                share_script=True,
                now=datetime.now(UTC),
            )


async def test_group_id_shared_and_share_script(seeded_world: dict[str, Any]) -> None:
    """Batch shares one group_id; share_script=false leaves only first row scripted."""
    factory = get_session_factory()
    items = [
        _draft(
            server_id=seeded_world["server_id"],
            gpu_id=seeded_world["gpu0_id"],
            account_link_id=seeded_world["bob_link_id"],
            start_offset_min=800,
            duration_min=60,
        ),
        _draft(
            server_id=seeded_world["server_id"],
            gpu_id=seeded_world["gpu1_id"],
            account_link_id=seeded_world["bob_link_id"],
            start_offset_min=800,
            duration_min=60,
        ),
    ]
    async with factory() as session, session.begin():
        _group_id, rows = await rs.create_reservation_batch(
            session,
            items=items,
            user_id=seeded_world["bob_id"],
            lab_id=seeded_world["lab_id"],
            script="echo hi",
            script_scheduled_start_at=None,
            script_max_runtime_seconds=None,
            share_script=False,
            now=datetime.now(UTC),
        )
        assert len({r.group_id for r in rows}) == 1
        assert rows[0].script == "echo hi"
        assert rows[1].script is None  # share_script=false


async def test_preview_collects_conflicts(seeded_world: dict[str, Any]) -> None:
    """preview_conflicts collects rather than raising; surfaces time_too_long."""
    factory = get_session_factory()
    # Pre-seed an exclusive reservation on gpu0
    pre = _draft(
        server_id=seeded_world["server_id"],
        gpu_id=seeded_world["gpu0_id"],
        account_link_id=seeded_world["bob_link_id"],
        start_offset_min=1000,
        duration_min=60,
    )
    async with factory() as session, session.begin():
        await rs.create_reservation_batch(
            session,
            items=[pre],
            user_id=seeded_world["bob_id"],
            lab_id=seeded_world["lab_id"],
            script=None,
            script_scheduled_start_at=None,
            script_max_runtime_seconds=None,
            share_script=True,
            now=datetime.now(UTC),
        )

    # 3 preview items: (1) conflicts with pre, (2) clean, (3) too long (25h)
    items = [
        _draft(
            server_id=seeded_world["server_id"],
            gpu_id=seeded_world["gpu0_id"],
            account_link_id=seeded_world["bob_link_id"],
            start_offset_min=1010,
            duration_min=30,
        ),
        _draft(
            server_id=seeded_world["server_id"],
            gpu_id=seeded_world["gpu1_id"],
            account_link_id=seeded_world["bob_link_id"],
            start_offset_min=1200,
            duration_min=30,
        ),
        _draft(
            server_id=seeded_world["server_id"],
            gpu_id=seeded_world["gpu1_id"],
            account_link_id=seeded_world["bob_link_id"],
            start_offset_min=2000,
            duration_min=25 * 60,
        ),
    ]
    async with factory() as session:
        result = await rs.preview_conflicts(
            session, items=items, user_id=seeded_world["bob_id"], now=datetime.now(UTC)
        )
    types = {c.input_index: c.type for c in result.conflicts}
    assert types.get(0) == "exclusive_conflict"
    assert 1 not in types  # clean
    assert types.get(2) == "time_too_long"
    assert len(result.time_limit_checks) == 3
    assert result.time_limit_checks[2]["would_exceed"] is True


async def test_cancel_owner_and_admin_paths(seeded_world: dict[str, Any]) -> None:
    """owner self-cancel ok; non-owner non-admin → 403; lab_admin can cancel."""
    factory = get_session_factory()
    item = _draft(
        server_id=seeded_world["server_id"],
        gpu_id=seeded_world["gpu0_id"],
        account_link_id=seeded_world["bob_link_id"],
        start_offset_min=3000,
        duration_min=60,
    )
    async with factory() as session, session.begin():
        _gid, rows = await rs.create_reservation_batch(
            session,
            items=[item],
            user_id=seeded_world["bob_id"],
            lab_id=seeded_world["lab_id"],
            script=None,
            script_scheduled_start_at=None,
            script_max_runtime_seconds=None,
            share_script=True,
            now=datetime.now(UTC),
        )
        rid = rows[0].id

    # Non-admin, non-owner can't cancel.
    async with factory() as session, session.begin():
        with pytest.raises(rs.CancelNotPermittedError):
            await rs.cancel_reservation(
                session,
                reservation_id=rid,
                actor_user_id=seeded_world["alice_id"],
                actor_can_admin=False,
                reason="snoop",
                lab_id=seeded_world["lab_id"],
            )

    # Admin can cancel.
    async with factory() as session, session.begin():
        cancelled = await rs.cancel_reservation(
            session,
            reservation_id=rid,
            actor_user_id=seeded_world["alice_id"],
            actor_can_admin=True,
            reason="admin-cancel",
            lab_id=seeded_world["lab_id"],
        )
        assert cancelled.status == "cancelled"
        assert cancelled.cancellation_reason == "admin-cancel"


async def test_cancel_group(seeded_world: dict[str, Any]) -> None:
    """cancel_group flips every active row in the group, audits once."""
    factory = get_session_factory()
    items = [
        _draft(
            server_id=seeded_world["server_id"],
            gpu_id=seeded_world["gpu0_id"],
            account_link_id=seeded_world["bob_link_id"],
            start_offset_min=4000,
            duration_min=60,
        ),
        _draft(
            server_id=seeded_world["server_id"],
            gpu_id=seeded_world["gpu1_id"],
            account_link_id=seeded_world["bob_link_id"],
            start_offset_min=4000,
            duration_min=60,
        ),
    ]
    async with factory() as session, session.begin():
        group_id, _rows = await rs.create_reservation_batch(
            session,
            items=items,
            user_id=seeded_world["bob_id"],
            lab_id=seeded_world["lab_id"],
            script=None,
            script_scheduled_start_at=None,
            script_max_runtime_seconds=None,
            share_script=True,
            now=datetime.now(UTC),
        )

    async with factory() as session, session.begin():
        cancelled = await rs.cancel_group(
            session,
            group_id=group_id,
            actor_user_id=seeded_world["bob_id"],
            actor_can_admin=False,
            reason="batch-cancel",
            lab_id=seeded_world["lab_id"],
        )
        assert len(cancelled) == 2
        assert all(r.status == "cancelled" for r in cancelled)


async def test_cancel_group_refuses_running_script(seeded_world: dict[str, Any]) -> None:
    """Batch cancel must not leave a Linux-side script running."""
    factory = get_session_factory()
    item = _draft(
        server_id=seeded_world["server_id"],
        gpu_id=seeded_world["gpu0_id"],
        account_link_id=seeded_world["bob_link_id"],
        start_offset_min=4100,
        duration_min=60,
    )
    async with factory() as session, session.begin():
        group_id, rows = await rs.create_reservation_batch(
            session,
            items=[item],
            user_id=seeded_world["bob_id"],
            lab_id=seeded_world["lab_id"],
            script="sleep 999",
            script_scheduled_start_at=None,
            script_max_runtime_seconds=None,
            share_script=True,
            now=datetime.now(UTC),
        )
        rows[0].status = rs.STATUS_ACTIVE
        rows[0].script_status = rs.SCRIPT_RUNNING

    async with factory() as session, session.begin():
        with pytest.raises(rs.GroupCancelRunningScriptError) as exc_info:
            await rs.cancel_group(
                session,
                group_id=group_id,
                actor_user_id=seeded_world["bob_id"],
                actor_can_admin=False,
                reason="batch-cancel",
                lab_id=seeded_world["lab_id"],
            )
        assert exc_info.value.running_reservation_ids == [rows[0].id]


async def test_script_too_large(seeded_world: dict[str, Any]) -> None:
    factory = get_session_factory()
    big_script = "a" * 5000
    item = _draft(
        server_id=seeded_world["server_id"],
        gpu_id=seeded_world["gpu0_id"],
        account_link_id=seeded_world["bob_link_id"],
        start_offset_min=5000,
        duration_min=30,
    )
    async with factory() as session, session.begin():
        with pytest.raises(rs.ScriptTooLargeError):
            await rs.create_reservation_batch(
                session,
                items=[item],
                user_id=seeded_world["bob_id"],
                lab_id=seeded_world["lab_id"],
                script=big_script,
                script_scheduled_start_at=None,
                script_max_runtime_seconds=None,
                share_script=True,
                now=datetime.now(UTC),
            )


async def test_modify_add_script_defaults_trigger_to_start(seeded_world: dict[str, Any]) -> None:
    """PATCH with a script but blank trigger must match create semantics."""
    factory = get_session_factory()
    item = _draft(
        server_id=seeded_world["server_id"],
        gpu_id=seeded_world["gpu0_id"],
        account_link_id=seeded_world["bob_link_id"],
        start_offset_min=6000,
        duration_min=30,
    )
    async with factory() as session, session.begin():
        _gid, rows = await rs.create_reservation_batch(
            session,
            items=[item],
            user_id=seeded_world["bob_id"],
            lab_id=seeded_world["lab_id"],
            script=None,
            script_scheduled_start_at=None,
            script_max_runtime_seconds=None,
            share_script=True,
            now=datetime.now(UTC),
        )
        rid = rows[0].id

    async with factory() as session, session.begin():
        row = await rs.modify_reservation(
            session,
            reservation_id=rid,
            actor_user_id=seeded_world["bob_id"],
            new_start_at=None,
            new_end_at=None,
            new_script="echo edited",
            new_script_scheduled_start_at=None,
            new_script_max_runtime_seconds=None,
            lab_id=seeded_world["lab_id"],
            now=datetime.now(UTC),
        )
        assert row.script == "echo edited"
        assert row.script_scheduled_start_at == row.start_at
