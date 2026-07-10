"""Phase 3 WSS roundtrip — enrollment, heartbeat, telemetry persist.

Originally landed in Phase 3 as a skipped test because FastAPI's sync
TestClient runs the ASGI app on its own anyio portal while our async
engine is cached per event-loop, so any DB call inside the WebSocket
handler hits a different loop than the one that opened the connection
(surfaces as CancelledError).

Phase 4 closes that FU-12 by spinning up an in-process uvicorn server on
a free port + using the real ``websockets`` client to drive the
roundtrip. The DB engine, the WebSocket handler, and the test all share
one event loop, which fixes the mismatch.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator

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


def _migration_url() -> str | None:
    url = os.environ.get("CORELAB_MIGRATION_DATABASE_URL", "")
    if not url or "127.0.0.1" not in url:
        return None
    return url


_WIPE_TABLES = (
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
async def ws_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[str, str, Engine]]:
    """Yield ``(http_base, ws_base, sync_engine)`` for an in-process uvicorn."""
    migration_url = _migration_url()
    if migration_url is None:
        pytest.skip(
            "phase 4 FU-12 WS test needs CORELAB_MIGRATION_DATABASE_URL pointing at 127.0.0.1"
        )
    monkeypatch.setenv("CORELAB_DATABASE_URL", migration_url)
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

    # Wait for uvicorn to bind a socket — it sets ``started`` then
    # populates ``servers`` with the bound asyncio.Server instances.
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


async def test_agent_enrollment_heartbeat_telemetry(
    ws_stack: tuple[str, str, Engine],
) -> None:
    http_base, ws_base, sync_engine = ws_stack

    async with httpx.AsyncClient(base_url=http_base) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={
                "username": "alice",
                "password": "AlicePass!2024",  # pragma: allowlist secret
            },
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        create = await client.post(
            "/api/v1/servers",
            json={"hostname": "ws-test", "display_name": "WS Test"},
            headers=headers,
        )
        assert create.status_code == 201, create.text
        body = create.json()
        server_id = body["server"]["id"]
        enrollment_token = body["enrollment_token"]

        ws_url = f"{ws_base}/ws/agent?token={enrollment_token}&server_id={server_id}"
        async with websockets.connect(ws_url) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "agent.heartbeat",
                        "id": "h-1",
                        "ts": "2026-06-04T00:00:00Z",
                        "payload": {"uptime_seconds": 5, "agent_version": "0.0.0"},
                    }
                )
            )
            await ws.send(
                json.dumps(
                    {
                        "type": "agent.gpu.telemetry",
                        "id": "t-1",
                        "ts": "2026-06-04T00:00:01Z",
                        "payload": {
                            "gpus": [
                                {
                                    "gpu_index": 0,
                                    "model": "RTX 4090",
                                    "memory_total_mb": 24576,
                                    "util_pct": 50,
                                    "memory_used_mb": 12288,
                                    "temperature_c": 60,
                                    "power_w": 200,
                                    "processes": [],
                                },
                                {
                                    "gpu_index": 1,
                                    "model": "RTX 4090",
                                    "memory_total_mb": 24576,
                                    "util_pct": 75,
                                    "memory_used_mb": 18432,
                                    "temperature_c": 70,
                                    "power_w": 280,
                                    "processes": [],
                                },
                            ]
                        },
                    }
                )
            )
            # Let the server-side receive loop commit both frames. The
            # pre-approval telemetry must be ignored: phone-home proves
            # token possession, not operational trust.
            await asyncio.sleep(0.5)

            with sync_engine.connect() as conn:
                pending_row = conn.execute(
                    text(
                        "SELECT status, agent_token_hash, last_heartbeat_at, approved_at "
                        "FROM server WHERE id = :sid"
                    ),
                    {"sid": server_id},
                ).one()
                assert pending_row.agent_token_hash is not None
                assert pending_row.last_heartbeat_at is not None
                assert pending_row.approved_at is None
                assert pending_row.status == "pending"
                pre_approval_gpus = conn.execute(
                    text("SELECT COUNT(*) FROM gpu WHERE server_id = :sid"),
                    {"sid": server_id},
                ).scalar_one()
                assert pre_approval_gpus == 0

            approve = await client.post(f"/api/v1/servers/{server_id}/approve", headers=headers)
            assert approve.status_code == 200, approve.text
            assert approve.json()["approved_at"] is not None

            await ws.send(
                json.dumps(
                    {
                        "type": "agent.gpu.telemetry",
                        "id": "t-2",
                        "ts": "2026-06-04T00:00:02Z",
                        "payload": {
                            "gpus": [
                                {
                                    "gpu_index": 0,
                                    "model": "RTX 4090",
                                    "memory_total_mb": 24576,
                                    "util_pct": 50,
                                    "memory_used_mb": 12288,
                                    "temperature_c": 60,
                                    "power_w": 200,
                                    "processes": [],
                                },
                                {
                                    "gpu_index": 1,
                                    "model": "RTX 4090",
                                    "memory_total_mb": 24576,
                                    "util_pct": 75,
                                    "memory_used_mb": 18432,
                                    "temperature_c": 70,
                                    "power_w": 280,
                                    "processes": [],
                                },
                            ]
                        },
                    }
                )
            )
            await asyncio.sleep(0.5)

        # Let the disconnect handler flip status + write the audit row.
        await asyncio.sleep(0.4)

    with sync_engine.connect() as conn:
        server_row = conn.execute(
            text(
                "SELECT status, agent_token_hash, last_heartbeat_at, approved_at "
                "FROM server WHERE id = :sid"
            ),
            {"sid": server_id},
        ).one()
        assert server_row.agent_token_hash is not None, "enrollment did not bcrypt the token"
        assert server_row.last_heartbeat_at is not None
        assert server_row.approved_at is not None
        assert server_row.status == "offline"

        gpus = conn.execute(
            text("SELECT gpu_index, util_pct FROM gpu WHERE server_id = :sid"),
            {"sid": server_id},
        ).all()
        assert {(g.gpu_index, g.util_pct) for g in gpus} == {(0, 50), (1, 75)}

        token_row = conn.execute(
            text("SELECT used_at FROM enrollment_token WHERE used_by_server_id = :sid"),
            {"sid": server_id},
        ).one()
        assert token_row.used_at is not None

        actions = {
            a
            for (a,) in conn.execute(
                text("SELECT action FROM audit_log WHERE target_server_id = :sid"),
                {"sid": server_id},
            ).all()
        }
        assert {"server.phone_home_pending_approval", "server.approve", "server.offline"} <= actions
        assert "server.online" not in actions
