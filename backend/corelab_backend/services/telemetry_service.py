"""GPU telemetry upsert + agent heartbeat bookkeeping.

heartbeat / telemetry writes are explicitly NOT audited (invariant #10):
the rate is too high and would drown out interesting events. Status
transitions (server.online / server.offline) are audited from the hub.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime

import bcrypt
from corelab_protocol import GpuTelemetry
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import EnrollmentToken, Gpu, Server
from ..security import hash_setup_token
from ._serializable_retry import with_serializable_retry


class AgentAuthError(Exception):
    """Token did not match server / enrollment_token row."""


async def authenticate_agent(
    session: AsyncSession, *, server_id: int, plaintext_token: str
) -> Server:
    """Verify the plaintext token against either the persisted agent
    token hash or an unused enrollment_token. On first successful
    enrollment, bcrypts the token into ``server.agent_token_hash`` and
    marks the enrollment_token consumed."""
    server = await session.get(Server, server_id)
    if server is None or not server.is_active:
        raise AgentAuthError(f"server {server_id} not found or disabled")

    if server.agent_token_hash is not None:
        if not bcrypt.checkpw(plaintext_token.encode(), server.agent_token_hash.encode("ascii")):
            raise AgentAuthError("agent_token mismatch")
        return server

    # First connection: validate against enrollment_token and bind it.
    hashed = hash_setup_token(plaintext_token)
    result = await session.execute(
        select(EnrollmentToken).where(
            EnrollmentToken.token_hash == hashed,
            EnrollmentToken.lab_id == server.lab_id,
        )
    )
    token = result.scalar_one_or_none()
    if token is None:
        raise AgentAuthError("enrollment_token not found")
    if token.used_at is not None and token.used_by_server_id != server_id:
        raise AgentAuthError("enrollment_token already used")
    if token.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
        raise AgentAuthError("enrollment_token expired")

    salt = bcrypt.gensalt(rounds=12)
    server.agent_token_hash = bcrypt.hashpw(plaintext_token.encode(), salt).decode("ascii")
    token.used_at = datetime.now(UTC)
    token.used_by_server_id = server_id
    await session.flush()
    return server


async def mark_heartbeat(
    session: AsyncSession, *, server_id: int, agent_version: str | None
) -> bool:
    """Record heartbeat context and return whether the server is approved.

    A valid enrollment token proves the agent reached the backend, but
    it is not the trust transition. Unapproved servers remain pending
    until a lab admin approves them.
    """
    server = await session.get(Server, server_id)
    if server is None:
        return False
    server.last_heartbeat_at = datetime.now(UTC)
    if agent_version is not None:
        server.agent_version = agent_version
    approved = server.approved_at is not None and bool(server.is_active)
    if approved and server.status != "online":
        server.status = "online"
    await session.flush()
    return approved


async def is_server_approved(session: AsyncSession, *, server_id: int) -> bool:
    server = await session.get(Server, server_id)
    return bool(server is not None and server.is_active and server.approved_at is not None)


async def upsert_telemetry(session: AsyncSession, *, server_id: int, payload: GpuTelemetry) -> bool:
    if not await is_server_approved(session, server_id=server_id):
        return False
    now = datetime.now(UTC)
    for entry in payload.gpus:
        stmt = mysql_insert(Gpu).values(
            server_id=server_id,
            gpu_index=entry.gpu_index,
            uuid=entry.uuid,
            model=entry.model,
            memory_total_mb=entry.memory_total_mb,
            compute_capability=entry.compute_capability,
            util_pct=entry.util_pct,
            memory_used_mb=entry.memory_used_mb,
            temperature_c=entry.temperature_c,
            power_w=entry.power_w,
            process_snapshot=[p.model_dump() for p in entry.processes],
            last_updated_at=now,
            is_active=1,
        )
        upsert = stmt.on_duplicate_key_update(
            uuid=stmt.inserted.uuid,
            model=stmt.inserted.model,
            memory_total_mb=stmt.inserted.memory_total_mb,
            compute_capability=stmt.inserted.compute_capability,
            util_pct=stmt.inserted.util_pct,
            memory_used_mb=stmt.inserted.memory_used_mb,
            temperature_c=stmt.inserted.temperature_c,
            power_w=stmt.inserted.power_w,
            process_snapshot=stmt.inserted.process_snapshot,
            last_updated_at=stmt.inserted.last_updated_at,
            is_active=1,
        )
        await session.execute(upsert)
    await session.flush()
    return True


async def upsert_telemetry_transaction(
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    *,
    server_id: int,
    payload: GpuTelemetry,
    max_retries: int = 3,
    backoff_base_s: float = 0.02,
) -> bool:
    """Write one telemetry batch with fresh-session deadlock retries."""

    async def attempt() -> bool:
        async with session_factory() as session:
            accepted = await upsert_telemetry(session, server_id=server_id, payload=payload)
            await session.commit()
            return accepted

    return await with_serializable_retry(
        attempt,
        max_retries=max_retries,
        backoff_base_s=backoff_base_s,
    )
