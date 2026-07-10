"""Phase 6 state machine + scheduler integration tests.

Exercises the transition helpers added in C1, the scheduler tick wired
in C2, and the lifecycle receive handlers from C4. Tests are written
in pairs (happy + illegal / symmetric) per the worker self-correction
carried over from Phase 5 ("涉及对称逻辑主动写正反测试").

Seeds a minimal world (lab + user + server + 1 GPU + PA + SSH-verified
link + 1 reservation) inline so each test stays self-contained. The
``integration_client`` fixture handles the FK-reverse wipe between
tests; no manual cleanup needed here.
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
    Reservation,
    Server,
    ServerAdminGrant,
    User,
)
from corelab_backend.security import hash_password
from corelab_backend.services import account_link_service as als
from corelab_backend.services import reservation_scheduler, reservation_service
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def seeded(integration_client: AsyncClient) -> dict[str, Any]:
    """Minimal Phase 6 world: lab + bob + server (max=24h) + 1 GPU + 1 PA + link."""
    del integration_client
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="Phase6 Lab", slug="phase6")
        session.add(lab)
        await session.flush()
        bob = User(
            lab_id=lab.id,
            username="bob",
            email="bob@phase6.test",
            display_name="Bob",
            password_hash=hash_password("BobPass!2024"),  # pragma: allowlist secret
            role="lab_admin",
        )
        session.add(bob)
        await session.flush()
        server = Server(
            lab_id=lab.id,
            hostname="gpu-01.phase6",
            display_name="GPU 01",
            status="online",
            created_by_user_id=bob.id,
            max_reservation_hours=24,
        )
        session.add(server)
        await session.flush()
        gpu = Gpu(server_id=server.id, gpu_index=0, model="RTX 4090", memory_total_mb=24576)
        session.add(gpu)
        await session.flush()
        pa = PhysicalAccount(
            server_id=server.id,
            linux_username="bob_lab",
            uid=1001,
            source="admin_manual_register",
            created_by_user_id=bob.id,
        )
        session.add(pa)
        await session.flush()
        await als.establish_via_ssh_challenge(
            session,
            user_id=bob.id,
            physical_account_id=pa.id,
            challenge_id="chal-bob-p6",
            signer_fingerprint="SHA256:bob",
            lab_id=lab.id,
            server_id=server.id,
        )

    factory2 = get_session_factory()
    async with factory2() as session:
        from sqlalchemy import select as _select

        link = (
            (
                await session.execute(
                    _select(AccountLink).where(AccountLink.user_id == bob.id).limit(1)
                )
            )
            .scalars()
            .one()
        )

    return {
        "lab_id": lab.id,
        "bob_id": bob.id,
        "server_id": server.id,
        "gpu_id": gpu.id,
        "pa_id": pa.id,
        "link_id": link.id,
    }


async def _insert_reservation(
    seeded: dict[str, Any],
    *,
    start_offset_min: int,
    duration_min: int,
    status: str = "scheduled",
    script: str | None = None,
    script_status: str | None = None,
) -> int:
    """Bypass the service so we can plant rows in any combination of
    status / script_status the test needs."""
    factory = get_session_factory()
    base = datetime.now(UTC).replace(microsecond=0)
    async with factory() as session, session.begin():
        row = Reservation(
            user_id=seeded["bob_id"],
            server_id=seeded["server_id"],
            gpu_id=seeded["gpu_id"],
            account_link_id=seeded["link_id"],
            start_at=base + timedelta(minutes=start_offset_min),
            end_at=base + timedelta(minutes=start_offset_min + duration_min),
            status=status,
            script=script,
            script_status=script_status,
        )
        session.add(row)
        await session.flush()
        return row.id


# ─── transition helpers ──────────────────────────────────────────────


async def test_transition_to_active_promotes_scheduled(seeded: dict[str, Any]) -> None:
    res_id = await _insert_reservation(seeded, start_offset_min=-10, duration_min=60)
    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = await session.get(Reservation, res_id)
        assert row is not None
        await reservation_service.transition_to_active(
            session, reservation=row, lab_id=seeded["lab_id"]
        )
        assert row.status == reservation_service.STATUS_ACTIVE


async def test_transition_to_active_from_terminal_raises(seeded: dict[str, Any]) -> None:
    """scheduled -> active is legal; completed -> active is not (P6-2 allowed map)."""
    res_id = await _insert_reservation(
        seeded, start_offset_min=-120, duration_min=60, status="completed"
    )
    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = await session.get(Reservation, res_id)
        assert row is not None
        with pytest.raises(reservation_service.ReservationIllegalTransitionError):
            await reservation_service.transition_to_active(
                session, reservation=row, lab_id=seeded["lab_id"]
            )


async def test_transition_to_completed_from_active(seeded: dict[str, Any]) -> None:
    res_id = await _insert_reservation(
        seeded, start_offset_min=-60, duration_min=30, status="active"
    )
    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = await session.get(Reservation, res_id)
        assert row is not None
        await reservation_service.transition_to_completed(
            session, reservation=row, lab_id=seeded["lab_id"]
        )
        assert row.status == reservation_service.STATUS_COMPLETED


async def test_transition_to_failed_records_reason(seeded: dict[str, Any]) -> None:
    res_id = await _insert_reservation(
        seeded, start_offset_min=-60, duration_min=30, status="active"
    )
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
        assert row.status == reservation_service.STATUS_FAILED
        assert row.cancellation_reason == "script_failed"


# ─── script_status transitions ───────────────────────────────────────


async def test_update_script_status_null_to_running(seeded: dict[str, Any]) -> None:
    res_id = await _insert_reservation(
        seeded, start_offset_min=-30, duration_min=60, status="active", script="echo hi"
    )
    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = await session.get(Reservation, res_id)
        assert row is not None
        await reservation_service.update_script_status(
            session,
            reservation=row,
            new_script_status=reservation_service.SCRIPT_RUNNING,
            lab_id=seeded["lab_id"],
            trigger="test",
        )
        assert row.script_status == reservation_service.SCRIPT_RUNNING


async def test_update_script_status_running_to_killed(seeded: dict[str, Any]) -> None:
    res_id = await _insert_reservation(
        seeded,
        start_offset_min=-30,
        duration_min=60,
        status="active",
        script="echo hi",
        script_status="running",
    )
    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = await session.get(Reservation, res_id)
        assert row is not None
        await reservation_service.update_script_status(
            session,
            reservation=row,
            new_script_status=reservation_service.SCRIPT_KILLED,
            lab_id=seeded["lab_id"],
            trigger="test",
        )
        assert row.script_status == reservation_service.SCRIPT_KILLED


async def test_update_script_status_terminal_to_anything_raises(seeded: dict[str, Any]) -> None:
    res_id = await _insert_reservation(
        seeded,
        start_offset_min=-30,
        duration_min=60,
        status="completed",
        script="echo hi",
        script_status="completed",
    )
    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = await session.get(Reservation, res_id)
        assert row is not None
        with pytest.raises(reservation_service.ReservationIllegalTransitionError):
            await reservation_service.update_script_status(
                session,
                reservation=row,
                new_script_status=reservation_service.SCRIPT_RUNNING,
                lab_id=seeded["lab_id"],
                trigger="test",
            )


# ─── scheduler tick ──────────────────────────────────────────────────


async def test_scheduler_tick_promotes_due_scheduled(seeded: dict[str, Any]) -> None:
    """scheduled with start_at past → active after one tick."""
    res_id = await _insert_reservation(seeded, start_offset_min=-10, duration_min=60)
    await reservation_scheduler.reservation_tick()
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(Reservation, res_id)
        assert row is not None
        assert row.status == reservation_service.STATUS_ACTIVE


async def test_scheduler_tick_completes_active_with_clean_script(
    seeded: dict[str, Any],
) -> None:
    """active + script_status=completed + end_at past → completed."""
    res_id = await _insert_reservation(
        seeded,
        start_offset_min=-120,
        duration_min=60,
        status="active",
        script="echo hi",
        script_status="completed",
    )
    await reservation_scheduler.reservation_tick()
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(Reservation, res_id)
        assert row is not None
        assert row.status == reservation_service.STATUS_COMPLETED


async def test_scheduler_tick_fails_active_with_killed_script(
    seeded: dict[str, Any],
) -> None:
    """active + script_status=killed + end_at past → failed (P6-7 + line 1148)."""
    res_id = await _insert_reservation(
        seeded,
        start_offset_min=-120,
        duration_min=60,
        status="active",
        script="echo hi",
        script_status="killed",
    )
    await reservation_scheduler.reservation_tick()
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(Reservation, res_id)
        assert row is not None
        assert row.status == reservation_service.STATUS_FAILED


async def test_scheduler_tick_skips_future_scheduled(seeded: dict[str, Any]) -> None:
    """scheduled with start_at in the future → untouched."""
    res_id = await _insert_reservation(seeded, start_offset_min=60, duration_min=30)
    await reservation_scheduler.reservation_tick()
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(Reservation, res_id)
        assert row is not None
        assert row.status == reservation_service.STATUS_SCHEDULED


async def test_scheduler_tick_skips_terminal_rows(seeded: dict[str, Any]) -> None:
    """cancelled rows are not retouched, even if end_at is past."""
    res_id = await _insert_reservation(
        seeded, start_offset_min=-120, duration_min=30, status="cancelled"
    )
    await reservation_scheduler.reservation_tick()
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(Reservation, res_id)
        assert row is not None
        assert row.status == reservation_service.STATUS_CANCELLED


# ─── lifecycle handlers ──────────────────────────────────────────────


async def test_on_script_started_moves_null_to_running(seeded: dict[str, Any]) -> None:
    from corelab_backend.services import script_lifecycle_service
    from corelab_protocol import ScriptStartedEvent

    res_id = await _insert_reservation(
        seeded, start_offset_min=-30, duration_min=60, status="active", script="echo hi"
    )
    payload = ScriptStartedEvent(
        reservation_id=res_id,
        pid=12345,
        started_at=datetime.now(UTC),
        log_path="/tmp/test.log",
    )
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await script_lifecycle_service.on_script_started(
            session, payload=payload, lab_id=seeded["lab_id"]
        )
    async with factory() as session:
        row = await session.get(Reservation, res_id)
        assert row is not None
        assert row.script_status == reservation_service.SCRIPT_RUNNING
        assert row.script_log_path == "/tmp/test.log"


async def test_on_script_started_running_clears_dispatch_and_stores_log_path(
    seeded: dict[str, Any],
) -> None:
    from corelab_backend.services import script_lifecycle_service
    from corelab_protocol import ScriptStartedEvent

    res_id = await _insert_reservation(
        seeded,
        start_offset_min=-30,
        duration_min=60,
        status="active",
        script="sleep 60",
        script_status="running",
    )
    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = await session.get(Reservation, res_id)
        assert row is not None
        row.script_dispatch_started_at = datetime.now(UTC) - timedelta(seconds=10)
    payload = ScriptStartedEvent(
        reservation_id=res_id,
        pid=12345,
        started_at=datetime.now(UTC),
        log_path="/tmp/running.log",
    )
    async with factory() as session, session.begin():
        await script_lifecycle_service.on_script_started(
            session, payload=payload, lab_id=seeded["lab_id"]
        )
    async with factory() as session:
        row = await session.get(Reservation, res_id)
        assert row is not None
        assert row.script_status == reservation_service.SCRIPT_RUNNING
        assert row.script_dispatch_started_at is None
        assert row.script_log_path == "/tmp/running.log"


async def test_on_script_output_chunk_appends_bounded_tail(seeded: dict[str, Any]) -> None:
    from corelab_backend.services import script_lifecycle_service
    from corelab_protocol import ScriptOutputChunkEvent

    res_id = await _insert_reservation(
        seeded,
        start_offset_min=-30,
        duration_min=60,
        status="active",
        script="echo hi",
        script_status="running",
    )
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await script_lifecycle_service.on_script_output_chunk(
            session,
            payload=ScriptOutputChunkEvent(
                reservation_id=res_id,
                stream="stdout",
                text="first\n",
                ts=datetime.now(UTC),
            ),
            lab_id=seeded["lab_id"],
        )
        row = await session.get(Reservation, res_id)
        assert row is not None
        await reservation_service.append_script_log_tail(
            session,
            reservation=row,
            text="abcdef",
            max_chars=10,
        )

    async with factory() as session:
        row = await session.get(Reservation, res_id)
        assert row is not None
        assert row.script_log_tail_text == "rst\nabcdef"
        assert len(row.script_log_tail_text) == 10
        assert row.script_log_tail_truncated == 1


async def test_reservation_script_log_api_owner_allowed_other_user_forbidden(
    seeded: dict[str, Any],
    integration_client: AsyncClient,
) -> None:
    res_id = await _insert_reservation(
        seeded,
        start_offset_min=-30,
        duration_min=60,
        status="active",
        script="echo hi",
        script_status="running",
    )
    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = await session.get(Reservation, res_id)
        assert row is not None
        row.script_log_tail_text = "hello from platform log\n"
        row.script_log_tail_truncated = 0
        row.script_output_size_bytes = 24
        row.script_log_path = "/tmp/corelab.log"
        eve = User(
            lab_id=seeded["lab_id"],
            username="eve",
            email="eve@phase6.test",
            display_name="Eve",
            password_hash=hash_password("EvePass!2024"),  # pragma: allowlist secret
            role="user",
        )
        server_admin = User(
            lab_id=seeded["lab_id"],
            username="sam",
            email="sam@phase6.test",
            display_name="Sam",
            password_hash=hash_password("SamPass!2024"),  # pragma: allowlist secret
            role="user",
        )
        session.add_all([eve, server_admin])
        await session.flush()
        session.add(
            ServerAdminGrant(
                user_id=server_admin.id,
                server_id=seeded["server_id"],
                granted_by_user_id=seeded["bob_id"],
            )
        )

    async def _login(username: str, password: str) -> dict[str, str]:
        resp = await integration_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert resp.status_code == 200
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    bob_headers = await _login("bob", "BobPass!2024")
    bob_resp = await integration_client.get(
        f"/api/v1/reservations/{res_id}/script-log",
        headers=bob_headers,
    )
    assert bob_resp.status_code == 200
    assert bob_resp.json()["text"] == "hello from platform log\n"
    assert bob_resp.json()["log_path"] == "/tmp/corelab.log"

    sam_headers = await _login("sam", "SamPass!2024")
    sam_resp = await integration_client.get(
        f"/api/v1/reservations/{res_id}/script-log",
        headers=sam_headers,
    )
    assert sam_resp.status_code == 200

    eve_headers = await _login("eve", "EvePass!2024")
    eve_resp = await integration_client.get(
        f"/api/v1/reservations/{res_id}/script-log",
        headers=eve_headers,
    )
    assert eve_resp.status_code == 403


async def test_on_script_finished_running_to_failed_on_nonzero_exit(
    seeded: dict[str, Any],
) -> None:
    from corelab_backend.services import script_lifecycle_service
    from corelab_protocol import ScriptFinishedEvent

    res_id = await _insert_reservation(
        seeded,
        start_offset_min=-30,
        duration_min=60,
        status="active",
        script="exit 1",
        script_status="running",
    )
    now = datetime.now(UTC)
    payload = ScriptFinishedEvent(
        reservation_id=res_id,
        exit_code=1,
        started_at=now - timedelta(seconds=5),
        finished_at=now,
        duration_seconds=5.0,
        output_size_bytes=128,
        log_path="/tmp/test.log",
        killed_by_corelab=False,
    )
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await script_lifecycle_service.on_script_finished(
            session, payload=payload, lab_id=seeded["lab_id"]
        )
    async with factory() as session:
        row = await session.get(Reservation, res_id)
        assert row is not None
        assert row.script_status == reservation_service.SCRIPT_FAILED
        assert row.script_exit_code == 1
        assert row.script_output_size_bytes == 128


async def test_on_script_finished_killed_signals_pending(seeded: dict[str, Any]) -> None:
    """SP-5 P6-14 — killed_by_corelab=true wakes a registered pending-kill event."""
    from corelab_backend.services import script_lifecycle_service
    from corelab_protocol import ScriptFinishedEvent

    res_id = await _insert_reservation(
        seeded,
        start_offset_min=-30,
        duration_min=60,
        status="active",
        script="sleep 999",
        script_status="running",
    )
    event = script_lifecycle_service._register_pending_kill(res_id)
    try:
        now = datetime.now(UTC)
        payload = ScriptFinishedEvent(
            reservation_id=res_id,
            exit_code=-15,
            started_at=now - timedelta(seconds=2),
            finished_at=now,
            duration_seconds=2.0,
            killed_by_corelab=True,
            killed_reason="user_cancel",
        )
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await script_lifecycle_service.on_script_finished(
                session, payload=payload, lab_id=seeded["lab_id"]
            )
        assert event.is_set()
    finally:
        script_lifecycle_service._discard_pending_kill(res_id)


async def test_cancel_active_with_running_script_agent_unreachable_keeps_active(
    seeded: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SP-5 P6-14 — agent_rpc.AgentOfflineError -> AgentUnreachableDuringCancelError
    is raised by the orchestrator, reservation stays active, audit
    attempt_failed row is written."""
    from corelab_backend.models import AuditLog
    from corelab_backend.services import agent_rpc, audit_service, script_lifecycle_service
    from sqlalchemy import select as _select

    res_id = await _insert_reservation(
        seeded,
        start_offset_min=-30,
        duration_min=60,
        status="active",
        script="sleep 999",
        script_status="running",
    )

    async def fake_request(**_: Any) -> dict[str, Any]:
        raise agent_rpc.AgentOfflineError("no live agent connection")

    monkeypatch.setattr(agent_rpc, "request_response", fake_request)

    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = await session.get(Reservation, res_id)
        assert row is not None
        with pytest.raises(script_lifecycle_service.AgentUnreachableDuringCancelError):
            await script_lifecycle_service.cancel_active_with_running_script(
                session,
                reservation=row,
                actor_user_id=seeded["bob_id"],
                actor_can_admin=True,
                reason="user testing",
                lab_id=seeded["lab_id"],
                cancel_reason_for_agent="user_cancel",
            )

    # Row still active + audit row written.
    async with factory() as session:
        row = await session.get(Reservation, res_id)
        assert row is not None
        assert row.status == reservation_service.STATUS_ACTIVE
        audits = (
            (
                await session.execute(
                    _select(AuditLog).where(
                        AuditLog.action == "reservation.cancel.attempt_failed",
                        AuditLog.target_id == res_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(audits) == 1
        assert "agent_unreachable" in (audits[0].payload or {}).get("reason", "")
    # Avoid an unused-import warning in this branch — audit_service is
    # used transitively through the orchestrator.
    del audit_service
