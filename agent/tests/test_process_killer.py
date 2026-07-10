"""process_killer unit tests — capability gate + mock mode + signals.

Real os.kill is monkeypatched so we assert the SIGTERM→SIGKILL escalation
and the capability gate without touching live processes.
"""

from __future__ import annotations

import signal

import pytest
from corelab_agent import capabilities, process_killer


@pytest.fixture(autouse=True)
def _reset_caps() -> None:
    capabilities.clear()


class TestCapabilityGate:
    async def test_disabled_capability_refuses(self) -> None:
        capabilities.set_enabled("gpu.kill_process", False)
        result = await process_killer.kill_pid(1234, mock_mode=False)
        assert result.ok is False
        assert result.killed is False
        assert "disabled" in (result.error or "")

    async def test_default_off_when_unset(self) -> None:
        # No capability pushed yet → default False → refuse.
        result = await process_killer.kill_pid(1234, mock_mode=False)
        assert result.ok is False


class TestMockMode:
    async def test_mock_never_signals(self, monkeypatch: pytest.MonkeyPatch) -> None:
        capabilities.set_enabled("gpu.kill_process", True)

        def _boom(*a: object, **k: object) -> None:
            raise AssertionError("mock mode must not call os.kill")

        monkeypatch.setattr(process_killer.os, "kill", _boom)
        result = await process_killer.kill_pid(1234, mock_mode=True)
        assert result.ok is True
        assert result.killed is True
        assert result.mock_warning is not None


class TestSignalEscalation:
    async def test_sigterm_then_no_sigkill_when_exits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        capabilities.set_enabled("gpu.kill_process", True)
        calls: list[tuple[int, int]] = []

        def _kill(pid: int, sig: int) -> None:
            calls.append((pid, sig))
            if sig == 0:
                # liveness probe after the grace → process is gone
                raise ProcessLookupError()

        monkeypatch.setattr(process_killer.os, "kill", _kill)
        monkeypatch.setattr(process_killer.asyncio, "sleep", _noop_sleep)

        result = await process_killer.kill_pid(4321, mock_mode=False)
        assert result.ok is True
        assert result.killed is True
        # SIGTERM sent, liveness probed, no SIGKILL needed.
        assert (4321, signal.SIGTERM) in calls
        assert (4321, signal.SIGKILL) not in calls

    async def test_sigkill_when_survives_grace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        capabilities.set_enabled("gpu.kill_process", True)
        calls: list[tuple[int, int]] = []

        def _kill(pid: int, sig: int) -> None:
            calls.append((pid, sig))
            # sig 0 liveness probe → still alive (no raise) → forces SIGKILL

        monkeypatch.setattr(process_killer.os, "kill", _kill)
        monkeypatch.setattr(process_killer.asyncio, "sleep", _noop_sleep)

        result = await process_killer.kill_pid(4321, mock_mode=False)
        assert result.ok is True
        assert result.killed is True
        assert (4321, signal.SIGTERM) in calls
        assert (4321, signal.SIGKILL) in calls

    async def test_already_gone_is_ok_not_killed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        capabilities.set_enabled("gpu.kill_process", True)

        def _kill(pid: int, sig: int) -> None:
            raise ProcessLookupError()

        monkeypatch.setattr(process_killer.os, "kill", _kill)
        result = await process_killer.kill_pid(4321, mock_mode=False)
        assert result.ok is True
        assert result.killed is False
        assert "already exited" in (result.error or "")

    async def test_permission_denied_falls_back_to_sudo_kill(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        capabilities.set_enabled("gpu.kill_process", True)
        sudo_calls: list[tuple[int, signal.Signals]] = []

        def _kill(pid: int, sig: int) -> None:
            if sig == 0:
                raise PermissionError("not owner")
            raise PermissionError("not owner")

        def _sudo_signal(pid: int, sig: signal.Signals) -> process_killer._SignalAttempt:
            sudo_calls.append((pid, sig))
            return process_killer._SignalAttempt(ok=True, used_sudo=True)

        monkeypatch.setattr(process_killer.os, "kill", _kill)
        monkeypatch.setattr(process_killer, "_sudo_signal", _sudo_signal)
        monkeypatch.setattr(process_killer.asyncio, "sleep", _noop_sleep)

        result = await process_killer.kill_pid(4321, mock_mode=False)
        assert result.ok is True
        assert result.killed is True
        assert sudo_calls == [(4321, signal.SIGTERM), (4321, signal.SIGKILL)]

    async def test_permission_denied_reports_sudo_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        capabilities.set_enabled("gpu.kill_process", True)

        def _kill(pid: int, sig: int) -> None:
            raise PermissionError("not owner")

        def _sudo_signal(pid: int, sig: signal.Signals) -> process_killer._SignalAttempt:
            return process_killer._SignalAttempt(ok=False, used_sudo=True, error="sudo denied")

        monkeypatch.setattr(process_killer.os, "kill", _kill)
        monkeypatch.setattr(process_killer, "_sudo_signal", _sudo_signal)

        result = await process_killer.kill_pid(4321, mock_mode=False)
        assert result.ok is False
        assert "sudo denied" in (result.error or "")


async def _noop_sleep(_seconds: float) -> None:
    return None
