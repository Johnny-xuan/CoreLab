"""Unit tests for the Phase 6 agent script_runner.

Targets the Mac mock path (which is what the agent runs in dev /
this test environment) plus the cancel-side state bookkeeping that
the SP-5 P6-14 invariant depends on. The Linux subprocess path is
deliberately not unit-tested here — exercising it requires real sudo
+ a non-system uid, which only the Phase 10 deployment integration
test can provide.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from corelab_agent import capabilities
from corelab_agent.script_runner import ScriptRunner
from corelab_protocol import (
    CancelScriptRequest,
    ExecuteScriptRequest,
    MessageEnvelope,
)


@pytest.fixture(autouse=True)
def enable_script_capability() -> None:
    """Capability gate defaults to permissive but reset to a known state
    so a previous test cannot starve us."""
    capabilities.clear()
    capabilities.set_enabled("script.execute_as_user", True)


def _make_runner() -> tuple[ScriptRunner, list[MessageEnvelope]]:
    captured: list[MessageEnvelope] = []

    async def push(envelope: MessageEnvelope) -> None:
        captured.append(envelope)

    return ScriptRunner(push_event=push, mock_mode=True), captured


@pytest.fixture
async def runner_pair() -> AsyncIterator[tuple[ScriptRunner, list[MessageEnvelope]]]:
    runner, captured = _make_runner()
    try:
        yield runner, captured
    finally:
        await runner.shutdown()


def _req(reservation_id: int = 1, **overrides: Any) -> ExecuteScriptRequest:
    payload: dict[str, Any] = {
        "reservation_id": reservation_id,
        "linux_username": "test_user",
        "script": "echo hello",
        "stdout_log_path_hint": f"/tmp/test-{reservation_id}.log",
    }
    payload.update(overrides)
    return ExecuteScriptRequest(**payload)


async def test_execute_mock_returns_started_and_warning(
    runner_pair: tuple[ScriptRunner, list[MessageEnvelope]],
) -> None:
    runner, _ = runner_pair
    response = await runner.execute(_req())
    assert response.ok is True
    assert response.started is True
    assert response.mock_warning is not None
    assert response.mock_warning.startswith("MOCK-")


async def test_execute_mock_pushes_started_then_finished(
    monkeypatch: pytest.MonkeyPatch,
    runner_pair: tuple[ScriptRunner, list[MessageEnvelope]],
) -> None:
    """The mock background task should push started + finished events."""
    runner, captured = runner_pair
    # Speed up the mock timer so the test does not actually sleep 50 ms.
    monkeypatch.setattr("corelab_agent.script_runner.MOCK_DURATION_SECONDS", 0.001)

    response = await runner.execute(_req(reservation_id=42))
    assert response.ok is True
    # Wait for the background task — script.finished is the cleanup signal.
    import asyncio

    for _ in range(100):
        if any(env.type == "agent.script.finished" for env in captured):
            break
        await asyncio.sleep(0.005)

    types = [env.type for env in captured]
    assert "agent.script.started" in types
    assert "agent.script.output_chunk" in types
    assert "agent.script.finished" in types
    chunk = next(env for env in captured if env.type == "agent.script.output_chunk")
    assert chunk.payload["reservation_id"] == 42
    assert "corelab-agent MOCK" in chunk.payload["text"]
    finished = next(env for env in captured if env.type == "agent.script.finished")
    assert finished.payload["reservation_id"] == 42
    assert finished.payload["killed_by_corelab"] is False


async def test_execute_refuses_when_capability_disabled(
    runner_pair: tuple[ScriptRunner, list[MessageEnvelope]],
) -> None:
    runner, _ = runner_pair
    capabilities.set_enabled("script.execute_as_user", False)
    response = await runner.execute(_req(reservation_id=2))
    assert response.ok is False
    assert response.started is False
    assert response.error == "CAPABILITY_DISABLED"


async def test_execute_refuses_duplicate_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    runner_pair: tuple[ScriptRunner, list[MessageEnvelope]],
) -> None:
    """A second execute() for an already-running reservation_id is refused."""
    runner, _ = runner_pair
    # Keep the first run "in flight" for the duration of the test by
    # making the mock duration long; we will not await its finish.
    monkeypatch.setattr("corelab_agent.script_runner.MOCK_DURATION_SECONDS", 5.0)
    first = await runner.execute(_req(reservation_id=99))
    assert first.ok is True
    second = await runner.execute(_req(reservation_id=99))
    assert second.ok is False
    assert second.error == "ALREADY_RUNNING"


async def test_cancel_no_live_process_returns_cancelled_false(
    runner_pair: tuple[ScriptRunner, list[MessageEnvelope]],
) -> None:
    runner, _ = runner_pair
    response = await runner.cancel(CancelScriptRequest(reservation_id=12345, reason="user_cancel"))
    assert response.ok is True
    assert response.cancelled is False
    assert response.detail is not None


async def test_cancel_marks_killed_reason_on_mock_entry(
    monkeypatch: pytest.MonkeyPatch,
    runner_pair: tuple[ScriptRunner, list[MessageEnvelope]],
) -> None:
    """Cancel on a mock run sets killed_reason so the eventual
    script.finished event will carry killed_by_corelab=true."""
    runner, captured = runner_pair
    monkeypatch.setattr("corelab_agent.script_runner.MOCK_DURATION_SECONDS", 0.1)
    await runner.execute(_req(reservation_id=77))
    response = await runner.cancel(CancelScriptRequest(reservation_id=77, reason="admin_cancel"))
    assert response.ok is True
    assert response.cancelled is True
    # Mock path emits a warning so dev UIs don't mistake it for the real kill.
    assert response.mock_warning == "MOCK-MAC-NO-PROCESS"
    # Wait for the background task to push the finished event with the
    # cancel reason baked in.
    import asyncio

    for _ in range(100):
        if any(env.type == "agent.script.finished" for env in captured):
            break
        await asyncio.sleep(0.005)
    finished = next(env for env in captured if env.type == "agent.script.finished")
    assert finished.payload["killed_by_corelab"] is True
    assert finished.payload["killed_reason"] == "admin_cancel"


def test_started_at_serializes_to_iso() -> None:
    """Defensive: ScriptStartedEvent's datetime payload survives
    model_dump(mode='json') so the WS receive side parses cleanly."""
    from corelab_protocol import ScriptStartedEvent

    event = ScriptStartedEvent(
        reservation_id=1, pid=42, started_at=datetime.now(UTC), log_path=None
    )
    dumped = event.model_dump(mode="json")
    assert isinstance(dumped["started_at"], str)
    assert dumped["started_at"].endswith("Z") or "+" in dumped["started_at"]
