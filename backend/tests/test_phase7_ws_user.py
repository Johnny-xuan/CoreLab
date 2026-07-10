"""Phase 7 C2 — /ws/user JWT + heartbeat tests.

P7-4 gate 7 (jwt invalid → close 1008) and gate 8 (heartbeat timeout
→ close 1001). Reuses the Phase 3 / FU-12 in-process uvicorn pattern
so the WebSocket handler shares the event loop with the test client.

The test overrides ``CORELAB_WS_USER_HEARTBEAT_SECONDS`` via env so
the 30 s production default does not stall the suite.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
import uvicorn
import websockets
from corelab_backend.config import get_settings
from corelab_backend.db import get_engine, get_session_factory
from corelab_backend.main import create_app
from corelab_backend.security import hash_password
from sqlalchemy import Engine, create_engine, text

pytestmark = pytest.mark.asyncio


def _migration_url() -> str | None:
    url = os.environ.get("CORELAB_MIGRATION_DATABASE_URL", "")
    if not url or "127.0.0.1" not in url:
        return None
    return url


_WIPE_TABLES = (
    "reservation",
    "account_link_request",
    "account_link",
    "authorized_key_entry",
    "physical_account",
    "agent_capability",
    "gpu",
    "server_admin_grant",
    "enrollment_token",
    "server",
    "audit_log",
    "ssh_public_key",
    "setup_token",
    "registration_invite",
    "user",
    "lab",
)


@pytest_asyncio.fixture
async def ws_user_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[str, str, Engine]]:
    """``(http_base, ws_base, sync_engine)`` for an in-process uvicorn
    with the heartbeat dialled down so the timeout test fires quickly."""
    migration_url = _migration_url()
    if migration_url is None:
        pytest.skip("ws_user tests need CORELAB_MIGRATION_DATABASE_URL pointing at 127.0.0.1")
    monkeypatch.setenv("CORELAB_DATABASE_URL", migration_url)
    monkeypatch.setenv("CORELAB_WS_USER_HEARTBEAT_SECONDS", "2")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    sync_engine = create_engine(migration_url.replace("+asyncmy", "+pymysql"))
    with sync_engine.begin() as conn:
        for table in _WIPE_TABLES:
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("INSERT INTO lab (id, name, slug) VALUES (1, 'Test Lab', 'test')"))
        conn.execute(
            text(
                "INSERT INTO user (id, lab_id, username, email, display_name, role, "
                "password_hash, is_active) VALUES (1, 1, 'alice', 'a@x.com', 'Alice', "
                "'lab_admin', :pw, 1)"
            ),
            {"pw": hash_password("AlicePass!2024")},
        )

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())

    for _ in range(200):
        if server.started and server.servers:
            break
        await asyncio.sleep(0.02)
    else:
        server.should_exit = True
        await serve_task
        raise RuntimeError("uvicorn failed to start within 4s")

    sock = next(iter(server.servers[0].sockets))
    port = sock.getsockname()[1]
    http_base = f"http://127.0.0.1:{port}"
    ws_base = f"ws://127.0.0.1:{port}"

    try:
        yield http_base, ws_base, sync_engine
    finally:
        server.should_exit = True
        await serve_task
        sync_engine.dispose()


async def _login(http_base: str) -> str:
    async with httpx.AsyncClient(base_url=http_base) as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "AlicePass!2024"},  # pragma: allowlist secret
        )
        assert resp.status_code == 200, resp.text
        return str(resp.json()["access_token"])


async def test_ws_user_jwt_invalid_closes_1008(
    ws_user_stack: tuple[str, str, Engine],
) -> None:
    """Bad JWT → handler closes 1008 policy_violation (P7-4)."""
    _, ws_base, _ = ws_user_stack
    ws_url = f"{ws_base}/ws/user?token=not-a-jwt"
    with pytest.raises(websockets.ConnectionClosed) as exc_info:
        async with websockets.connect(ws_url) as ws:
            await ws.recv()
    assert exc_info.value.code == 1008


async def test_ws_user_ping_roundtrip_and_subscribe(
    ws_user_stack: tuple[str, str, Engine],
) -> None:
    """Happy path — ping → pong + subscribe lands in the registry."""
    http_base, ws_base, _ = ws_user_stack
    from corelab_backend.api import ws_user as hub

    access_token = await _login(http_base)
    ws_url = f"{ws_base}/ws/user?token={access_token}"
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"type": "ping", "payload": {"seq": 1}}))
        pong: dict[str, Any] = json.loads(await ws.recv())
        assert pong["type"] == "pong"
        assert pong["payload"]["seq"] == 1
        # Subscribe — registry should hold the user id.
        await ws.send(json.dumps({"type": "subscribe", "payload": {"server_id": 99}}))
        # Subscribe is fire-and-forget; give the loop one tick.
        await asyncio.sleep(0.1)
        assert 1 in hub.gpu_subscribers(99)
        await ws.send(json.dumps({"type": "unsubscribe", "payload": {"server_id": 99}}))
        await asyncio.sleep(0.1)
        assert 1 not in hub.gpu_subscribers(99)


async def test_ws_user_heartbeat_timeout_closes_1001(
    ws_user_stack: tuple[str, str, Engine],
) -> None:
    """No ping within 2x heartbeat window → handler closes 1001 (P7-4)."""
    http_base, ws_base, _ = ws_user_stack
    access_token = await _login(http_base)
    ws_url = f"{ws_base}/ws/user?token={access_token}"
    # Heartbeat = 2 s (fixture override), grace = 2x = 4 s, so silence
    # of ~5 s is past the cliff. We never send anything from the client.
    async with websockets.connect(ws_url) as ws:
        with pytest.raises(websockets.ConnectionClosed) as exc_info:
            await asyncio.wait_for(ws.recv(), timeout=8.0)
        assert exc_info.value.code == 1001
