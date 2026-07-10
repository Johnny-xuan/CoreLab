"""Phase 3 agent unit tests for the GPU collector + WS URL builder."""

from __future__ import annotations

import math

from corelab_agent.config import AgentConfig, MockGpuSpec
from corelab_agent.gpu_collector import (
    MockGpuCollector,
    NvidiaSmiCollector,
    NvidiaSmiError,
    build_collector,
)
from corelab_agent.ws_client import build_connect_url


def test_mock_collector_returns_two_gpus_with_valid_util() -> None:
    collector = MockGpuCollector(started_at_monotonic=0.0)

    import asyncio

    telemetry = asyncio.run(collector.collect())
    assert len(telemetry.gpus) == 2

    for gpu in telemetry.gpus:
        assert gpu.model == "NVIDIA RTX 4090"
        assert gpu.memory_total_mb == 24576
        assert 0 <= gpu.util_pct <= 100
        assert gpu.memory_used_mb is not None
        assert gpu.memory_used_mb <= 24576


def test_mock_collector_phase_offset_means_gpus_differ_over_time() -> None:
    """The two GPUs use offset sine phases (0 and π/2) so for most
    time samples their util_pct differs."""
    import asyncio

    samples_with_diff = 0
    samples = 6
    for shift in range(samples):
        c = MockGpuCollector(started_at_monotonic=-shift * 7.0)
        t = asyncio.run(c.collect())
        if t.gpus[0].util_pct != t.gpus[1].util_pct:
            samples_with_diff += 1
    assert samples_with_diff >= samples - 1


def test_build_collector_mock_vs_real() -> None:
    assert isinstance(build_collector(True), MockGpuCollector)
    assert isinstance(build_collector(False), NvidiaSmiCollector)


def test_real_collector_parses_gpu_and_process_csv() -> None:
    import asyncio

    async def runner(args: list[str]) -> str:
        if args[0].startswith("--query-gpu="):
            return "\n".join(
                [
                    "0, GPU-aaaa, NVIDIA A100-PCIE-40GB, 40960, 2048, 37, 55, 123.4, 8.0",
                    "1, GPU-bbbb, NVIDIA RTX 4090, 24576, 0, 0, N/A, [Not Supported], 8.9",
                ]
            )
        if args[0].startswith("--query-compute-apps="):
            return "GPU-aaaa, 2222, python, 1024\n"
        raise AssertionError(f"unexpected nvidia-smi args: {args}")

    collector = NvidiaSmiCollector(runner=runner, username_for_pid=lambda _pid: "alice")
    telemetry = asyncio.run(collector.collect())

    assert len(telemetry.gpus) == 2
    gpu0 = telemetry.gpus[0]
    assert gpu0.gpu_index == 0
    assert gpu0.uuid == "GPU-aaaa"
    assert gpu0.model == "NVIDIA A100-PCIE-40GB"
    assert gpu0.memory_total_mb == 40960
    assert gpu0.memory_used_mb == 2048
    assert gpu0.util_pct == 37
    assert gpu0.temperature_c == 55
    assert gpu0.power_w == 123
    assert gpu0.compute_capability == "8.0"
    assert len(gpu0.processes) == 1
    assert gpu0.processes[0].pid == 2222
    assert gpu0.processes[0].linux_username == "alice"
    assert gpu0.processes[0].memory_mb == 1024

    gpu1 = telemetry.gpus[1]
    assert gpu1.temperature_c is None
    assert gpu1.power_w is None
    assert gpu1.processes == []


def test_real_collector_falls_back_when_compute_cap_query_is_unsupported() -> None:
    import asyncio

    calls: list[str] = []

    async def runner(args: list[str]) -> str:
        calls.append(args[0])
        if args[0].startswith("--query-gpu="):
            if "compute_cap" in args[0]:
                raise NvidiaSmiError("Invalid query field: compute_cap")
            return "0, GPU-aaaa, NVIDIA A100, 40960, 1024, 5, 40, 80.0\n"
        if args[0].startswith("--query-compute-apps="):
            return ""
        raise AssertionError(f"unexpected nvidia-smi args: {args}")

    telemetry = asyncio.run(NvidiaSmiCollector(runner=runner).collect())

    assert telemetry.gpus[0].uuid == "GPU-aaaa"
    assert telemetry.gpus[0].compute_capability is None
    assert any("compute_cap" in call for call in calls)


def test_real_collector_keeps_gpu_rows_when_process_query_fails() -> None:
    import asyncio

    async def runner(args: list[str]) -> str:
        if args[0].startswith("--query-gpu="):
            return "0, GPU-aaaa, NVIDIA A100, 40960, 1024, 5, 40, 80.0, 8.0\n"
        if args[0].startswith("--query-compute-apps="):
            raise NvidiaSmiError("not supported")
        raise AssertionError(f"unexpected nvidia-smi args: {args}")

    telemetry = asyncio.run(NvidiaSmiCollector(runner=runner).collect())

    assert len(telemetry.gpus) == 1
    assert telemetry.gpus[0].processes == []


def test_build_connect_url_includes_token_and_server_id() -> None:
    cfg = AgentConfig(
        backend_url="ws://localhost:8080",
        enrollment_token="enroll-xyz",
        server_id=42,
        mock_mode=True,
    )
    url = build_connect_url(cfg)
    assert url == "ws://localhost:8080/ws/agent?token=enroll-xyz&server_id=42"


def test_build_connect_url_prefers_agent_token_over_enrollment() -> None:
    cfg = AgentConfig(
        backend_url="ws://localhost:8080",
        enrollment_token="enroll-xyz",
        agent_token="agent-abc",
        server_id=7,
        mock_mode=True,
    )
    url = build_connect_url(cfg)
    assert "token=agent-abc" in url
    assert "token=enroll-xyz" not in url


def test_period_constant_matches_one_cycle_per_minute() -> None:
    assert math.isclose(MockGpuCollector.PERIOD_SECONDS, 60.0)


# ── Topology-aware mode (demo simulation) ─────────────────────────────────


def _collect(specs, **kw):
    import asyncio

    return asyncio.run(MockGpuCollector(specs, started_at_monotonic=0.0, **kw).collect())


def test_topology_mode_mirrors_configured_cards() -> None:
    specs = [
        MockGpuSpec(model="NVIDIA A100 80GB PCIe", memory_total_mb=81920, compute_capability="8.0"),
        MockGpuSpec(model="NVIDIA A100 80GB PCIe", memory_total_mb=81920, compute_capability="8.0"),
        MockGpuSpec(model="NVIDIA A100 80GB PCIe", memory_total_mb=81920, compute_capability="8.0"),
    ]
    t = _collect(specs, server_id=1)
    assert len(t.gpus) == 3
    for idx, gpu in enumerate(t.gpus):
        assert gpu.gpu_index == idx
        assert gpu.model == "NVIDIA A100 80GB PCIe"
        assert gpu.memory_total_mb == 81920
        assert gpu.compute_capability == "8.0"
        assert 0 <= gpu.util_pct <= 100


def test_topology_idle_card_hovers_low_and_carries_no_process() -> None:
    t = _collect([MockGpuSpec(model="NVIDIA RTX 4090", base_util=0, process_user="alice")])
    gpu = t.gpus[0]
    assert gpu.util_pct <= 3
    assert gpu.processes == []  # idle -> no fake job even if a user is named


def test_topology_busy_card_carries_named_process() -> None:
    t = _collect([MockGpuSpec(model="NVIDIA A100 80GB PCIe", base_util=82, process_user="alice")])
    gpu = t.gpus[0]
    assert gpu.util_pct >= 50
    assert len(gpu.processes) == 1
    assert gpu.processes[0].linux_username == "alice"
    assert gpu.memory_used_mb and gpu.memory_used_mb > 0


def test_topology_uuid_is_server_scoped_and_unique() -> None:
    specs = [MockGpuSpec(model="x"), MockGpuSpec(model="x")]
    a = _collect(specs, server_id=3)
    b = _collect(specs, server_id=7)
    assert a.gpus[0].uuid != a.gpus[1].uuid  # unique within a server
    assert a.gpus[0].uuid != b.gpus[0].uuid  # and across servers
    assert "0003" in a.gpus[0].uuid


def test_build_collector_with_specs_uses_topology() -> None:
    specs = [MockGpuSpec(model="NVIDIA H100", memory_total_mb=81559)]
    collector = build_collector(True, specs, server_id=2)
    import asyncio

    t = asyncio.run(collector.collect())
    assert len(t.gpus) == 1
    assert t.gpus[0].model == "NVIDIA H100"


def test_build_collector_empty_specs_falls_back_to_legacy_default() -> None:
    collector = build_collector(True, [], server_id=2)
    import asyncio

    t = asyncio.run(collector.collect())
    assert len(t.gpus) == 2
    assert t.gpus[0].model == "NVIDIA RTX 4090"
