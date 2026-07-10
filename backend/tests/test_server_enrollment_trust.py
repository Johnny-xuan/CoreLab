"""Server enrollment trust boundary unit tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from corelab_backend.models import Server
from corelab_backend.services import telemetry_service
from corelab_protocol import GpuTelemetry, GpuTelemetryEntry


class FakeSession:
    def __init__(self, server: Server | None) -> None:
        self.server = server
        self.executed: list[Any] = []
        self.flushed = False

    async def get(self, model: type[Any], row_id: int) -> Any:
        assert model is Server
        if self.server is None or self.server.id != row_id:
            return None
        return self.server

    async def execute(self, stmt: Any) -> None:
        self.executed.append(stmt)

    async def flush(self) -> None:
        self.flushed = True


def _server(*, approved: bool, status: str = "pending") -> Server:
    return Server(
        id=7,
        lab_id=1,
        hostname="gpu-7",
        status=status,
        is_active=1,
        approved_at=datetime.now(UTC) if approved else None,
    )


@pytest.mark.asyncio
async def test_mark_heartbeat_keeps_unapproved_server_pending() -> None:
    server = _server(approved=False)
    session = FakeSession(server)

    approved = await telemetry_service.mark_heartbeat(
        session, server_id=server.id, agent_version="0.3.0"
    )

    assert approved is False
    assert server.status == "pending"
    assert server.agent_version == "0.3.0"
    assert server.last_heartbeat_at is not None
    assert session.flushed is True


@pytest.mark.asyncio
async def test_mark_heartbeat_marks_approved_server_online() -> None:
    server = _server(approved=True)
    session = FakeSession(server)

    approved = await telemetry_service.mark_heartbeat(
        session, server_id=server.id, agent_version="0.3.1"
    )

    assert approved is True
    assert server.status == "online"
    assert server.agent_version == "0.3.1"


@pytest.mark.asyncio
async def test_upsert_telemetry_ignores_unapproved_server() -> None:
    server = _server(approved=False)
    session = FakeSession(server)
    payload = GpuTelemetry(gpus=[GpuTelemetryEntry(gpu_index=0, util_pct=33)])

    accepted = await telemetry_service.upsert_telemetry(
        session, server_id=server.id, payload=payload
    )

    assert accepted is False
    assert session.executed == []
    assert session.flushed is False


@pytest.mark.asyncio
async def test_upsert_telemetry_accepts_approved_server() -> None:
    server = _server(approved=True)
    session = FakeSession(server)
    payload = GpuTelemetry(gpus=[GpuTelemetryEntry(gpu_index=0, util_pct=33)])

    accepted = await telemetry_service.upsert_telemetry(
        session, server_id=server.id, payload=payload
    )

    assert accepted is True
    assert len(session.executed) == 1
    assert session.flushed is True
