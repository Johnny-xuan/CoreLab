"""Request/response RPC over the agent WSS hub.

Phase 4 C5 wires the real plumbing: requests register a Future under
the envelope id, the WS receive loop matches incoming RPC responses by
``correlation_id``, and the future resolves with the typed payload.
Timeout cancels the pending future cleanly. The connection pool itself
lives in :mod:`agent_hub`.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, cast

from corelab_protocol import RPC_REQUEST_TO_RESPONSE, MessageEnvelope, MessageType

from ..logging_setup import get_logger
from . import agent_hub

_log = get_logger("corelab.agent_rpc")


class AgentRpcError(Exception):
    pass


class RpcNotYetWiredError(AgentRpcError):
    """Reserved for future "RPC type not registered" failures; kept so
    tests that exercised the Phase 4 C2 stub keep working."""


class AgentOfflineError(AgentRpcError):
    """The target server has no live WSS connection in the pool."""


class AgentRpcTimeoutError(AgentRpcError):
    """The agent took too long to reply (configurable per-call)."""


class UnexpectedResponseTypeError(AgentRpcError):
    """A correlation_id matched but the response frame type was wrong."""


# Pending RPC correlations: ``{correlation_id: (expected_type, future)}``
# Module-global because there's a single backend leader (ADR-011).
_PENDING: dict[str, tuple[str, asyncio.Future[dict[str, Any]]]] = {}

# Optional fire-and-forget replies. Connect-time sync frames are sent before
# the receive loop is ready to await an RPC future, but the agent still replies
# with the normal typed ack. Register those ids so the receive loop can consume
# expected acks without warning about an unmatched RPC response.
_OPTIONAL_RESPONSES: dict[str, str] = {}
_MAX_OPTIONAL_RESPONSES = 1024


def expect_optional_response(*, correlation_id: str, frame_type: str) -> bool:
    """Register an expected reply for a fire-and-forget request frame."""
    expected_response = RPC_REQUEST_TO_RESPONSE.get(frame_type)
    if expected_response is None:
        return False
    _OPTIONAL_RESPONSES[correlation_id] = expected_response
    while len(_OPTIONAL_RESPONSES) > _MAX_OPTIONAL_RESPONSES:
        _OPTIONAL_RESPONSES.pop(next(iter(_OPTIONAL_RESPONSES)))
    return True


def deliver_response(
    *,
    correlation_id: str | None,
    frame_type: str,
    payload: dict[str, Any],
) -> bool:
    """Resolve a pending RPC future from the WS receive loop.

    Returns True if a pending entry was found and resolved; False
    otherwise (caller can ignore the frame or log a warning).
    """
    if correlation_id is None:
        return False
    entry = _PENDING.pop(correlation_id, None)
    if entry is None:
        expected_optional = _OPTIONAL_RESPONSES.pop(correlation_id, None)
        return expected_optional == frame_type
    expected_type, fut = entry
    if fut.done():
        return False
    if frame_type != expected_type:
        fut.set_exception(
            UnexpectedResponseTypeError(f"expected {expected_type!r}, got {frame_type!r}")
        )
        return True
    fut.set_result(payload)
    return True


async def request_response(
    *,
    server_id: int,
    frame_type: str,
    payload: dict[str, Any],
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Send ``frame_type`` to ``server_id`` and await the matching reply.

    Raises ``AgentOfflineError`` if the server isn't connected,
    ``AgentRpcTimeoutError`` if the agent doesn't reply in time, and
    ``UnexpectedResponseTypeError`` if it replies with the wrong shape.
    """
    expected_response = RPC_REQUEST_TO_RESPONSE.get(frame_type)
    if expected_response is None:
        raise RpcNotYetWiredError(f"frame type {frame_type!r} is not a recognised RPC request")

    ws = agent_hub.pool.get(server_id)
    if ws is None:
        raise AgentOfflineError(f"no live agent connection for server {server_id}")

    envelope = MessageEnvelope(type=cast(MessageType, frame_type), payload=payload)
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[dict[str, Any]] = loop.create_future()
    _PENDING[envelope.id] = (expected_response, fut)
    try:
        await ws.send_text(envelope.model_dump_json())
        try:
            return await asyncio.wait_for(fut, timeout=timeout_seconds)
        except TimeoutError as exc:
            raise AgentRpcTimeoutError(
                f"agent {server_id} did not reply within {timeout_seconds}s"
            ) from exc
    finally:
        # Drop the pending entry if it's still there (timeout / send fail).
        _PENDING.pop(envelope.id, None)
        if not fut.done():
            with contextlib.suppress(asyncio.InvalidStateError):
                fut.cancel()
            _log.debug("agent_rpc.future_cancelled", correlation_id=envelope.id)
