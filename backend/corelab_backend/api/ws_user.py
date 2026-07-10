"""``/ws/user`` — user-facing WebSocket hub.

Phase 7 C2 (FU-13 / docs/05 §4). One connection per browser tab; one
user can have several concurrent connections so all of them receive
pushed events. The router itself is thin — the module-level registries
(``_user_connections`` for fan-out, ``_gpu_subscribers`` for opt-in
``gpu.live_update`` flow) are the public surface used by
``notification_service`` and ``gpu_broker``.

Connection lifecycle (docs/05 §4):

1. Accept ``GET /ws/user?token=<jwt>`` after :func:`decode_access_token`
   succeeds; close 1008 otherwise.
2. Register the websocket in ``_user_connections[user_id]``.
3. Heartbeat: each ``receive_json`` is wrapped in
   ``asyncio.wait_for(..., heartbeat_seconds * 2)``. A miss closes 1001
   ``going_away`` (the brief P7-4 gate). The "* 2" covers the 30 s ping
   interval + a 30 s grace window.
4. Dispatch client-send frames:
   * ``ping`` → reply ``pong`` (echoes ``payload``).
   * ``subscribe`` → add the user to ``_gpu_subscribers[server_id]``.
   * ``unsubscribe`` → remove.
5. Cleanup on disconnect / close.

Server-push frames are not type-checked at the hub level. Callers
(``notification_service`` / ``gpu_broker``) compose the envelope per
docs/05 §4.3 and call :func:`push_to_user`.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any, Final
from uuid import uuid4

import jwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from ..config import get_settings
from ..logging_setup import get_logger
from ..security import decode_access_token

_log = get_logger("corelab.ws_user")

router = APIRouter()


# Module-level registries. Single-backend-process model (ADR-011) so a
# dict + sets is enough; no Redis fan-out needed at this scale.
_user_connections: Final[dict[int, set[WebSocket]]] = {}
_gpu_subscribers: Final[dict[int, set[int]]] = {}  # server_id → user_id set


# Client → server frame types. Anything else is dropped + logged.
_CLIENT_FRAME_TYPES: Final[frozenset[str]] = frozenset({"ping", "subscribe", "unsubscribe"})


def _register_connection(user_id: int, ws: WebSocket) -> None:
    _user_connections.setdefault(user_id, set()).add(ws)


def _unregister_connection(user_id: int, ws: WebSocket) -> None:
    sockets = _user_connections.get(user_id)
    if sockets is None:
        return
    sockets.discard(ws)
    if not sockets:
        _user_connections.pop(user_id, None)
    # Also drop the user from any gpu subscriptions that referenced this
    # connection — subscriptions are per-user, not per-socket, so we only
    # remove the user once their last socket is gone.
    if user_id not in _user_connections:
        for subs in _gpu_subscribers.values():
            subs.discard(user_id)


async def push_to_user(user_id: int, frame: dict[str, Any]) -> int:
    """Send ``frame`` to every live websocket for ``user_id``.

    Returns the count of sockets that received the frame. Dead sockets
    are silently discarded (the disconnect handler cleans them up).
    The caller is responsible for composing the envelope per docs/05 §4.2
    (``{type, id, ts, payload}``).
    """
    sockets = _user_connections.get(user_id, set())
    if not sockets:
        return 0
    delivered = 0
    for ws in list(sockets):
        try:
            await ws.send_json(frame)
            delivered += 1
        except (WebSocketDisconnect, RuntimeError):
            sockets.discard(ws)
    return delivered


def add_gpu_subscriber(server_id: int, user_id: int) -> None:
    """Opt the user into ``gpu.live_update`` fan-out for ``server_id``."""
    _gpu_subscribers.setdefault(server_id, set()).add(user_id)


def remove_gpu_subscriber(server_id: int, user_id: int) -> None:
    subs = _gpu_subscribers.get(server_id)
    if subs is None:
        return
    subs.discard(user_id)
    if not subs:
        _gpu_subscribers.pop(server_id, None)


def gpu_subscribers(server_id: int) -> frozenset[int]:
    """Read-only snapshot of subscribers for ``server_id`` (used by gpu_broker)."""
    return frozenset(_gpu_subscribers.get(server_id, set()))


def _make_envelope(*, type_: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": type_,
        "id": str(uuid4()),
        "ts": datetime.now(UTC).isoformat(),
        "payload": payload,
    }


async def _handle_client_frame(ws: WebSocket, *, user_id: int, frame: dict[str, Any]) -> None:
    frame_type = frame.get("type")
    payload = frame.get("payload") or {}
    if frame_type == "ping":
        await ws.send_json(_make_envelope(type_="pong", payload=payload))
        return
    if frame_type == "subscribe":
        server_id = payload.get("server_id")
        if isinstance(server_id, int):
            add_gpu_subscriber(server_id, user_id)
        else:
            _log.info("ws_user.subscribe.bad_payload", user_id=user_id, payload=payload)
        return
    if frame_type == "unsubscribe":
        server_id = payload.get("server_id")
        if isinstance(server_id, int):
            remove_gpu_subscriber(server_id, user_id)
        return
    _log.info("ws_user.unknown_frame", user_id=user_id, frame_type=frame_type)


@router.websocket("/ws/user")
async def ws_user_endpoint(websocket: WebSocket, token: str = Query(...)) -> None:
    """JWT-gated WSS connection for browser clients."""
    try:
        claims = decode_access_token(token)
    except jwt.PyJWTError as exc:
        _log.info("ws_user.jwt_invalid", error=str(exc))
        # Accept first so the close code is delivered as a proper WS
        # close frame (1008 policy_violation) rather than an HTTP 403
        # handshake rejection that browsers cannot inspect.
        await websocket.accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user_id = int(claims["sub"])
    await websocket.accept()
    _register_connection(user_id, websocket)
    settings = get_settings()
    # docs/05 §4.5 — browser sends ping every 30 s; we close 1001 if no
    # ping arrived inside the window + an equal grace period.
    timeout_seconds = max(2, settings.ws_user_heartbeat_seconds * 2)
    try:
        while True:
            try:
                frame = await asyncio.wait_for(websocket.receive_json(), timeout=timeout_seconds)
            except TimeoutError:
                _log.info("ws_user.heartbeat_timeout", user_id=user_id)
                with contextlib.suppress(RuntimeError):
                    await websocket.close(code=status.WS_1001_GOING_AWAY)
                return
            except WebSocketDisconnect:
                return
            if not isinstance(frame, dict):
                continue
            frame_type = frame.get("type")
            if frame_type not in _CLIENT_FRAME_TYPES:
                _log.info(
                    "ws_user.unknown_frame",
                    user_id=user_id,
                    frame_type=frame_type,
                )
                continue
            await _handle_client_frame(websocket, user_id=user_id, frame=frame)
    finally:
        _unregister_connection(user_id, websocket)
