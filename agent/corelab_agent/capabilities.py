"""Local capability cache.

Mirrors the backend's per-server ``agent_capability`` rows so RPC
handlers can reject calls that would breach a disabled capability
before they ever touch a subprocess (docs/04-security.md §12).

Phase 4 ships the lookup surface with a permissive default — the
backend → agent ``rpc.capability_sync`` push that flips entries to
their real backend state lands in Phase 8 alongside the compliance
monitor. Until then individual entries can be force-disabled by tests
or by an operator's local override file (out of scope for v0.1).
"""

from __future__ import annotations


class CapabilityDisabledError(Exception):
    """Caller asked the agent to do something the local cache says it can't."""


_cache: dict[str, bool] = {}


def is_enabled(key: str, *, default: bool = True) -> bool:
    """Return whether a capability is currently enabled on this agent."""
    return _cache.get(key, default)


def set_enabled(key: str, enabled: bool) -> None:
    """Used by tests + by the future capability_sync RPC handler."""
    _cache[key] = enabled


def clear() -> None:
    """Reset the cache — for test isolation only."""
    _cache.clear()


def require_enabled(key: str) -> None:
    """Raise ``CapabilityDisabledError`` if ``key`` is disabled."""
    if not is_enabled(key):
        raise CapabilityDisabledError(f"capability {key!r} is disabled on this agent")
