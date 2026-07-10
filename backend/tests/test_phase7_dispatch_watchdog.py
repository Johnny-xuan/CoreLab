"""Phase 7 C0 (B 方案) — dispatch RPC + watchdog tests.

Each test plants a row in the state the scenario needs (status,
script_status, script_dispatch_started_at, audit pre-history) and
drives ``reservation_scheduler.reservation_tick`` or
``_retry_stuck_dispatches`` directly. The four required scenarios
(brief §11 gates 3 / 3b / 3c + 4) live here; the existing Phase 6
state-machine tests stay untouched.

Pattern note (Phase 6 carry):
* never assume ``lab_id=1`` — every seed creates a fresh lab and the
  ``integration_client`` fixture FK-reverse wipes between tests.
* monkeypatch ``agent_rpc.request_response`` rather than spinning a
  real WSS connection — the scheduler invariants don't care about
  the wire, only about the RPC contract.
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
    AuditLog,
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
    audit_service,
    reservation_scheduler,
    reservation_service,
)
from httpx import AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def seeded(integration_client: AsyncClient) -> dict[str, Any]:
    """Minimal Phase 7 world: lab + user + server + GPU + PA + verified link."""
    del integration_client
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="Phase7 Lab", slug="phase7")
        session.add(lab)
        await session.flush()
        carol = User(
            lab_id=lab.id,
            username="carol",
            email="carol@phase7.test",
            display_name="Carol",
            password_hash=hash_password("CarolPass!2024"),  # pragma: allowlist secret
            role="lab_admin",
        )
        session.add(carol)
        await session.flush()
        server = Server(
            lab_id=lab.id,
            hostname="gpu-07.phase7",
            display_name="GPU 07",
            status="online",
            created_by_user_id=carol.id,
            max_reservation_hours=24,
        )
        session.add(server)
        await session.flush()
        gpu = Gpu(server_id=server.id, gpu_index=0, model="RTX 4090", memory_total_mb=24576)
        session.add(gpu)
        await session.flush()
        pa = PhysicalAccount(
            server_id=server.id,
            linux_username="carol_lab",
            uid=1007,
            source="admin_manual_register",
            created_by_user_id=carol.id,
        )
        session.add(pa)
        await session.flush()
        await als.establish_via_ssh_challenge(
            session,
            user_id=carol.id,
            physical_account_id=pa.id,
            challenge_id="chal-carol-p7",
            signer_fingerprint="SHA256:carol",
            lab_id=lab.id,
            server_id=server.id,
        )

    factory2 = get_session_factory()
    async with factory2() as session:
        link = (
            (
                await session.execute(
                    select(AccountLink).where(AccountLink.user_id == carol.id).limit(1)
                )
            )
            .scalars()
            .one()
        )

    return {
        "lab_id": lab.id,
        "carol_id": carol.id,
        "server_id": server.id,
        "gpu_id": gpu.id,
        "pa_id": pa.id,
        "link_id": link.id,
    }


async def _insert_active_with_script(
    seeded: dict[str, Any],
    *,
    script_scheduled_offset_min: int = -1,
    script_status: str | None = None,
    dispatch_started_at: datetime | None = None,
) -> int:
    """Plant an active reservation carrying a script. Returns reservation id.

    Bypasses the writer service so the test can plant any combination
    of dispatch markers it needs.
    """
    factory = get_session_factory()
    base = datetime.now(UTC).replace(microsecond=0)
    async with factory() as session, session.begin():
        row = Reservation(
            user_id=seeded["carol_id"],
            server_id=seeded["server_id"],
            gpu_id=seeded["gpu_id"],
            account_link_id=seeded["link_id"],
            start_at=base - timedelta(minutes=30),
            end_at=base + timedelta(minutes=30),
            status="active",
            script="echo hi",
            script_max_runtime_seconds=60,
            script_scheduled_start_at=base + timedelta(minutes=script_scheduled_offset_min),
            script_status=script_status,
            script_dispatch_started_at=dispatch_started_at,
        )
        session.add(row)
        await session.flush()
        return row.id


async def _write_attempt_failed(*, reservation_id: int, server_id: int, lab_id: int) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await audit_service.write(
            session,
            action="reservation.script.dispatch_attempt_failed",
            actor_user_id=None,
            lab_id=lab_id,
            target_type="reservation",
            target_id=reservation_id,
            target_lab_id=lab_id,
            target_server_id=server_id,
            payload={"reason": "test-seed"},
        )


# ─── Catch #1 gate 3: dispatch RPC failure keeps row running ──────────


async def test_dispatch_failed_keeps_running(
    seeded: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RPC throws AgentOfflineError → script_status stays running,
    dispatch_started_at stays set, attempt_failed audit row appears."""
    res_id = await _insert_active_with_script(seeded, script_scheduled_offset_min=-1)

    captured: list[dict[str, Any]] = []

    async def fake_request(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        raise agent_rpc.AgentOfflineError("no live agent connection")

    monkeypatch.setattr(agent_rpc, "request_response", fake_request)

    await reservation_scheduler.reservation_tick()
    # Let the post-tick fire-and-forget _dispatch_one finish.
    await asyncio.sleep(0.1)

    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(Reservation, res_id)
        assert row is not None
        assert row.script_status == reservation_service.SCRIPT_RUNNING, (
            "B 方案 — status must not roll back on RPC failure"
        )
        assert row.script_dispatch_started_at is not None, (
            "dispatch_started_at must stay set so watchdog can find it"
        )
        attempt_failed = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "reservation.script.dispatch_attempt_failed",
                        AuditLog.target_id == res_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(attempt_failed) == 1
        assert attempt_failed[0].payload is not None
        assert "AgentOfflineError" in attempt_failed[0].payload.get("error_class", "")
        dispatched_audit = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "reservation.script.dispatched",
                        AuditLog.target_id == res_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(dispatched_audit) == 1, (
            "dispatched audit must be written by mark_script_dispatched"
        )

    assert captured, "agent_rpc.request_response should have been called once"
    assert captured[0]["frame_type"] == "backend.script.execute"


async def test_dispatch_rejected_response_records_attempt_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent execute ack with ok=false is still a failed dispatch attempt.

    Without this, USER_NOT_FOUND / CAPABILITY_DISABLED leaves the row
    ``running`` forever because the watchdog only counts audit rows.
    """
    recorded: list[dict[str, Any]] = []

    async def fake_request(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["frame_type"] == "backend.script.execute"
        return {"ok": False, "started": False, "error": "USER_NOT_FOUND"}

    async def fake_record(**kwargs: Any) -> None:
        recorded.append(kwargs)

    monkeypatch.setattr(agent_rpc, "request_response", fake_request)
    monkeypatch.setattr(reservation_scheduler, "_record_dispatch_attempt_failed", fake_record)

    await reservation_scheduler._dispatch_one(
        reservation_id=123,
        server_id=456,
        payload={"reservation_id": 123, "script": "echo hi"},
    )

    assert recorded == [
        {
            "reservation_id": 123,
            "server_id": 456,
            "error_class": "AgentRejectedScriptExecution",
            "error_message": "USER_NOT_FOUND",
        }
    ]


# ─── Catch #1 gate 3b: watchdog retries stuck rows ────────────────────


async def test_watchdog_retries_stuck(
    seeded: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row stuck on running with dispatch_started_at > 60s old AND
    no lifecycle.started audit → watchdog re-fires _dispatch_one and
    bumps the timer (attempts < 3, so no transition_to_failed)."""
    base = datetime.now(UTC).replace(microsecond=0)
    res_id = await _insert_active_with_script(
        seeded,
        script_scheduled_offset_min=-5,
        script_status=reservation_service.SCRIPT_RUNNING,
        dispatch_started_at=base - timedelta(seconds=90),
    )
    # Pre-seed 1 attempt_failed (initial dispatch already failed once)
    # so attempts < WATCHDOG_MAX_DISPATCH_ATTEMPTS still holds.
    await _write_attempt_failed(
        reservation_id=res_id,
        server_id=seeded["server_id"],
        lab_id=seeded["lab_id"],
    )

    captured: list[dict[str, Any]] = []

    async def fake_request(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"ok": True, "started": True, "pid": 4242}

    monkeypatch.setattr(agent_rpc, "request_response", fake_request)

    await reservation_scheduler.reservation_tick()
    await asyncio.sleep(0.1)

    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(Reservation, res_id)
        assert row is not None
        assert row.status == reservation_service.STATUS_ACTIVE, (
            "watchdog must not transition row before max attempts"
        )
        assert row.script_status == reservation_service.SCRIPT_RUNNING
        assert row.script_dispatch_started_at is not None
        assert row.script_dispatch_started_at > (base - timedelta(seconds=60)).replace(
            tzinfo=None
        ), "watchdog must bump dispatch_started_at to now-ish"
        retry_audit = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "reservation.script.dispatch_retry",
                        AuditLog.target_id == res_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(retry_audit) == 1
        assert retry_audit[0].payload is not None
        assert retry_audit[0].payload.get("attempt_number") == 2
        assert retry_audit[0].payload.get("trigger") == "watchdog"
    assert captured, "watchdog must have retried via _dispatch_one"


# ─── Catch #1 gate 3b ext: 3x failures → transition_to_failed ─────────


async def test_watchdog_3x_fails_marks_failed(
    seeded: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After WATCHDOG_MAX_DISPATCH_ATTEMPTS failures + stuck dispatch,
    watchdog calls transition_to_failed (running → failed legal edge)."""
    base = datetime.now(UTC).replace(microsecond=0)
    res_id = await _insert_active_with_script(
        seeded,
        script_scheduled_offset_min=-5,
        script_status=reservation_service.SCRIPT_RUNNING,
        dispatch_started_at=base - timedelta(seconds=90),
    )
    for _ in range(reservation_scheduler.WATCHDOG_MAX_DISPATCH_ATTEMPTS):
        await _write_attempt_failed(
            reservation_id=res_id,
            server_id=seeded["server_id"],
            lab_id=seeded["lab_id"],
        )

    captured: list[dict[str, Any]] = []

    async def fake_request(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"ok": True, "started": True}

    monkeypatch.setattr(agent_rpc, "request_response", fake_request)

    await reservation_scheduler.reservation_tick()
    await asyncio.sleep(0.05)

    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(Reservation, res_id)
        assert row is not None
        assert row.status == reservation_service.STATUS_FAILED, (
            "watchdog must transition_to_failed after MAX attempts"
        )
        assert row.cancellation_reason is not None
        assert "dispatch_" in row.cancellation_reason
        assert "failed" in row.cancellation_reason
        # _ALLOWED_TRANSITIONS allowed running -> {completed,failed,killed};
        # the row still carries script_status='running' (we did not
        # touch script_status, only reservation.status). That is fine
        # — the row is now terminal so the watchdog never re-fires.
        transition_audit = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "reservation.transition",
                        AuditLog.target_id == res_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert any(
            (a.payload or {}).get("to") == reservation_service.STATUS_FAILED
            for a in transition_audit
        ), "transition_to_failed audit row must have been written"
    assert captured == [], "watchdog must NOT retry after MAX attempts"


# ─── Catch #1 gate 3c: backend crash between commit & create_task ─────


async def test_backend_crash_between_commit_and_create_task_recovered(
    seeded: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate the crash case: row already at script_status='running'
    + dispatch_started_at set + no attempt_failed + no lifecycle.started.
    Watchdog 60s later must re-fire (attempts=0 < MAX so retry path)."""
    base = datetime.now(UTC).replace(microsecond=0)
    res_id = await _insert_active_with_script(
        seeded,
        script_scheduled_offset_min=-5,
        script_status=reservation_service.SCRIPT_RUNNING,
        dispatch_started_at=base - timedelta(seconds=120),
    )
    # No attempt_failed audit, no lifecycle.started audit — this is the
    # crash-between-commit-and-create_task signature.

    captured: list[dict[str, Any]] = []

    async def fake_request(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"ok": True, "started": True, "pid": 9999}

    monkeypatch.setattr(agent_rpc, "request_response", fake_request)

    await reservation_scheduler.reservation_tick()
    await asyncio.sleep(0.1)

    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(Reservation, res_id)
        assert row is not None
        assert row.status == reservation_service.STATUS_ACTIVE
        assert row.script_status == reservation_service.SCRIPT_RUNNING
        assert row.script_dispatch_started_at is not None
        # MySQL DATETIME(6) round-trips as a tz-naive Python datetime;
        # compare against the naive copy of ``base`` to avoid TypeError.
        assert row.script_dispatch_started_at > (base - timedelta(seconds=60)).replace(
            tzinfo=None
        ), "watchdog must bump timer on the recovery dispatch"
        retry_audit = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "reservation.script.dispatch_retry",
                        AuditLog.target_id == res_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(retry_audit) == 1
        assert (retry_audit[0].payload or {}).get("attempt_number") == 1
    assert captured, "watchdog must have re-fired _dispatch_one"
    assert captured[0]["frame_type"] == "backend.script.execute"


# ─── Watchdog self-heal: lifecycle.started audit present but column not cleared


async def test_watchdog_self_heals_orphan_dispatch_column(
    seeded: dict[str, Any],
) -> None:
    """If lifecycle.started audit is present but dispatch_started_at
    was never cleared (handler bug / race), watchdog just clears the
    column — no retry, no failure."""
    base = datetime.now(UTC).replace(microsecond=0)
    res_id = await _insert_active_with_script(
        seeded,
        script_scheduled_offset_min=-5,
        script_status=reservation_service.SCRIPT_RUNNING,
        dispatch_started_at=base - timedelta(seconds=120),
    )
    # Plant the lifecycle.started audit so the watchdog takes the self-heal branch.
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await audit_service.write(
            session,
            action="reservation.script.lifecycle.started",
            actor_user_id=None,
            lab_id=seeded["lab_id"],
            target_type="reservation",
            target_id=res_id,
            target_lab_id=seeded["lab_id"],
            target_server_id=seeded["server_id"],
            payload={"trigger": "agent.script.started"},
        )

    await reservation_scheduler.reservation_tick()

    factory2 = get_session_factory()
    async with factory2() as session:
        row = await session.get(Reservation, res_id)
        assert row is not None
        assert row.status == reservation_service.STATUS_ACTIVE
        assert row.script_status == reservation_service.SCRIPT_RUNNING
        assert row.script_dispatch_started_at is None, (
            "self-heal must clear dispatch column when lifecycle audit is present"
        )
