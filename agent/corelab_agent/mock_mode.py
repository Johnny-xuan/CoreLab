"""Mock mode for non-Linux dev.

Mac / Windows have no ``nvidia-smi`` and no ``systemd``; running the
real telemetry collector or RPC handlers on them is pointless. When
``mock_mode = true`` in the config, the agent stubs those code paths.

**Linux is warned loudly** because mock mode reaching production is
almost always a deployment bug (planner invariant #4): an operator
sees the agent connect + heartbeat just fine and only later notices
the GPU stats are pure fiction.
"""

from __future__ import annotations

import platform

from .logging_setup import get_logger


def warn_if_linux() -> None:
    """Emit a warning if mock mode is active on a Linux host.

    Called at startup; the agent still runs (we don't hard-fail because
    some test harnesses legitimately want this), but the warning is
    structured + persistent so monitoring picks it up.
    """
    if platform.system() == "Linux":
        log = get_logger("corelab.agent.mock")
        log.warning(
            "mock_mode.active_on_linux",
            msg=(
                "Mock mode is enabled on a Linux host. "
                "Real nvidia-smi / sudo handlers are NOT being used; "
                "GPU telemetry and RPC results are fake. "
                "If this is a production server, fix /etc/corelab-agent.toml."
            ),
            platform=platform.platform(),
        )


def fake_telemetry_snapshot() -> dict[str, object]:
    """Deterministic stub for a single nvidia-smi cycle.

    Phase 1 just provides a placeholder so future ws_client telemetry
    code has something to call without a real GPU.
    """
    return {
        "gpus": [
            {
                "gpu_index": 0,
                "model": "MOCK NVIDIA RTX 4090",
                "memory_total_mb": 24576,
                "memory_used_mb": 0,
                "util_pct": 0,
                "temperature_c": 35,
                "process_snapshot": [],
            }
        ]
    }
