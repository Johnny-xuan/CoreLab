"""Phase 8 C2 — push agent_policy state to the agent (P8-9).

Compose ``backend.policy.sync`` payload from the ``agent_policy`` rows
for a server, send via :mod:`agent_rpc`. Caller is responsible for
invoking this *after* the writing transaction has committed so the
agent never sees a row that the backend later rolls back.

Failure modes are non-fatal: ``AgentOfflineError`` warn-logs and
returns ``False`` — the next reconnect-time push will catch the agent
up. The contract is "push on change, but tolerate offline".
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logging_setup import get_logger
from ..models import AgentPolicy
from . import agent_hub, agent_rpc

_log = get_logger("corelab.policy_sync")


def _compute_etag(entries: list[dict[str, Any]]) -> str:
    """Stable hash over the policy list so the agent can short-circuit
    no-op syncs. Order is alphabetical by ``key`` to make the digest
    deterministic regardless of DB insertion order."""
    sorted_entries = sorted(entries, key=lambda e: e["key"])
    canonical = "|".join(
        # FU-37 — threshold_value is now a dict; serialise with
        # ``sort_keys`` so key insertion order in Python does not flip
        # the digest.
        f"{e['key']}:{e['enabled']}:{e['severity']}:"
        f"{json.dumps(e['threshold_value'], sort_keys=True)}:"
        f"{e['grace_period_seconds']}:{e['notify_admin']}"
        for e in sorted_entries
    )
    return "policy-" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


async def compose_payload(session: AsyncSession, *, server_id: int) -> dict[str, Any]:
    """Build the ``backend.policy.sync`` payload from the live rows."""
    result = await session.execute(
        select(AgentPolicy)
        .where(AgentPolicy.server_id == server_id)
        .order_by(AgentPolicy.policy_key)
    )
    rows = result.scalars().all()
    entries = [
        {
            "key": row.policy_key,
            "enabled": bool(row.enabled),
            "severity": row.severity,
            "threshold_value": row.threshold_value,
            "grace_period_seconds": row.grace_period_seconds,
            "notify_admin": bool(row.notify_admin),
        }
        for row in rows
    ]
    return {
        "server_id": server_id,
        "policies": entries,
        "etag": _compute_etag(entries),
    }


async def push_to_server(
    session: AsyncSession,
    *,
    server_id: int,
    timeout_seconds: float = 5.0,
) -> bool:
    """Push the policy snapshot to the agent. Returns True on agent ack.

    Tolerates ``AgentOfflineError`` (warn-log, return False) so a
    server that is currently disconnected does not block the policy
    update. The agent re-requests a full snapshot on its next connect.
    """
    payload = await compose_payload(session, server_id=server_id)
    try:
        result = await agent_rpc.request_response(
            server_id=server_id,
            frame_type="backend.policy.sync",
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
    except agent_rpc.AgentOfflineError:
        _log.info("policy_sync.agent_offline", server_id=server_id)
        return False
    except agent_rpc.AgentRpcTimeoutError as exc:
        _log.warning("policy_sync.timeout", server_id=server_id, error=str(exc))
        return False
    if not result.get("ok"):
        _log.warning(
            "policy_sync.rejected",
            server_id=server_id,
            error=result.get("error"),
        )
        return False
    return True


async def send_on_connect(session: AsyncSession, *, server_id: int) -> bool:
    """Push policy snapshot during agent connect without awaiting an RPC ack."""
    payload = await compose_payload(session, server_id=server_id)
    if not payload["policies"]:
        return False
    ws = agent_hub.pool.get(server_id)
    if ws is None:
        return False
    frame_id = str(uuid4())
    agent_rpc.expect_optional_response(
        correlation_id=frame_id,
        frame_type="backend.policy.sync",
    )
    frame = json.dumps(
        {
            "type": "backend.policy.sync",
            "id": frame_id,
            "ts": datetime.now(UTC).isoformat(),
            "payload": payload,
            "correlation_id": None,
        }
    )
    with contextlib.suppress(Exception):
        await ws.send_text(frame)
        _log.info("policy_sync.sent_on_connect", server_id=server_id)
        return True
    return False
