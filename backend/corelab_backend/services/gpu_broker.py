"""Phase 7 C9 — broker that fans GPU telemetry out to ``/ws/user``.

Background: ``/ws/agent`` (Phase 3) receives ``agent.gpu.telemetry``
once every 60 s in prod / 5 s in mock. The frontend wants a live
``gpu.live_update`` push but capped at 1 Hz per server so a busy
agent does not flood subscribers.

This module is the bridge — call :func:`fan_out` from the agent_ws
telemetry branch right after the DB upsert. It:

1. Throttles each server to at most one fan-out per second
   (``_throttle[server_id] = monotonic timestamp``).
2. Composes the docs/05 §4.3 ``gpu.live_update`` envelope.
3. Pushes to every user in ``ws_user.gpu_subscribers(server_id)``.

The throttle is per-process (ADR-011 — single backend leader). The
broker has zero coupling to the agent transport so a future RPC path
or a Prometheus exporter can call it the same way.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, Final
from uuid import uuid4

from corelab_protocol import GpuTelemetry

from ..api import ws_user
from ..logging_setup import get_logger

_log = get_logger("corelab.gpu_broker")

# Per-server throttle clock (monotonic seconds).
_throttle: Final[dict[int, float]] = {}

# 1 Hz per docs/05 §4.5 — agents may push faster but subscribers see
# at most one update per second. Tests override via ``set_min_interval``.
_min_interval_seconds: float = 1.0


def set_min_interval(seconds: float) -> None:
    """Test hook — relax / tighten the throttle window."""
    global _min_interval_seconds
    _min_interval_seconds = seconds


def reset_throttle() -> None:
    """Test hook — clear the per-server clock between scenarios."""
    _throttle.clear()


async def fan_out(*, server_id: int, payload: GpuTelemetry) -> int:
    """Push ``gpu.live_update`` to every subscriber of ``server_id``.

    Returns the number of frames delivered (0 if throttled or no
    subscribers). Callers can ignore the return; it's mostly for tests
    + observability.
    """
    now = time.monotonic()
    last = _throttle.get(server_id, 0.0)
    if now - last < _min_interval_seconds:
        return 0
    _throttle[server_id] = now

    subscribers = ws_user.gpu_subscribers(server_id)
    if not subscribers:
        return 0

    sampled_at = datetime.now(UTC).isoformat()
    frame: dict[str, Any] = {
        "type": "gpu.live_update",
        "id": str(uuid4()),
        "ts": sampled_at,
        "payload": {
            "server_id": server_id,
            "gpus": [g.model_dump(mode="json") for g in payload.gpus],
            "sampled_at": sampled_at,
        },
    }
    delivered = 0
    for user_id in subscribers:
        delivered += await ws_user.push_to_user(user_id, frame)
    return delivered
