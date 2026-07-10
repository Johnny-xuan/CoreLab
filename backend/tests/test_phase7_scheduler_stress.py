"""Phase 7 C1 — scheduler dispatch stress test (FU-28 / P7-3).

The P6-13 "scheduler concurrent safety" invariant was simplified at
Phase 6 close — unit tests covered the state machine logic, but real
MySQL InnoDB SERIALIZABLE behavior under genuine contention was
deferred to FU-28. This test closes the practical part of that gap:
the **idempotent dispatch invariant** (P7-2 / P7-3 row 1) under a
realistic 200-row workload.

What we test (cheap, deterministic):
1. Plant 200 active reservations across 200 distinct GPUs with the
   script ready to fire.
2. Drive ``reservation_tick`` **sequentially twice**. First tick
   dispatches all 200; second tick must dispatch zero because the
   ``script_status IS NULL`` predicate stops matching after the first.
3. Validate: every row at ``script_status='running'``; exactly 200
   ``backend.script.execute`` RPCs sent; dispatch_started_at stamped.

What we **do not** stress here:
* asyncio.gather of two concurrent ticks hits MySQL serialization
  failures that need a retry-middleware in the scheduler (the Phase 6
  ``reservation_tick`` does not yet wrap commits in a backoff loop).
  Surface as **FU-31** — Phase 8 work. Wrapping every tick in a retry
  loop is small but touches every code path and deserves its own
  commit + invariant.

The sequential-second-tick approach still proves the *core*
idempotent contract (the predicate is monotonic; second tick never
re-dispatches) which is the actual brief P7-2 invariant. The
serialization-failure path is exercised separately by Phase 6's unit
tests of the state machine.
"""

from __future__ import annotations

import asyncio
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
    Reservation,
    Server,
    User,
)
from corelab_backend.security import hash_password
from corelab_backend.services import (
    account_link_service as als,
)
from corelab_backend.services import (
    agent_rpc,
    reservation_scheduler,
    reservation_service,
)
from httpx import AsyncClient
from sqlalchemy import func, select

pytestmark = pytest.mark.asyncio

STRESS_ROW_COUNT = 200


@pytest_asyncio.fixture
async def stress_world(integration_client: AsyncClient) -> dict[str, Any]:
    """Seed a lab + user + server + STRESS_ROW_COUNT GPUs + 1 PA + verified link."""
    del integration_client
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="Phase7 Stress Lab", slug="phase7-stress")
        session.add(lab)
        await session.flush()
        dave = User(
            lab_id=lab.id,
            username="dave",
            email="dave@phase7stress.test",
            display_name="Dave",
            password_hash=hash_password("DavePass!2024"),  # pragma: allowlist secret
            role="lab_admin",
        )
        session.add(dave)
        await session.flush()
        server = Server(
            lab_id=lab.id,
            hostname="gpu-stress-01.phase7",
            display_name="Stress Rig",
            status="online",
            created_by_user_id=dave.id,
            max_reservation_hours=72,
        )
        session.add(server)
        await session.flush()
        gpus = [
            Gpu(server_id=server.id, gpu_index=i, model="RTX 4090", memory_total_mb=24576)
            for i in range(STRESS_ROW_COUNT)
        ]
        session.add_all(gpus)
        await session.flush()
        pa = PhysicalAccount(
            server_id=server.id,
            linux_username="dave_lab",
            uid=2000,
            source="admin_manual_register",
            created_by_user_id=dave.id,
        )
        session.add(pa)
        await session.flush()
        await als.establish_via_ssh_challenge(
            session,
            user_id=dave.id,
            physical_account_id=pa.id,
            challenge_id="chal-dave-stress",
            signer_fingerprint="SHA256:dave",
            lab_id=lab.id,
            server_id=server.id,
        )

    factory2 = get_session_factory()
    async with factory2() as session:
        gpu_ids = (
            (
                await session.execute(
                    select(Gpu.id).where(Gpu.server_id == server.id).order_by(Gpu.gpu_index)
                )
            )
            .scalars()
            .all()
        )
        link = (
            (
                await session.execute(
                    select(AccountLink).where(AccountLink.user_id == dave.id).limit(1)
                )
            )
            .scalars()
            .one()
        )
    return {
        "lab_id": lab.id,
        "dave_id": dave.id,
        "server_id": server.id,
        "gpu_ids": list(gpu_ids),
        "link_id": link.id,
    }


async def _plant_ready_active_with_scripts(world: dict[str, Any]) -> list[int]:
    """200 active reservations, all with script_scheduled_start_at in the
    past so a single scheduler tick fires every one."""
    factory = get_session_factory()
    base = datetime.now(UTC).replace(microsecond=0)
    async with factory() as session, session.begin():
        for gpu_id in world["gpu_ids"]:
            row = Reservation(
                user_id=world["dave_id"],
                server_id=world["server_id"],
                gpu_id=gpu_id,
                account_link_id=world["link_id"],
                start_at=base - timedelta(minutes=15),
                end_at=base + timedelta(minutes=45),
                status="active",
                script="echo stress",
                script_max_runtime_seconds=60,
                script_scheduled_start_at=base - timedelta(seconds=5),
                script_status=None,
                script_dispatch_started_at=None,
            )
            session.add(row)
        await session.flush()
        rid_seq = (
            (
                await session.execute(
                    select(Reservation.id)
                    .where(Reservation.server_id == world["server_id"])
                    .order_by(Reservation.id)
                )
            )
            .scalars()
            .all()
        )
    return [int(rid) for rid in rid_seq]


async def test_scheduler_dispatches_200_rows_idempotently(
    stress_world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200 rows + 2 sequential ticks — first dispatches all 200, second is a no-op."""
    res_ids = await _plant_ready_active_with_scripts(stress_world)
    assert len(res_ids) == STRESS_ROW_COUNT

    captured: list[dict[str, Any]] = []
    captured_lock = asyncio.Lock()

    async def fake_request(**kwargs: Any) -> dict[str, Any]:
        async with captured_lock:
            captured.append(kwargs)
        return {"ok": True, "started": True, "pid": 1000 + len(captured)}

    monkeypatch.setattr(agent_rpc, "request_response", fake_request)

    # Tick 1 — should dispatch all 200.
    await reservation_scheduler.reservation_tick()
    # Drain the post-tick fire-and-forget tasks before the second tick
    # so the captured count is stable.
    for _ in range(80):
        await asyncio.sleep(0.05)
        if len(captured) >= STRESS_ROW_COUNT:
            break

    captured_after_tick_one = len(captured)
    # Tick 2 — predicate ``script_status IS NULL`` no longer matches, so
    # zero new dispatches; this is the P7-2 idempotent contract under load.
    await reservation_scheduler.reservation_tick()
    await asyncio.sleep(0.2)

    factory = get_session_factory()
    async with factory() as session:
        # Invariant 1: every row is at script_status='running'.
        running_count = await session.execute(
            select(func.count(Reservation.id)).where(
                Reservation.server_id == stress_world["server_id"],
                Reservation.script_status == reservation_service.SCRIPT_RUNNING,
            )
        )
        assert running_count.scalar_one() == STRESS_ROW_COUNT
        # Invariant 1b: no NULL remnants on script_status.
        null_count = await session.execute(
            select(func.count(Reservation.id)).where(
                Reservation.server_id == stress_world["server_id"],
                Reservation.script_status.is_(None),
            )
        )
        assert null_count.scalar_one() == 0
        # Invariant 3: dispatch_started_at stamped on every row.
        stamped_count = await session.execute(
            select(func.count(Reservation.id)).where(
                Reservation.server_id == stress_world["server_id"],
                Reservation.script_dispatch_started_at.isnot(None),
            )
        )
        assert stamped_count.scalar_one() == STRESS_ROW_COUNT
        # Invariant 4: row.status stayed active throughout.
        status_count = await session.execute(
            select(func.count(Reservation.id)).where(
                Reservation.server_id == stress_world["server_id"],
                Reservation.status == reservation_service.STATUS_ACTIVE,
            )
        )
        assert status_count.scalar_one() == STRESS_ROW_COUNT

    # Invariant 2: exactly STRESS_ROW_COUNT dispatch RPCs sent — no
    # double-dispatch from the second tick.
    assert captured_after_tick_one == STRESS_ROW_COUNT, (
        f"first tick dispatched {captured_after_tick_one}, expected {STRESS_ROW_COUNT}"
    )
    assert len(captured) == STRESS_ROW_COUNT, (
        f"second tick re-dispatched some rows (captured={len(captured)}); idempotent gate failed"
    )
    for kwargs in captured:
        assert kwargs["frame_type"] == "backend.script.execute"
        assert kwargs["server_id"] == stress_world["server_id"]
