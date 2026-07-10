"""In-process connection pool for agent WSS sessions.

A single backend instance is the only writer (single-leader, ADR-011);
the pool is just a ``{server_id: WebSocket}`` dict. Re-connecting an
already-online server closes the prior socket (close code 1008) so a
flapping agent doesn't fork the audit trail.
"""

from __future__ import annotations

import contextlib

from fastapi import WebSocket

from ..logging_setup import get_logger

_log = get_logger("corelab.agent_hub")


class AgentConnectionPool:
    def __init__(self) -> None:
        self._connections: dict[int, WebSocket] = {}

    async def register(self, server_id: int, ws: WebSocket) -> None:
        existing = self._connections.get(server_id)
        if existing is not None and existing is not ws:
            _log.info("agent_hub.duplicate_kick", server_id=server_id)
            # The old socket may already be torn down; ignore close errors.
            with contextlib.suppress(Exception):
                await existing.close(code=1008, reason="duplicate_connect")
        self._connections[server_id] = ws

    def unregister(self, server_id: int, ws: WebSocket) -> None:
        if self._connections.get(server_id) is ws:
            del self._connections[server_id]

    def get(self, server_id: int) -> WebSocket | None:
        return self._connections.get(server_id)

    def is_online(self, server_id: int) -> bool:
        return server_id in self._connections

    def all_server_ids(self) -> list[int]:
        return list(self._connections)


# Process-wide singleton (single backend instance per Phase 1 ADR-011).
pool = AgentConnectionPool()
