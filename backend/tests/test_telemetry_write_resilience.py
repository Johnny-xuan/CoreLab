from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from corelab_backend.api import agent_ws as agent_ws_module
from corelab_backend.services import telemetry_service
from corelab_protocol import GpuTelemetry, GpuTelemetryEntry
from sqlalchemy.exc import OperationalError


def _payload() -> GpuTelemetry:
    return GpuTelemetry(gpus=[GpuTelemetryEntry(gpu_index=0, util_pct=42)])


def _deadlock_exc() -> OperationalError:
    return OperationalError(
        statement="INSERT",
        params=None,
        orig=Exception("(1213, 'Deadlock found when trying to get lock')"),
    )


def _other_operational_exc() -> OperationalError:
    return OperationalError(
        statement="INSERT",
        params=None,
        orig=Exception("(1042, 'Can not get hostname')"),
    )


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1


def _session_factory(sessions: list[_FakeSession]) -> Callable[[], _FakeSession]:
    def factory() -> _FakeSession:
        session = _FakeSession()
        sessions.append(session)
        return session

    return factory


async def test_telemetry_transaction_retries_deadlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sessions: list[_FakeSession] = []

    async def fake_upsert(*_: Any, **__: Any) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _deadlock_exc()
        return True

    monkeypatch.setattr(telemetry_service, "upsert_telemetry", fake_upsert)

    accepted = await telemetry_service.upsert_telemetry_transaction(
        _session_factory(sessions),
        server_id=81,
        payload=_payload(),
        max_retries=2,
        backoff_base_s=0,
    )

    assert accepted is True
    assert calls == 2
    assert len(sessions) == 2
    assert sessions[0].commits == 0
    assert sessions[1].commits == 1


async def test_telemetry_transaction_does_not_retry_other_operational_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_upsert(*_: Any, **__: Any) -> bool:
        nonlocal calls
        calls += 1
        raise _other_operational_exc()

    monkeypatch.setattr(telemetry_service, "upsert_telemetry", fake_upsert)

    with pytest.raises(OperationalError):
        await telemetry_service.upsert_telemetry_transaction(
            _session_factory([]),
            server_id=81,
            payload=_payload(),
            max_retries=5,
            backoff_base_s=0,
        )

    assert calls == 1


async def test_agent_ws_contains_final_telemetry_operational_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_transaction(*_: Any, **__: Any) -> bool:
        raise _deadlock_exc()

    async def fail_if_fanned_out(*_: Any, **__: Any) -> int:
        raise AssertionError("failed telemetry write should not fan out")

    monkeypatch.setattr(
        agent_ws_module.telemetry_service,
        "upsert_telemetry_transaction",
        fail_transaction,
    )
    monkeypatch.setattr(agent_ws_module.gpu_broker, "fan_out", fail_if_fanned_out)

    accepted = await agent_ws_module._handle_gpu_telemetry(
        _session_factory([]),
        server_id=81,
        payload=_payload(),
    )

    assert accepted is False


async def test_agent_ws_fans_out_accepted_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fanned_out: list[int] = []

    async def accept_transaction(*_: Any, **__: Any) -> bool:
        return True

    async def fake_fan_out(*, server_id: int, payload: GpuTelemetry) -> int:
        assert payload.gpus[0].util_pct == 42
        fanned_out.append(server_id)
        return 1

    monkeypatch.setattr(
        agent_ws_module.telemetry_service,
        "upsert_telemetry_transaction",
        accept_transaction,
    )
    monkeypatch.setattr(agent_ws_module.gpu_broker, "fan_out", fake_fan_out)

    accepted = await agent_ws_module._handle_gpu_telemetry(
        _session_factory([]),
        server_id=81,
        payload=_payload(),
    )

    assert accepted is True
    assert fanned_out == [81]
