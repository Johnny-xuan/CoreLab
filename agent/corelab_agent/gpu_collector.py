"""GPU telemetry source.

Two implementations share the :class:`GpuCollector` Protocol:

* :class:`MockGpuCollector` — returns fake GPUs whose util_pct swings on a
  sine wave (off-phase per index so the dashboard shows movement). With
  no topology configured it emits the legacy 2xRTX 4090 default; given a
  list of :class:`~corelab_agent.config.MockGpuSpec` (e.g. from
  ``scripts/demo_agents.py``) it mirrors a seeded server's real topology
  so live telemetry lands cleanly on the existing ``(server_id,
  gpu_index)`` rows. No subprocess, no nvidia-smi.
* :class:`NvidiaSmiCollector` — shells out to ``nvidia-smi --query-gpu``
  + ``--query-compute-apps`` and parses the CSV output (Phase 3 ships
  the skeleton; full process-snapshot parsing lands as soon as Phase 4+
  needs the data).

The agent picks the implementation at startup based on
``AgentConfig.mock_mode``.
"""

from __future__ import annotations

import asyncio
import csv
import io
import math
import pwd
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from corelab_protocol import GpuProcessSnapshot, GpuTelemetry, GpuTelemetryEntry

from .logging_setup import get_logger

if TYPE_CHECKING:
    from .config import MockGpuSpec

log = get_logger("corelab.agent.gpu_collector")


class GpuCollector(Protocol):
    async def collect(self) -> GpuTelemetry: ...


class MockGpuCollector:
    """Deterministic GPU stub for Mac / dev / demo.

    util_pct oscillates as a sine wave so the UI shows live movement
    without real hardware. Two modes:

    * **legacy** (``specs is None``) — 2 RTX 4090s swinging full 0..100 on
      offset phases, preserved for the existing dev workflow + tests.
    * **topology** (``specs`` given) — one card per spec at the matching
      ``gpu_index``; an idle card (``base_util == 0``) hovers near 0, a
      loaded card oscillates ±12 around its ``base_util`` and, if
      ``process_user`` is set, carries one fake compute process so the
      card reads as genuinely occupied.
    """

    PERIOD_SECONDS = 60.0
    _LOAD_SWING = 12.0

    def __init__(
        self,
        specs: list[MockGpuSpec] | None = None,
        *,
        server_id: int | None = None,
        started_at_monotonic: float | None = None,
    ) -> None:
        self._specs = list(specs) if specs else None
        self._server_id = server_id
        self._started = (
            started_at_monotonic if started_at_monotonic is not None else time.monotonic()
        )

    def _elapsed(self) -> float:
        return time.monotonic() - self._started

    def _wave(self, phase: float) -> int:
        """Legacy full-swing wave (50 ± 50) for the default cards."""
        value = 50.0 + 50.0 * math.sin(2 * math.pi * self._elapsed() / self.PERIOD_SECONDS + phase)
        return max(0, min(100, round(value)))

    def _load_wave(self, base_util: int, phase: float) -> int:
        """Topology wave: idle hovers near 0, load oscillates ±_LOAD_SWING."""
        t = 2 * math.pi * self._elapsed() / self.PERIOD_SECONDS + phase
        if base_util <= 0:
            # Idle card: a sliver of background noise (0..3%) so it isn't a
            # dead flat line, but unmistakably "free".
            return max(0, min(3, round(1.5 + 1.5 * math.sin(t))))
        value = base_util + self._LOAD_SWING * math.sin(t)
        return max(0, min(100, round(value)))

    def _uuid(self, gpu_index: int) -> str:
        sid = self._server_id if self._server_id is not None else 0
        return f"GPU-mock-{sid:04d}-{gpu_index:02d}"

    async def collect(self) -> GpuTelemetry:
        if self._specs is None:
            return self._collect_legacy()
        return self._collect_topology()

    def _collect_topology(self) -> GpuTelemetry:
        assert self._specs is not None
        entries: list[GpuTelemetryEntry] = []
        for idx, spec in enumerate(self._specs):
            phase = (idx % 4) * (math.pi / 2)
            util = self._load_wave(spec.base_util, phase)
            mem_used = int(spec.memory_total_mb * util / 100)
            processes: list[GpuProcessSnapshot] = []
            if spec.process_user and util > 20:
                processes = [
                    GpuProcessSnapshot(
                        pid=10000 + idx,
                        linux_username=spec.process_user,
                        memory_mb=mem_used,
                    )
                ]
            entries.append(
                GpuTelemetryEntry(
                    gpu_index=idx,
                    uuid=self._uuid(idx),
                    model=spec.model,
                    memory_total_mb=spec.memory_total_mb,
                    compute_capability=spec.compute_capability,
                    util_pct=util,
                    memory_used_mb=mem_used,
                    temperature_c=35 + util // 3,
                    power_w=60 + util * 3,
                    processes=processes,
                )
            )
        return GpuTelemetry(gpus=entries)

    def _collect_legacy(self) -> GpuTelemetry:
        gpu0_util = self._wave(0.0)
        gpu1_util = self._wave(math.pi / 2)
        gpu_total = 24576
        return GpuTelemetry(
            gpus=[
                GpuTelemetryEntry(
                    gpu_index=0,
                    uuid="GPU-mock-00000000-0000-0000-0000-000000000000",
                    model="NVIDIA RTX 4090",
                    memory_total_mb=gpu_total,
                    compute_capability="8.9",
                    util_pct=gpu0_util,
                    memory_used_mb=int(gpu_total * gpu0_util / 100),
                    temperature_c=40 + gpu0_util // 3,
                    power_w=70 + gpu0_util * 3,
                    processes=[],
                ),
                GpuTelemetryEntry(
                    gpu_index=1,
                    uuid="GPU-mock-00000000-0000-0000-0000-000000000001",
                    model="NVIDIA RTX 4090",
                    memory_total_mb=gpu_total,
                    compute_capability="8.9",
                    util_pct=gpu1_util,
                    memory_used_mb=int(gpu_total * gpu1_util / 100),
                    temperature_c=40 + gpu1_util // 3,
                    power_w=70 + gpu1_util * 3,
                    processes=(
                        [GpuProcessSnapshot(pid=1234, linux_username="alice", memory_mb=15000)]
                        if gpu1_util > 60
                        else []
                    ),
                ),
            ]
        )


class NvidiaSmiCollector:
    """Real nvidia-smi parser (Linux + nvidia driver).

    Uses ``nvidia-smi --query-gpu`` for card-level telemetry and
    ``--query-compute-apps`` for process snapshots. The process query is
    best-effort because containerized driver setups may not expose it; GPU
    rows still report if process rows fail.
    """

    _GPU_FIELDS_WITH_COMPUTE: tuple[str, ...] = (
        "index",
        "uuid",
        "name",
        "memory.total",
        "memory.used",
        "utilization.gpu",
        "temperature.gpu",
        "power.draw",
        "compute_cap",
    )
    _GPU_FIELDS_BASE: tuple[str, ...] = _GPU_FIELDS_WITH_COMPUTE[:-1]
    _PROCESS_FIELDS: tuple[str, ...] = (
        "gpu_uuid",
        "pid",
        "process_name",
        "used_memory",
    )

    def __init__(
        self,
        *,
        command: str = "nvidia-smi",
        runner: Callable[[list[str]], Awaitable[str]] | None = None,
        username_for_pid: Callable[[int], str] | None = None,
    ) -> None:
        self._command = command
        self._runner = runner
        self._username_for_pid = username_for_pid or _linux_username_for_pid

    async def collect(self) -> GpuTelemetry:
        gpu_text, has_compute_cap = await self._query_gpus()
        processes_by_uuid = await self._query_processes()
        entries = _parse_gpu_rows(
            gpu_text,
            has_compute_cap=has_compute_cap,
            processes_by_uuid=processes_by_uuid,
        )
        return GpuTelemetry(gpus=entries)

    async def _query_gpus(self) -> tuple[str, bool]:
        try:
            return await self._run_query("gpu", self._GPU_FIELDS_WITH_COMPUTE), True
        except NvidiaSmiError as exc:
            if "compute_cap" not in str(exc) and "Invalid query" not in str(exc):
                raise
            log.warning("nvidia_smi.compute_cap_unsupported", error=str(exc))
            return await self._run_query("gpu", self._GPU_FIELDS_BASE), False

    async def _query_processes(self) -> dict[str, list[GpuProcessSnapshot]]:
        try:
            text = await self._run_query("compute-apps", self._PROCESS_FIELDS)
        except NvidiaSmiError as exc:
            log.warning("nvidia_smi.process_query_failed", error=str(exc))
            return {}
        return _parse_process_rows(text, username_for_pid=self._username_for_pid)

    async def _run_query(self, target: str, fields: tuple[str, ...]) -> str:
        args = [
            f"--query-{target}={','.join(fields)}",
            "--format=csv,noheader,nounits",
        ]
        if self._runner is not None:
            return await self._runner(args)
        return await self._run_subprocess(args)

    async def _run_subprocess(self, args: list[str]) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._command,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise NvidiaSmiError(f"{self._command!r} not found") from exc
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode("utf-8", "replace").strip()
            raise NvidiaSmiError(err or f"{self._command} exited {proc.returncode}")
        return stdout.decode("utf-8", "replace")


class NvidiaSmiError(RuntimeError):
    """nvidia-smi command failed or is unavailable."""


class NvidiaSmiParseError(ValueError):
    """nvidia-smi returned a row shape we do not understand."""


_MISSING_VALUES = {"", "n/a", "na", "[not supported]", "not supported", "none"}
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _csv_rows(text: str) -> list[list[str]]:
    reader = csv.reader(io.StringIO(text), skipinitialspace=True)
    return [
        [cell.strip() for cell in row]
        for row in reader
        if row and any(cell.strip() for cell in row)
    ]


def _parse_number(value: str) -> float | None:
    normalized = value.strip().lower()
    if normalized in _MISSING_VALUES:
        return None
    match = _NUMBER_RE.search(value)
    if match is None:
        return None
    return float(match.group(0))


def _parse_int(value: str, *, default: int | None = None) -> int | None:
    number = _parse_number(value)
    if number is None:
        return default
    return round(number)


def _parse_gpu_rows(
    text: str,
    *,
    has_compute_cap: bool,
    processes_by_uuid: dict[str, list[GpuProcessSnapshot]],
) -> list[GpuTelemetryEntry]:
    expected_len = 9 if has_compute_cap else 8
    entries: list[GpuTelemetryEntry] = []
    for row in _csv_rows(text):
        if len(row) != expected_len:
            raise NvidiaSmiParseError(
                f"expected {expected_len} gpu fields, got {len(row)}: {row!r}"
            )
        index_s, uuid, name, mem_total, mem_used, util, temp, power, *rest = row
        gpu_index = _parse_int(index_s)
        if gpu_index is None:
            raise NvidiaSmiParseError(f"invalid gpu index: {index_s!r}")
        compute_capability = rest[0] if rest and rest[0] else None
        entries.append(
            GpuTelemetryEntry(
                gpu_index=gpu_index,
                uuid=uuid or None,
                model=name or None,
                memory_total_mb=_parse_int(mem_total),
                compute_capability=compute_capability,
                util_pct=_parse_int(util, default=0) or 0,
                memory_used_mb=_parse_int(mem_used),
                temperature_c=_parse_int(temp),
                power_w=_parse_int(power),
                processes=processes_by_uuid.get(uuid, []),
            )
        )
    return entries


def _parse_process_rows(
    text: str,
    *,
    username_for_pid: Callable[[int], str],
) -> dict[str, list[GpuProcessSnapshot]]:
    processes_by_uuid: dict[str, list[GpuProcessSnapshot]] = {}
    for row in _csv_rows(text):
        if len(row) != 4:
            raise NvidiaSmiParseError(f"expected 4 process fields, got {len(row)}: {row!r}")
        gpu_uuid, pid_s, _process_name, used_memory = row
        pid = _parse_int(pid_s)
        if pid is None:
            continue
        try:
            linux_username = username_for_pid(pid)
        except OSError:
            linux_username = "unknown"
        snapshot = GpuProcessSnapshot(
            pid=pid,
            linux_username=linux_username,
            memory_mb=_parse_int(used_memory, default=0) or 0,
        )
        processes_by_uuid.setdefault(gpu_uuid, []).append(snapshot)
    return processes_by_uuid


def _linux_username_for_pid(pid: int) -> str:
    stat = Path(f"/proc/{pid}").stat()
    return pwd.getpwuid(stat.st_uid).pw_name


def build_collector(
    mock_mode: bool,
    specs: list[MockGpuSpec] | None = None,
    server_id: int | None = None,
) -> GpuCollector:
    if mock_mode:
        return MockGpuCollector(specs or None, server_id=server_id)
    return NvidiaSmiCollector()
