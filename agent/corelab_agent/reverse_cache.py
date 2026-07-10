"""Phase 8 C2 — agent-side reverse-lookup cache (Worker Catch #1).

Backend pushes ``backend.account_link_cache.sync`` (full or
incremental); we store the result keyed by ``(server_id,
linux_username)`` so the 60s compliance_monitor tick (added in C3)
can resolve "who owns this Linux account + do they have an active
reservation on this GPU?" without ever crossing WSS back.

The cache also tracks a coarse ``last_full_sync_monotonic`` timestamp
so the agent can request a fresh full sync if it's stale (TTL 5min
per docs/04 §9.7.2).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Final


@dataclass(frozen=True)
class ActiveReservation:
    reservation_id: int
    gpu_id: int
    start_at: str  # ISO-8601 UTC
    end_at: str  # ISO-8601 UTC
    status: str  # "scheduled" | "active"
    gpu_memory_mb: int | None
    gpu_compute_share_pct: int | None
    source: str  # account_link.source — admin_declared excluded from act-as


@dataclass
class CacheEntry:
    user_ids: list[int]
    # JSON object key is ``str(user_id)`` — convert to int when materialising.
    active_reservations: dict[int, list[ActiveReservation]] = field(default_factory=dict)


# Module-level state — single agent process per server, ADR-011-style.
_cache: dict[tuple[int, str], CacheEntry] = {}
_last_full_sync_monotonic: float = 0.0

CACHE_TTL_SECONDS: Final[int] = 300


def reset() -> None:
    """Test hook — wipe everything."""
    global _last_full_sync_monotonic
    _cache.clear()
    _last_full_sync_monotonic = 0.0


def _parse_active_reservations(
    raw: dict[str, list[dict]],
) -> dict[int, list[ActiveReservation]]:
    out: dict[int, list[ActiveReservation]] = {}
    for user_id_str, items in raw.items():
        try:
            user_id = int(user_id_str)
        except (TypeError, ValueError):
            continue
        out[user_id] = [
            ActiveReservation(
                reservation_id=int(item["reservation_id"]),
                gpu_id=int(item["gpu_id"]),
                start_at=str(item["start_at"]),
                end_at=str(item["end_at"]),
                status=str(item["status"]),
                gpu_memory_mb=item.get("gpu_memory_mb"),
                gpu_compute_share_pct=item.get("gpu_compute_share_pct"),
                source=str(item["source"]),
            )
            for item in items
        ]
    return out


def apply_full_snapshot(server_id: int, entries: list[dict]) -> int:
    """Replace this server's slice of the cache with ``entries``.

    Returns the number of (server_id, linux_username) keys now cached.
    """
    global _last_full_sync_monotonic
    # Drop existing entries for this server.
    for key in [k for k in _cache if k[0] == server_id]:
        del _cache[key]
    for entry in entries:
        linux_username = str(entry["linux_username"])
        _cache[(server_id, linux_username)] = CacheEntry(
            user_ids=list(entry.get("user_ids", [])),
            active_reservations=_parse_active_reservations(
                dict(entry.get("active_reservations") or {})
            ),
        )
    _last_full_sync_monotonic = time.monotonic()
    return sum(1 for k in _cache if k[0] == server_id)


def apply_incremental(
    server_id: int,
    entries: list[dict],
    removed_linux_usernames: list[str] | None = None,
) -> int:
    """Upsert entries + remove the listed usernames. Returns total entry
    count for the server after applying."""
    for entry in entries:
        linux_username = str(entry["linux_username"])
        _cache[(server_id, linux_username)] = CacheEntry(
            user_ids=list(entry.get("user_ids", [])),
            active_reservations=_parse_active_reservations(
                dict(entry.get("active_reservations") or {})
            ),
        )
    for removed in removed_linux_usernames or []:
        _cache.pop((server_id, str(removed)), None)
    return sum(1 for k in _cache if k[0] == server_id)


def lookup_user_ids(server_id: int, linux_username: str) -> list[int]:
    entry = _cache.get((server_id, linux_username))
    if entry is None:
        return []
    return list(entry.user_ids)


def lookup_active_reservations(
    server_id: int, linux_username: str, user_id: int
) -> list[ActiveReservation]:
    entry = _cache.get((server_id, linux_username))
    if entry is None:
        return []
    return list(entry.active_reservations.get(user_id, []))


def needs_full_sync() -> bool:
    """First connect or TTL expiry — agent should request a fresh
    snapshot."""
    if _last_full_sync_monotonic == 0.0:
        return True
    return (time.monotonic() - _last_full_sync_monotonic) > CACHE_TTL_SECONDS


def last_full_sync_monotonic() -> float:
    return _last_full_sync_monotonic


def entry_count(server_id: int | None = None) -> int:
    if server_id is None:
        return len(_cache)
    return sum(1 for k in _cache if k[0] == server_id)
