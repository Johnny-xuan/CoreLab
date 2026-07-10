"""Phase 8 C3 — agent compliance_monitor tests (P8-5 / P8-6).

Verifies the per-tick decision tree on synthetic GpuTelemetry snapshots:
* unlinked_user_occupy fires when no link cached
* no_reservation_occupy when linked but no active reservation
* preempt_others_reservation when another linked user holds the GPU
* compliant path (no violation) when own reservation overlaps now
* memory_overuse when shared mode + over +20% (default tolerance)
* admin_declared link source does NOT count as a covering reservation
* gpu_temp_high + gpu_hang from telemetry-only signals
* run_tick does not invoke any agent_rpc (P8-5 "no RPC during tick")
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from corelab_agent import compliance_monitor, policy_cache, reverse_cache
from corelab_agent.violations import Violation
from corelab_protocol import GpuProcessSnapshot, GpuTelemetry, GpuTelemetryEntry


def _now_iso(offset_minutes: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(minutes=offset_minutes)).isoformat()


@pytest.fixture(autouse=True)
def _reset() -> None:
    reverse_cache.reset()
    policy_cache.reset()


def _seed_link(
    server_id: int,
    linux_username: str,
    user_ids: list[int],
    active_reservations: dict[str, list[dict[str, Any]]] | None = None,
) -> None:
    reverse_cache.apply_incremental(
        server_id=server_id,
        entries=[
            {
                "linux_username": linux_username,
                "user_ids": user_ids,
                "active_reservations": active_reservations or {},
            }
        ],
    )


def _telemetry_one_proc(
    gpu_index: int, linux_username: str, pid: int, memory_mb: int
) -> GpuTelemetry:
    return GpuTelemetry(
        gpus=[
            GpuTelemetryEntry(
                gpu_index=gpu_index,
                util_pct=50,
                memory_used_mb=memory_mb,
                temperature_c=60,
                processes=[
                    GpuProcessSnapshot(
                        pid=pid,
                        linux_username=linux_username,
                        memory_mb=memory_mb,
                    )
                ],
            )
        ],
    )


class _Recorder:
    """Captures violations dispatched + how many."""

    def __init__(self) -> None:
        self.violations: list[Violation] = []

    async def __call__(self, v: Violation) -> str:
        self.violations.append(v)
        return "log_only"


async def _run_one_tick(server_id: int, telemetry: GpuTelemetry) -> list[Violation]:
    recorder = _Recorder()
    state = compliance_monitor._MonitorState()
    await compliance_monitor.run_tick(
        server_id=server_id,
        telemetry=telemetry,
        dispatcher=recorder,
        state=state,
    )
    return recorder.violations


@pytest.mark.asyncio
async def test_unlinked_user_occupy_fires() -> None:
    # No cache entry for "stranger" → unlinked_user_occupy.
    tel = _telemetry_one_proc(gpu_index=0, linux_username="stranger", pid=999, memory_mb=8000)
    violations = await _run_one_tick(server_id=7, telemetry=tel)
    assert len(violations) == 1
    assert violations[0].policy_key == "unlinked_user_occupy"
    assert violations[0].linux_username == "stranger"


@pytest.mark.asyncio
async def test_no_reservation_occupy_fires_when_linked_but_no_active_res() -> None:
    _seed_link(7, "ivy_lab", [42], active_reservations={"42": []})
    tel = _telemetry_one_proc(gpu_index=0, linux_username="ivy_lab", pid=999, memory_mb=8000)
    violations = await _run_one_tick(server_id=7, telemetry=tel)
    assert len(violations) == 1
    assert violations[0].policy_key == "no_reservation_occupy"
    assert violations[0].linked_user_ids == [42]


@pytest.mark.asyncio
async def test_compliant_when_own_reservation_covers_now() -> None:
    _seed_link(
        7,
        "ivy_lab",
        [42],
        active_reservations={
            "42": [
                {
                    "reservation_id": 100,
                    "gpu_id": 0,
                    "start_at": _now_iso(-30),
                    "end_at": _now_iso(60),
                    "status": "active",
                    "gpu_memory_mb": None,
                    "gpu_compute_share_pct": None,
                    "source": "ssh_challenge",
                }
            ]
        },
    )
    tel = _telemetry_one_proc(gpu_index=0, linux_username="ivy_lab", pid=999, memory_mb=8000)
    violations = await _run_one_tick(server_id=7, telemetry=tel)
    assert violations == []


@pytest.mark.asyncio
async def test_admin_declared_link_does_not_count_as_actas() -> None:
    """docs/04 §9.8 P5 — admin_declared link is visible for reverse
    lookup but cannot act-as. So a process running under it without
    any verified covering link should still be flagged."""
    _seed_link(
        7,
        "ivy_lab",
        [42],
        active_reservations={
            "42": [
                {
                    "reservation_id": 100,
                    "gpu_id": 0,
                    "start_at": _now_iso(-30),
                    "end_at": _now_iso(60),
                    "status": "active",
                    "gpu_memory_mb": None,
                    "gpu_compute_share_pct": None,
                    "source": "admin_declared",  # cannot act-as
                }
            ]
        },
    )
    tel = _telemetry_one_proc(gpu_index=0, linux_username="ivy_lab", pid=999, memory_mb=8000)
    violations = await _run_one_tick(server_id=7, telemetry=tel)
    assert len(violations) == 1
    assert violations[0].policy_key == "no_reservation_occupy"


@pytest.mark.asyncio
async def test_preempt_others_reservation_fires() -> None:
    # bob_lab has a reservation on gpu 0; ivy is running on gpu 0 without one.
    _seed_link(
        7,
        "bob_lab",
        [50],
        active_reservations={
            "50": [
                {
                    "reservation_id": 200,
                    "gpu_id": 0,
                    "start_at": _now_iso(-15),
                    "end_at": _now_iso(60),
                    "status": "active",
                    "gpu_memory_mb": None,
                    "gpu_compute_share_pct": None,
                    "source": "ssh_challenge",
                }
            ]
        },
    )
    _seed_link(7, "ivy_lab", [42], active_reservations={"42": []})
    tel = _telemetry_one_proc(gpu_index=0, linux_username="ivy_lab", pid=999, memory_mb=8000)
    violations = await _run_one_tick(server_id=7, telemetry=tel)
    assert len(violations) == 1
    assert violations[0].policy_key == "preempt_others_reservation"


@pytest.mark.asyncio
async def test_memory_overuse_fires_in_shared_mode() -> None:
    # Reservation quota 8000MB, process uses 10000MB (>+20% = >9600MB).
    _seed_link(
        7,
        "ivy_lab",
        [42],
        active_reservations={
            "42": [
                {
                    "reservation_id": 100,
                    "gpu_id": 0,
                    "start_at": _now_iso(-15),
                    "end_at": _now_iso(60),
                    "status": "active",
                    "gpu_memory_mb": 8000,
                    "gpu_compute_share_pct": 50,
                    "source": "ssh_challenge",
                }
            ]
        },
    )
    tel = _telemetry_one_proc(gpu_index=0, linux_username="ivy_lab", pid=999, memory_mb=10000)
    violations = await _run_one_tick(server_id=7, telemetry=tel)
    assert len(violations) == 1
    assert violations[0].policy_key == "memory_overuse"
    assert violations[0].payload["memory_used_mb"] == 10000
    assert violations[0].payload["memory_quota_mb"] == 8000


@pytest.mark.asyncio
async def test_memory_overuse_does_not_fire_within_tolerance() -> None:
    _seed_link(
        7,
        "ivy_lab",
        [42],
        active_reservations={
            "42": [
                {
                    "reservation_id": 100,
                    "gpu_id": 0,
                    "start_at": _now_iso(-15),
                    "end_at": _now_iso(60),
                    "status": "active",
                    "gpu_memory_mb": 8000,
                    "gpu_compute_share_pct": 50,
                    "source": "ssh_challenge",
                }
            ]
        },
    )
    # 9000 < 8000 * 1.20 = 9600 → compliant.
    tel = _telemetry_one_proc(gpu_index=0, linux_username="ivy_lab", pid=999, memory_mb=9000)
    violations = await _run_one_tick(server_id=7, telemetry=tel)
    assert violations == []


@pytest.mark.asyncio
async def test_gpu_temp_high_from_telemetry() -> None:
    # No processes; pure GPU-level signal.
    tel = GpuTelemetry(
        gpus=[
            GpuTelemetryEntry(
                gpu_index=0,
                util_pct=80,
                memory_used_mb=4000,
                temperature_c=92,  # > default 85
            )
        ],
    )
    violations = await _run_one_tick(server_id=7, telemetry=tel)
    assert any(v.policy_key == "gpu_temp_high" for v in violations)


@pytest.mark.asyncio
async def test_gpu_hang_requires_util_zero_streak_and_memory_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hang detection needs util=0 sustained for ``util_zero_seconds`` seconds
    AND ``memory_used_mb`` >= the configured ``mem_floor_mb``."""
    # Cache a hang policy with low threshold so the test does not have
    # to wait. FU-37 — threshold_value is now a dict.
    policy_cache.apply_sync(
        server_id=7,
        policies=[
            {
                "key": "gpu_hang",
                "enabled": True,
                "severity": "notify",
                "threshold_value": {"util_zero_seconds": 1, "mem_floor_mb": 1024},
                "grace_period_seconds": None,
                "notify_admin": False,
            }
        ],
        etag="e1",
    )
    state = compliance_monitor._MonitorState()
    recorder = _Recorder()
    tel = GpuTelemetry(
        gpus=[GpuTelemetryEntry(gpu_index=0, util_pct=0, memory_used_mb=2048, temperature_c=40)],
    )
    # First tick — streak just started; no hang yet.
    await compliance_monitor.run_tick(server_id=7, telemetry=tel, dispatcher=recorder, state=state)
    assert all(v.policy_key != "gpu_hang" for v in recorder.violations)
    # Advance monotonic clock past the 1s threshold.
    real_monotonic = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic + 5.0)
    await compliance_monitor.run_tick(server_id=7, telemetry=tel, dispatcher=recorder, state=state)
    assert any(v.policy_key == "gpu_hang" for v in recorder.violations)


def test_monitor_module_does_not_import_any_rpc_client() -> None:
    """P8-5 invariant — the compliance_monitor must not pull in any
    backend-facing RPC client. Static check: read the module source +
    look for forbidden imports. Far more decisive than a "spy + count"
    runtime test because design drift can't be silently hidden by
    a dispatcher injection."""
    from pathlib import Path

    src = Path(compliance_monitor.__file__).read_text()
    # The agent has no ``agent_rpc`` (that's a backend module name) so
    # the only path that could violate the invariant is direct WS push
    # via ws_client._push_envelope or a hand-rolled httpx call. Forbid
    # both.
    for forbidden in (
        "from .ws_client",
        "import ws_client",
        "httpx",
        "requests",
        "_push_envelope",
    ):
        assert forbidden not in src, f"compliance_monitor must not use {forbidden!r}"


@pytest.mark.asyncio
async def test_monitor_loop_starts_and_stops_cleanly() -> None:
    """ComplianceMonitor.start() + request_stop() must shut down within
    a tick of being asked."""
    from corelab_agent.gpu_collector import MockGpuCollector

    recorder = _Recorder()
    monitor = compliance_monitor.ComplianceMonitor(
        server_id=7,
        gpu_collector=MockGpuCollector(),
        dispatcher=recorder,
        tick_interval_seconds=0.2,
    )
    monitor.start()
    await asyncio.sleep(0.5)  # let at least one tick run
    monitor.request_stop()
    await monitor.join()
