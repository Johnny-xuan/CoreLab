"""Agent configuration — loaded from a TOML file.

In production the file lives at ``/etc/corelab-agent.toml`` (mode 0600,
owner ``corelab-agent``). For dev / tests, point ``--config`` at any
path. Schema is validated by pydantic so syntax / missing-field errors
surface immediately at startup.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_CONFIG_PATH = Path("/etc/corelab-agent.toml")


class MockGpuSpec(BaseModel):
    """One fake GPU for the demo/simulation collector.

    Lets a mock agent report a GPU topology that matches what the backend
    already knows for this server (model, memory, count) instead of the
    hardcoded 2x4090 default — so live telemetry lands cleanly on the
    seeded ``(server_id, gpu_index)`` rows rather than turning the
    dashboard into a mismatch of phantom cards.
    """

    model_config = ConfigDict(extra="forbid")

    model: str = Field(description="GPU model string, e.g. 'NVIDIA A100 80GB PCIe'.")
    memory_total_mb: int = Field(default=24576, ge=1)
    compute_capability: str = Field(default="8.9")
    # 0 = idle (oscillates near 0); >0 = under load (oscillates ±10 around
    # this center) so some cards visibly look busy and some free.
    base_util: int = Field(default=0, ge=0, le=100)
    # When set + the card is under load, report one fake compute process
    # owned by this Linux user so "someone is training here" reads true.
    process_user: str | None = Field(default=None)


class MockAccountSpec(BaseModel):
    """One fake Linux account the demo agent reports in its passwd scan.

    The launcher feeds the server's real seeded physical accounts here so
    the periodic scan *confirms* existing accounts (refreshing
    ``last_seen_at`` and backfilling uid) instead of inventing strangers
    on every box.
    """

    model_config = ConfigDict(extra="forbid")

    linux_username: str = Field(min_length=1, max_length=32)
    uid: int = Field(default=1000, ge=0)
    gid: int = Field(default=1000, ge=0)
    home_directory: str | None = Field(default=None)
    default_shell: str | None = Field(default="/bin/bash")


class AgentConfig(BaseModel):
    """Strictly validated agent configuration."""

    model_config = ConfigDict(extra="forbid")

    # ── Connection to backend ──────────────────────────────────────
    # Phase M v5 — multi-URL list is the canonical form; ``backend_url``
    # is a back-compat shim that, when present without ``backend_urls``,
    # is treated as a single-element list. New installs write
    # ``backend_urls`` directly. The model_validator collapses one form
    # into the other so the rest of the agent sees only ``backend_urls``.
    backend_url: str | None = Field(
        default=None,
        description="(deprecated) WSS endpoint — folded into backend_urls on load.",
    )
    backend_urls: list[str] = Field(
        default_factory=list,
        description="Ordered list of WSS endpoints. Agent tries each in turn.",
    )
    enrollment_token: str | None = Field(
        default=None,
        description="One-shot enroll token; present only before first phone-home succeeds.",
    )
    agent_token: str | None = Field(
        default=None,
        description="Long-lived per-server token granted by backend after enroll.",
    )

    @model_validator(mode="after")
    def _coalesce_urls(self) -> AgentConfig:
        if not self.backend_urls and self.backend_url:
            object.__setattr__(self, "backend_urls", [self.backend_url])
        if not self.backend_urls:
            raise ValueError("agent config must define either backend_url or backend_urls")
        return self

    # ── Local identity ────────────────────────────────────────────
    server_id: int | None = Field(
        default=None,
        ge=1,
        description="Server ID assigned by backend after enrollment; None pre-enroll.",
    )

    # ── Runtime knobs ─────────────────────────────────────────────
    mock_mode: bool = Field(
        default=False,
        description="Stub nvidia-smi with deterministic fake data (dev only; Linux warns).",
    )
    log_level: str = Field(default="INFO", description="DEBUG/INFO/WARNING/ERROR")
    log_json: bool = Field(default=True, description="Structured JSON logs to stderr.")

    # Reconnect backoff schedule — used by ws_client.
    reconnect_initial_seconds: float = Field(default=1.0, ge=0.1)
    reconnect_max_seconds: float = Field(default=30.0, ge=1.0)

    # Account discovery rescan cadence. A scan always runs on connect;
    # this loop catches useradd/userdel done while the connection stays
    # up. Only changed snapshots are re-sent.
    account_scan_interval_seconds: float = Field(default=3600.0, ge=10.0)

    # Governance loop. The monitor evaluates the latest local GPU telemetry
    # against policy/link caches pushed by the backend.
    compliance_monitor_enabled: bool = Field(default=True)
    compliance_monitor_interval_seconds: float = Field(default=60.0, ge=5.0)

    # ── Mock / demo simulation ────────────────────────────────────
    # Only consulted when mock_mode is true. Empty mock_gpus falls back
    # to the collector's built-in 2x4090 default (back-compat). Populate
    # both from scripts/demo_agents.py to mirror a seeded server exactly.
    mock_gpus: list[MockGpuSpec] = Field(default_factory=list)
    mock_accounts: list[MockAccountSpec] = Field(default_factory=list)


def load_config(path: Path | None = None) -> AgentConfig:
    """Read and validate a TOML config file."""
    resolved = path or DEFAULT_CONFIG_PATH
    with resolved.open("rb") as f:
        raw = tomllib.load(f)
    return AgentConfig.model_validate(raw)
