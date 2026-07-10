"""Push per-server agent_capability state to the live agent.

The agent's local capability gate (corelab_agent.capabilities) starts
permissive and only reflects the real backend switches once this push
lands. ``gpu.kill_process`` is the one that matters most: the agent
defaults it OFF, so manual + auto kill stay inert until this sync
explicitly enables it.

Pushed on agent connect (so a fresh connection learns the truth) and
after any capability flip (so toggling the switch in the UI takes
effect on a running agent without a reconnect). One-way-ish RPC: the
agent acks; an offline agent is tolerated (it re-syncs on reconnect).
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logging_setup import get_logger
from ..models import AgentCapability
from . import agent_hub, agent_rpc

_log = get_logger("corelab.capability_sync")


async def compose_payload(session: AsyncSession, *, server_id: int) -> dict[str, Any]:
    rows = (
        (
            await session.execute(
                select(AgentCapability).where(AgentCapability.server_id == server_id)
            )
        )
        .scalars()
        .all()
    )
    return {
        "capabilities": [{"key": r.capability_key, "enabled": bool(r.is_enabled)} for r in rows]
    }


async def send_on_connect(session: AsyncSession, *, server_id: int) -> bool:
    """Fire-and-forget capability push for the agent connect handshake.

    During connect the backend's receive loop hasn't started yet, so an
    RPC that awaits the ack would deadlock until timeout. Mirror the URL
    broadcast: send the frame straight to the socket and let the agent's
    ack arrive later (harmlessly unmatched). The agent applies the
    capabilities on receipt.
    """
    payload = await compose_payload(session, server_id=server_id)
    if not payload["capabilities"]:
        return False
    ws = agent_hub.pool.get(server_id)
    if ws is None:
        return False
    frame_id = str(uuid4())
    agent_rpc.expect_optional_response(
        correlation_id=frame_id,
        frame_type="backend.capability.sync",
    )
    frame = json.dumps(
        {
            "type": "backend.capability.sync",
            "id": frame_id,
            "ts": datetime.now(UTC).isoformat(),
            "payload": payload,
            "correlation_id": None,
        }
    )
    with contextlib.suppress(Exception):
        await ws.send_text(frame)
        _log.info("capability_sync.sent_on_connect", server_id=server_id)
        return True
    return False


async def push_to_server(
    session: AsyncSession,
    *,
    server_id: int,
    timeout_seconds: float = 5.0,
) -> bool:
    """Push the capability snapshot to the agent. True on agent ack.

    Used from the capability-flip endpoint, where the backend is in a
    normal request context and the agent's receive loop is running, so
    the request/response RPC completes cleanly.
    """
    payload = await compose_payload(session, server_id=server_id)
    if not payload["capabilities"]:
        return False
    try:
        result = await agent_rpc.request_response(
            server_id=server_id,
            frame_type="backend.capability.sync",
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
    except agent_rpc.AgentOfflineError:
        _log.info("capability_sync.agent_offline", server_id=server_id)
        return False
    except agent_rpc.AgentRpcTimeoutError as exc:
        _log.warning("capability_sync.timeout", server_id=server_id, error=str(exc))
        return False
    if not result.get("ok"):
        _log.warning("capability_sync.rejected", server_id=server_id, error=result.get("error"))
        return False
    return True
