"""Phase 8 C2 — agent-side cache of agent_policy rows.

Backend pushes ``backend.policy.sync`` after enrollment + on any
policy change; agent stores the typed entries keyed by
``(server_id, policy_key)``. The compliance_monitor + policy_handlers
modules (C3 / C4) read from this cache to decide what to do when a
violation is detected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final


@dataclass(frozen=True)
class PolicyEntry:
    key: str
    enabled: bool
    severity: str  # "log_only" | "notify" | "warn" | "auto_kill"
    # Phase 9 / FU-37 — per-policy_key dict (see
    # backend.agent_policy_service.THRESHOLD_SCHEMAS for the shape):
    # gpu_hang -> {util_zero_seconds, mem_floor_mb}
    # memory_overuse / gpu_temp_high -> {value, unit}
    # all others -> None.
    threshold_value: dict[str, Any] | None
    grace_period_seconds: int | None
    notify_admin: bool


_cache: dict[tuple[int, str], PolicyEntry] = {}
_etag_by_server: dict[int, str] = {}

# docs/02 §5.18 line 1459-1474 — 8 policy_key 字面 (kept in sync with
# backend.agent_policy_service.POLICY_KEYS).
POLICY_KEYS: Final[tuple[str, ...]] = (
    "no_reservation_occupy",
    "preempt_others_reservation",
    "script_overrun_grace",
    "memory_overuse",
    "gpu_hang",
    "gpu_temp_high",
    "zombie_process",
    "unlinked_user_occupy",
)


def reset() -> None:
    _cache.clear()
    _etag_by_server.clear()


def apply_sync(server_id: int, policies: list[dict], etag: str) -> int:
    """Replace this server's policy entries. Returns count stored."""
    for key in list(_cache):
        if key[0] == server_id:
            del _cache[key]
    for entry in policies:
        _cache[(server_id, str(entry["key"]))] = PolicyEntry(
            key=str(entry["key"]),
            enabled=bool(entry["enabled"]),
            severity=str(entry["severity"]),
            threshold_value=entry.get("threshold_value"),
            grace_period_seconds=entry.get("grace_period_seconds"),
            notify_admin=bool(entry.get("notify_admin", True)),
        )
    _etag_by_server[server_id] = etag
    return sum(1 for k in _cache if k[0] == server_id)


def get(server_id: int, policy_key: str) -> PolicyEntry | None:
    return _cache.get((server_id, policy_key))


def etag(server_id: int) -> str | None:
    return _etag_by_server.get(server_id)


def entry_count(server_id: int | None = None) -> int:
    if server_id is None:
        return len(_cache)
    return sum(1 for k in _cache if k[0] == server_id)
