"""Phase 8 C4 — grace_period tracker.

When a policy has ``grace_period_seconds > 0`` we don't act on the
first violation tick; we start a timer keyed by
``(policy_key, server_id, gpu_id, linux_username, pid)`` and only
escalate to the configured severity when the violation has persisted
for at least the grace window.

Calling :func:`record` returns ``True`` iff the violation has now
been observed for ``grace_period_seconds`` continuously — the caller
should take the configured action and may then :func:`clear` the
timer to avoid acting again on the next tick.

Calling :func:`clear_if_resolved` (or letting the timer time out
naturally with no further :func:`record` call) drops the entry.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class _Pending:
    first_seen_monotonic: float
    last_seen_monotonic: float


# Keyed by the violation signature.
_pending: dict[tuple, _Pending] = {}


def _key(
    *,
    policy_key: str,
    server_id: int,
    gpu_id: int | None,
    linux_username: str | None,
    pid: int | None,
) -> tuple:
    return (policy_key, server_id, gpu_id, linux_username, pid)


def record(
    *,
    policy_key: str,
    server_id: int,
    gpu_id: int | None,
    linux_username: str | None,
    pid: int | None,
    grace_period_seconds: int | None,
) -> bool:
    """Returns True iff the violation has persisted past the grace
    window. ``grace_period_seconds`` of 0 / None means "act immediately"
    — this returns True on the first call."""
    if not grace_period_seconds:
        return True
    k = _key(
        policy_key=policy_key,
        server_id=server_id,
        gpu_id=gpu_id,
        linux_username=linux_username,
        pid=pid,
    )
    now = time.monotonic()
    pending = _pending.get(k)
    if pending is None:
        _pending[k] = _Pending(first_seen_monotonic=now, last_seen_monotonic=now)
        return False
    pending.last_seen_monotonic = now
    return (now - pending.first_seen_monotonic) >= grace_period_seconds


def clear(
    *,
    policy_key: str,
    server_id: int,
    gpu_id: int | None,
    linux_username: str | None,
    pid: int | None,
) -> None:
    k = _key(
        policy_key=policy_key,
        server_id=server_id,
        gpu_id=gpu_id,
        linux_username=linux_username,
        pid=pid,
    )
    _pending.pop(k, None)


def reset() -> None:
    _pending.clear()


def pending_count() -> int:
    return len(_pending)
