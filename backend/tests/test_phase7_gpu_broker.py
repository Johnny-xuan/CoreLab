"""Phase 7 C9 — gpu_broker fan-out + 1 Hz throttle (P7-14).

Unit-level tests — no DB / no WS server. Stub push_to_user +
subscribers; verify fan-out and throttle.
"""

from __future__ import annotations

from typing import Any

import pytest
from corelab_backend.services import gpu_broker
from corelab_protocol import GpuTelemetry, GpuTelemetryEntry

pytestmark = pytest.mark.asyncio


def _make_payload() -> GpuTelemetry:
    return GpuTelemetry(gpus=[GpuTelemetryEntry(gpu_index=0, util_pct=42, memory_used_mb=1024)])


async def test_fan_out_pushes_to_all_subscribers(monkeypatch: pytest.MonkeyPatch) -> None:
    gpu_broker.reset_throttle()
    pushed: list[tuple[int, dict[str, Any]]] = []

    async def fake_push(user_id: int, frame: dict[str, Any]) -> int:
        pushed.append((user_id, frame))
        return 1

    monkeypatch.setattr(gpu_broker.ws_user, "push_to_user", fake_push)
    monkeypatch.setattr(gpu_broker.ws_user, "gpu_subscribers", lambda sid: frozenset({101, 102}))

    delivered = await gpu_broker.fan_out(server_id=7, payload=_make_payload())
    assert delivered == 2
    assert {u for u, _ in pushed} == {101, 102}
    assert pushed[0][1]["type"] == "gpu.live_update"
    assert pushed[0][1]["payload"]["server_id"] == 7
    assert len(pushed[0][1]["payload"]["gpus"]) == 1


async def test_fan_out_skips_when_no_subscribers(monkeypatch: pytest.MonkeyPatch) -> None:
    gpu_broker.reset_throttle()
    pushed: list[Any] = []

    async def fake_push(*_a: Any, **_kw: Any) -> int:
        pushed.append(1)
        return 1

    monkeypatch.setattr(gpu_broker.ws_user, "push_to_user", fake_push)
    monkeypatch.setattr(gpu_broker.ws_user, "gpu_subscribers", lambda sid: frozenset())

    delivered = await gpu_broker.fan_out(server_id=9, payload=_make_payload())
    assert delivered == 0
    assert pushed == []


async def test_fan_out_throttles_to_one_hz(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two fan_out calls back-to-back → only the first is delivered."""
    gpu_broker.reset_throttle()
    gpu_broker.set_min_interval(1.0)  # production default
    pushed: list[Any] = []

    async def fake_push(*_a: Any, **_kw: Any) -> int:
        pushed.append(1)
        return 1

    monkeypatch.setattr(gpu_broker.ws_user, "push_to_user", fake_push)
    monkeypatch.setattr(gpu_broker.ws_user, "gpu_subscribers", lambda sid: frozenset({1}))

    first = await gpu_broker.fan_out(server_id=11, payload=_make_payload())
    second = await gpu_broker.fan_out(server_id=11, payload=_make_payload())
    assert first == 1
    assert second == 0
    assert len(pushed) == 1


async def test_fan_out_throttles_per_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Different servers have independent throttle windows."""
    gpu_broker.reset_throttle()
    gpu_broker.set_min_interval(1.0)
    pushed: list[int] = []

    async def fake_push(_user_id: int, _frame: dict[str, Any]) -> int:
        pushed.append(1)
        return 1

    monkeypatch.setattr(gpu_broker.ws_user, "push_to_user", fake_push)
    monkeypatch.setattr(gpu_broker.ws_user, "gpu_subscribers", lambda sid: frozenset({1}))

    a = await gpu_broker.fan_out(server_id=20, payload=_make_payload())
    b = await gpu_broker.fan_out(server_id=21, payload=_make_payload())
    assert a == 1 and b == 1
    assert len(pushed) == 2
