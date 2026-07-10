"""Phase 8 C3 — agent 60s compliance monitor (P8-5).

Per docs/04 §9.7.1 the monitor iterates live GPU processes every 60s
(or on a GPU process event) and, **using only the local
reverse_cache + policy_cache**, decides which compliance ``policy_key``
fires for each process. Violations are dispatched through a pluggable
:data:`~corelab_agent.violations.Dispatcher` so the C4 policy_handlers
module owns the severity-routing decision while this module stays
focused on detection.

Worker Catch #1 architectural fix: the tick performs **no** WSS RPC
back to the backend — all reverse-lookup data (``user_ids`` +
``active_reservations``) is already in :mod:`reverse_cache` because
backend pushed ``backend.account_link_cache.sync`` ahead of time.
``test_monitor_no_rpc_during_tick`` validates this invariant.

Per-GPU + per-process state (e.g. ``util=0`` streak for ``gpu_hang``)
is tracked in ``_MonitorState`` so hangs are detected across ticks
without re-fetching anything.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from corelab_protocol import GpuTelemetry

from . import policy_cache, reverse_cache
from .gpu_collector import GpuCollector
from .logging_setup import get_logger
from .violations import Dispatcher, Violation

log = get_logger("corelab.agent.compliance")

DEFAULT_TICK_INTERVAL_S: Final[float] = 60.0
# Phase 9 / FU-37 — gpu_hang now carries
# ``threshold_value = {"util_zero_seconds": N, "mem_floor_mb": M}``;
# admin tunes both per server via the policy UI. The defaults below
# only fire when no row has been pushed yet (cold-start before the
# first ``backend.policy.sync``).
_GPU_HANG_DEFAULT_UTIL_ZERO_S: Final[int] = 600
_GPU_HANG_DEFAULT_MEM_FLOOR_MB: Final[int] = 1024
_MEMORY_OVERUSE_DEFAULT_PCT: Final[int] = 20
_GPU_TEMP_HIGH_DEFAULT_C: Final[int] = 85


@dataclass
class _GpuTickState:
    util_zero_since_monotonic: float | None = None


@dataclass
class _MonitorState:
    # Keyed by (server_id, gpu_index)
    gpu_state: dict[tuple[int, int], _GpuTickState] = field(default_factory=dict)


def _gpu_state(state: _MonitorState, server_id: int, gpu_index: int) -> _GpuTickState:
    key = (server_id, gpu_index)
    if key not in state.gpu_state:
        state.gpu_state[key] = _GpuTickState()
    return state.gpu_state[key]


def _reservation_overlaps_now(start_at: str, end_at: str) -> bool:
    try:
        start = datetime.fromisoformat(start_at)
        end = datetime.fromisoformat(end_at)
    except ValueError:
        return False
    now = datetime.now(UTC)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return start <= now < end


def _evaluate_process(
    *,
    server_id: int,
    gpu_index: int,
    linux_username: str,
    pid: int,
    memory_used_mb: int,
) -> Violation | None:
    """Per-process reverse-lookup + decide which policy_key fires.

    Returns ``None`` if the process is compliant (linked user with an
    active reservation covering this GPU now and memory within bounds).
    """
    linked_user_ids = reverse_cache.lookup_user_ids(server_id, linux_username)
    if not linked_user_ids:
        # docs/04 §9.7.1 step 1 — Linux account no platform user link.
        return Violation(
            policy_key="unlinked_user_occupy",
            server_id=server_id,
            gpu_id_local=gpu_index,
            linux_username=linux_username,
            pid=pid,
            linked_user_ids=[],
            payload={"memory_used_mb": memory_used_mb},
        )

    # Step 2: any of the linked users have a reservation on THIS gpu_index
    # NOW with a non-admin_declared link? (docs/04 §9.7.1 step 2 + §9.8 P5)
    own_reservation = None
    for uid in linked_user_ids:
        for res in reverse_cache.lookup_active_reservations(server_id, linux_username, uid):
            if res.gpu_id != gpu_index:
                continue
            if res.source == "admin_declared":
                continue  # admin_declared link cannot act-as.
            if not _reservation_overlaps_now(res.start_at, res.end_at):
                continue
            own_reservation = res
            break
        if own_reservation is not None:
            break

    if own_reservation is not None:
        # Compliant — unless shared mode + memory over the soft limit.
        quota_mb = own_reservation.gpu_memory_mb
        if quota_mb is not None and memory_used_mb > quota_mb * (
            1 + _memory_overuse_threshold_pct(server_id) / 100.0
        ):
            return Violation(
                policy_key="memory_overuse",
                server_id=server_id,
                gpu_id_local=gpu_index,
                linux_username=linux_username,
                pid=pid,
                linked_user_ids=linked_user_ids,
                payload={
                    "memory_used_mb": memory_used_mb,
                    "memory_quota_mb": quota_mb,
                    "reservation_id": own_reservation.reservation_id,
                },
            )
        return None  # compliant

    # Step 3: another linked user (NOT us) holds this GPU right now?
    # Without the full cross-user cache (which we don't push), we treat
    # "any reservation exists on this gpu_index from a different
    # linux_username's cached entries" as preempt. For Phase 8 we
    # surface no_reservation_occupy when we can't find any holder.
    other_holder = False
    # Scan all cached (server_id, _) entries; if any has an
    # active_reservation on this gpu_index, somebody else holds it.
    for cache_key in [k for k in _cache_keys_for_server(server_id) if k[1] != linux_username]:
        _, other_linux = cache_key
        # Need to know that the other entry's users exist.
        other_users = reverse_cache.lookup_user_ids(server_id, other_linux)
        for ouid in other_users:
            for res in reverse_cache.lookup_active_reservations(server_id, other_linux, ouid):
                if (
                    res.gpu_id == gpu_index
                    and res.source != "admin_declared"
                    and _reservation_overlaps_now(res.start_at, res.end_at)
                ):
                    other_holder = True
                    break
            if other_holder:
                break
        if other_holder:
            break

    if other_holder:
        return Violation(
            policy_key="preempt_others_reservation",
            server_id=server_id,
            gpu_id_local=gpu_index,
            linux_username=linux_username,
            pid=pid,
            linked_user_ids=linked_user_ids,
            payload={"memory_used_mb": memory_used_mb},
        )
    return Violation(
        policy_key="no_reservation_occupy",
        server_id=server_id,
        gpu_id_local=gpu_index,
        linux_username=linux_username,
        pid=pid,
        linked_user_ids=linked_user_ids,
        payload={"memory_used_mb": memory_used_mb},
    )


def _cache_keys_for_server(server_id: int) -> list[tuple[int, str]]:
    # Reach into reverse_cache's storage carefully — we only need the
    # set of (server_id, linux_username) keys to find "any other holder".
    return [(sid, lux) for (sid, lux) in reverse_cache._cache if sid == server_id]


def _memory_overuse_threshold_pct(server_id: int) -> int:
    """Return the +N% soft tolerance for ``memory_overuse``.

    Phase 9 / FU-37 — ``threshold_value`` is now ``{value, unit}``.
    Falls back to :data:`_MEMORY_OVERUSE_DEFAULT_PCT` when the policy
    has not yet been pushed (cold-start)."""
    entry = policy_cache.get(server_id, "memory_overuse")
    if entry is None or not isinstance(entry.threshold_value, dict):
        return _MEMORY_OVERUSE_DEFAULT_PCT
    return int(entry.threshold_value.get("value", _MEMORY_OVERUSE_DEFAULT_PCT))


def _evaluate_gpu(
    *,
    server_id: int,
    gpu_entry: object,  # GpuTelemetryEntry — duck typed to avoid hard import circular
    state: _MonitorState,
) -> list[Violation]:
    """Per-GPU level detection (hang / temp). Process-level violations
    come from :func:`_evaluate_process` and are appended elsewhere."""
    out: list[Violation] = []
    util_pct = int(getattr(gpu_entry, "util_pct", 0))
    memory_used_mb = int(getattr(gpu_entry, "memory_used_mb", None) or 0)
    temperature_c = int(getattr(gpu_entry, "temperature_c", None) or 0)
    gpu_index = int(getattr(gpu_entry, "gpu_index", 0))

    # gpu_hang — track util_zero streak per gpu.
    gstate = _gpu_state(state, server_id, gpu_index)
    if util_pct == 0:
        if gstate.util_zero_since_monotonic is None:
            gstate.util_zero_since_monotonic = time.monotonic()
    else:
        gstate.util_zero_since_monotonic = None
    # Phase 9 / FU-37 — gpu_hang threshold is now a dict carrying both
    # the util-zero duration and the memory floor.
    hang_policy = policy_cache.get(server_id, "gpu_hang")
    hang_payload = (
        hang_policy.threshold_value
        if hang_policy is not None and isinstance(hang_policy.threshold_value, dict)
        else {}
    )
    threshold_s = int(hang_payload.get("util_zero_seconds", _GPU_HANG_DEFAULT_UTIL_ZERO_S))
    mem_floor_mb = int(hang_payload.get("mem_floor_mb", _GPU_HANG_DEFAULT_MEM_FLOOR_MB))
    if (
        gstate.util_zero_since_monotonic is not None
        and memory_used_mb >= mem_floor_mb
        and time.monotonic() - gstate.util_zero_since_monotonic >= threshold_s
    ):
        out.append(
            Violation(
                policy_key="gpu_hang",
                server_id=server_id,
                gpu_id_local=gpu_index,
                linux_username=None,
                pid=None,
                payload={
                    "util_zero_for_s": int(time.monotonic() - gstate.util_zero_since_monotonic),
                    "memory_used_mb": memory_used_mb,
                    "mem_floor_mb": mem_floor_mb,
                    "threshold_value_s": threshold_s,
                },
            )
        )

    # gpu_temp_high — direct telemetry comparison; threshold is now
    # ``{value, unit}`` with ``unit == "celsius"``.
    temp_policy = policy_cache.get(server_id, "gpu_temp_high")
    temp_payload = (
        temp_policy.threshold_value
        if temp_policy is not None and isinstance(temp_policy.threshold_value, dict)
        else {}
    )
    temp_threshold = int(temp_payload.get("value", _GPU_TEMP_HIGH_DEFAULT_C))
    if temperature_c > temp_threshold:
        out.append(
            Violation(
                policy_key="gpu_temp_high",
                server_id=server_id,
                gpu_id_local=gpu_index,
                linux_username=None,
                pid=None,
                payload={
                    "temperature_c": temperature_c,
                    "threshold_c": temp_threshold,
                },
            )
        )

    return out


async def run_tick(
    *,
    server_id: int,
    telemetry: GpuTelemetry,
    dispatcher: Dispatcher,
    state: _MonitorState,
) -> int:
    """One pass: evaluate every GPU + every process and dispatch any
    violations. Returns the count of violations dispatched."""
    n = 0
    for gpu in telemetry.gpus:
        gpu_index = int(gpu.gpu_index)
        for proc in getattr(gpu, "processes", []) or []:
            violation = _evaluate_process(
                server_id=server_id,
                gpu_index=gpu_index,
                linux_username=str(proc.linux_username),
                pid=int(proc.pid),
                memory_used_mb=int(proc.memory_mb),
            )
            if violation is not None:
                try:
                    await dispatcher(violation)
                except Exception as exc:
                    log.warning(
                        "compliance.dispatch_failed",
                        policy_key=violation.policy_key,
                        error=str(exc),
                    )
                n += 1
        for v in _evaluate_gpu(server_id=server_id, gpu_entry=gpu, state=state):
            try:
                await dispatcher(v)
            except Exception as exc:
                log.warning(
                    "compliance.dispatch_failed",
                    policy_key=v.policy_key,
                    error=str(exc),
                )
            n += 1
    return n


class ComplianceMonitor:
    """Background loop that calls :func:`run_tick` every interval.

    Owns its own :class:`_MonitorState`. The loop exits when
    ``stop_event`` is set; it sleeps cancellation-safely so SIGTERM
    paths can shut down quickly.
    """

    def __init__(
        self,
        *,
        server_id: int,
        gpu_collector: GpuCollector,
        dispatcher: Dispatcher,
        tick_interval_seconds: float = DEFAULT_TICK_INTERVAL_S,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self._server_id = server_id
        self._gpu = gpu_collector
        self._dispatcher = dispatcher
        self._tick_interval = tick_interval_seconds
        self._stop_event = stop_event or asyncio.Event()
        self._state = _MonitorState()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    def request_stop(self) -> None:
        self._stop_event.set()

    async def join(self) -> None:
        if self._task is None:
            return
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def _run(self) -> None:
        log.info(
            "compliance.monitor.start",
            server_id=self._server_id,
            tick_interval_s=self._tick_interval,
        )
        while not self._stop_event.is_set():
            try:
                telemetry = await self._gpu.collect()
                n = await run_tick(
                    server_id=self._server_id,
                    telemetry=telemetry,
                    dispatcher=self._dispatcher,
                    state=self._state,
                )
                if n:
                    log.info("compliance.monitor.tick", violations=n)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("compliance.monitor.tick_failed", error=str(exc))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._tick_interval)
        log.info("compliance.monitor.stopped")
