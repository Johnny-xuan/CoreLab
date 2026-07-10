"""Phase 8 C3 — typed compliance violation + dispatcher protocol.

The :class:`Violation` dataclass is what the compliance_monitor
produces; the C4 :class:`PolicyDispatcher` consumes them and routes
to the right severity action (log_only / notify / warn / auto_kill).

Keeping these in a dedicated module avoids a circular import between
``compliance_monitor`` (producer) and ``policy_handlers`` (consumer,
added in C4).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Violation:
    policy_key: str
    server_id: int
    gpu_id_local: int  # nvidia gpu_index (NOT the backend gpu.id)
    linux_username: str | None
    pid: int | None
    linked_user_ids: list[int] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


# A dispatcher takes a violation, decides the action (per cached
# policy entry), and returns when the action has been started.
# Returning the actually-applied severity lets the monitor surface
# "downgraded" info to the audit trail (P8-7 capability x policy).
Dispatcher = Callable[[Violation], Awaitable[str]]
