"""Broadcast ``backend.config.update_urls`` to in-process agents.

Phase M v5 M-6. Lightweight helper: format the frame, fire-and-forget
send it to whichever agents are currently registered in
``agent_hub.pool``. Failures (socket dead, race with disconnect) are
logged + swallowed — the agent will pick up the latest URL list on
next reconnect anyway because ``server_service`` reads from
``lab.public_urls`` for the install snippet and the URL probe
scheduler keeps the field fresh.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterable

from fastapi import WebSocket

from ..logging_setup import get_logger
from . import agent_hub, agent_rpc

_log = get_logger("corelab.agent_url_broadcast")


def _to_wss(url: str) -> str:
    """Translate user-facing HTTP(S) URL to agent WSS endpoint.

    ``lab.public_urls`` stores user-facing URLs (http:// or https://)
    because that's what gets displayed in the Public Access card and
    pasted into install snippets. But the agent's ``backend_urls``
    field expects WSS endpoints with the ``/ws/agent`` path appended,
    matching what ``install-agent.sh`` writes on first install. This
    helper closes that gap so update_urls broadcasts and the toml-
    persisted list stay in the WSS form the agent's ws_client can
    actually connect on.
    """
    u = url.rstrip("/")
    if u.startswith("https://"):
        u = "wss://" + u[len("https://") :]
    elif u.startswith("http://"):
        u = "ws://" + u[len("http://") :]
    # Already ws(s):// passes through unchanged.
    if not u.endswith("/ws/agent"):
        u = u + "/ws/agent"
    return u


def _build_frame(urls: list[str]) -> tuple[str, str]:
    """Serialise a backend.config.update_urls envelope ready for ws.send."""
    # Avoid the heavy MessageEnvelope round-trip: this is a hot path
    # called every time the admin twiddles the Public Access card and
    # has no correlation_id / RPC tracking. The schema is small and
    # well-known. Stays in sync with corelab_protocol.PROTOCOL_VERSION
    # because the frame names + payload shape are tested over there.
    from datetime import UTC, datetime
    from uuid import uuid4

    wss_urls = [_to_wss(u) for u in urls]
    frame_id = str(uuid4())
    env = {
        "type": "backend.config.update_urls",
        "id": frame_id,
        "ts": datetime.now(UTC).isoformat(),
        "payload": {"urls": wss_urls},
        "correlation_id": None,
    }
    return json.dumps(env), frame_id


async def _send_to_socket(ws: WebSocket, frame: str, frame_id: str, server_id: int) -> None:
    agent_rpc.expect_optional_response(
        correlation_id=frame_id,
        frame_type="backend.config.update_urls",
    )
    with contextlib.suppress(Exception):
        await ws.send_text(frame)
        _log.info("agent_url_broadcast.sent", server_id=server_id)


async def push_update_urls(*, server_id: int, urls: list[str]) -> bool:
    """Send the URL list to a single in-process agent connection."""
    ws = agent_hub.pool.get(server_id)
    if ws is None:
        return False
    frame, frame_id = _build_frame(urls)
    await _send_to_socket(ws, frame, frame_id, server_id)
    return True


async def broadcast_update_urls(*, server_ids: Iterable[int], urls: list[str]) -> int:
    """Push the URL list to every listed server_id currently online.

    Returns the count of agents actually reached. Useful from the
    Public Access / tunnel routers right after lab.public_urls changes.
    """
    if not urls:
        return 0
    sent = 0
    for sid in server_ids:
        ws = agent_hub.pool.get(sid)
        if ws is None:
            continue
        frame, frame_id = _build_frame(urls)
        await _send_to_socket(ws, frame, frame_id, sid)
        sent += 1
    return sent
