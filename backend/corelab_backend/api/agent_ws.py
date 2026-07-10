"""``/ws/agent`` — per-server agent WebSocket endpoint.

Sits outside ``/api/v1`` because the WSS protocol is versioned separately
(``corelab_protocol.PROTOCOL_VERSION``). Each connection runs:

1. authenticate via query param token (bcrypt agent_token_hash, or
   first-time consume of the matching enrollment_token row)
2. register in the in-process connection pool (kicks any prior
   connection for the same server_id with close code 1008)
3. record heartbeat context; unapproved servers remain pending until a
   lab admin approves them
4. loop on ``receive_json``, ``parse_envelope``; pending servers may
   heartbeat but operational frames are ignored
5. on disconnect: approved online servers flip to "offline", emit
   `server.offline` audit, unregister from the pool
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime

from corelab_protocol import (
    AGENT_TO_BACKEND_TYPES,
    RPC_RESPONSE_TYPES,
    AccountScanReport,
    AgentHeartbeat,
    ComplianceViolationEvent,
    GpuTelemetry,
    Pong,
    ScriptFinishedEvent,
    ScriptOutputChunkEvent,
    ScriptStartedEvent,
    parse_envelope,
)
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session_factory
from ..logging_setup import get_logger
from ..models import Lab
from ..services import (
    agent_hub,
    agent_rpc,
    agent_url_broadcast,
    alert_service,
    audit_service,
    capability_sync_service,
    compliance_ingest_service,
    gpu_broker,
    lab_url_service,
    link_cache_sync_service,
    physical_account_service,
    policy_sync_service,
    script_lifecycle_service,
    telemetry_service,
)
from ..services.telemetry_service import AgentAuthError

router = APIRouter()
_log = get_logger("corelab.agent_ws")


async def _handle_gpu_telemetry(
    factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    *,
    server_id: int,
    payload: GpuTelemetry,
) -> bool:
    try:
        accepted = await telemetry_service.upsert_telemetry_transaction(
            factory,
            server_id=server_id,
            payload=payload,
        )
    except OperationalError as exc:
        _log.warning("agent_ws.telemetry_write_failed", server_id=server_id, error=str(exc))
        return False
    # Phase 7 C9 — fan out to /ws/user subscribers (1 Hz throttled).
    if accepted:
        await gpu_broker.fan_out(server_id=server_id, payload=payload)
    return accepted


@router.websocket("/ws/agent")
async def agent_ws(
    websocket: WebSocket,
    token: str = Query(min_length=8, max_length=128),
    server_id: int = Query(ge=1),
) -> None:
    factory = get_session_factory()

    # ── Phase 1: authenticate (rejects before accept so the agent sees
    # a clean handshake failure instead of a torn-down connection). ───
    try:
        async with factory() as session:
            server = await telemetry_service.authenticate_agent(
                session, server_id=server_id, plaintext_token=token
            )
            lab_id = server.lab_id
            await session.commit()
    except AgentAuthError as exc:
        _log.warning("agent_ws.auth_denied", server_id=server_id, reason=str(exc))
        await websocket.close(code=4401, reason="unauthorized")
        return

    await websocket.accept()
    await agent_hub.pool.register(server_id, websocket)

    # ── Phase 2: heartbeat + audit ───────────────────────────────────
    async with factory() as session:
        approved = await telemetry_service.mark_heartbeat(
            session, server_id=server_id, agent_version=None
        )
        await audit_service.write(
            session,
            action="server.online" if approved else "server.phone_home_pending_approval",
            actor_user_id=None,
            lab_id=lab_id,
            target_type="server",
            target_id=server_id,
            target_lab_id=lab_id,
            target_server_id=server_id,
        )
        # Phase M v5 M-6 — push the canonical URL list now so an agent
        # that connected via (say) the LAN URL learns about the tunnel
        # URL the admin enabled later, and vice-versa. The agent
        # persists this list back to its toml so reconnects after a
        # backend restart use the new list immediately.
        lab = await session.get(Lab, lab_id)
        urls = lab_url_service.agent_urls_only(lab) if approved and lab is not None else []
        await session.commit()

    if urls:
        await agent_url_broadcast.push_update_urls(server_id=server_id, urls=urls)

    # Push capability state so the agent's gate reflects the real
    # switches (esp. gpu.kill_process, which the agent defaults OFF).
    # Best-effort: failure here must not abort the connection.
    if approved:
        try:
            async with factory() as session:
                await capability_sync_service.send_on_connect(session, server_id=server_id)
        except Exception as exc:
            _log.warning("agent_ws.capability_sync_failed", server_id=server_id, error=str(exc))
        try:
            async with factory() as session:
                await policy_sync_service.send_on_connect(session, server_id=server_id)
        except Exception as exc:
            _log.warning("agent_ws.policy_sync_failed", server_id=server_id, error=str(exc))
        try:
            async with factory() as session:
                await link_cache_sync_service.send_on_connect(session, server_id=server_id)
        except Exception as exc:
            _log.warning("agent_ws.link_cache_sync_failed", server_id=server_id, error=str(exc))

    # ── Phase 3: receive loop ────────────────────────────────────────
    try:
        while True:
            raw_text = await websocket.receive_text()
            try:
                raw = json.loads(raw_text)
                envelope, payload = parse_envelope(raw)
            except (json.JSONDecodeError, ValidationError, KeyError) as exc:
                _log.warning("agent_ws.invalid_frame", server_id=server_id, error=str(exc))
                await websocket.close(code=1003, reason="invalid_frame")
                break

            if envelope.type not in AGENT_TO_BACKEND_TYPES:
                await websocket.close(code=1003, reason="unexpected_frame_type")
                break

            if not isinstance(payload, AgentHeartbeat | Pong):
                async with factory() as session:
                    approved = await telemetry_service.is_server_approved(
                        session, server_id=server_id
                    )
                if not approved:
                    _log.info(
                        "agent_ws.frame_ignored_pending_approval",
                        server_id=server_id,
                        frame_type=envelope.type,
                    )
                    continue

            if envelope.type in RPC_RESPONSE_TYPES:
                # Pass directly to the RPC framework; correlation_id
                # routing happens there. Payload schema was already
                # validated by parse_envelope above.
                delivered = agent_rpc.deliver_response(
                    correlation_id=envelope.correlation_id,
                    frame_type=envelope.type,
                    payload=envelope.payload,
                )
                if not delivered:
                    _log.warning(
                        "agent_ws.rpc_response_unmatched",
                        server_id=server_id,
                        correlation_id=envelope.correlation_id,
                        frame_type=envelope.type,
                    )
                continue

            if isinstance(payload, AgentHeartbeat):
                async with factory() as session:
                    await telemetry_service.mark_heartbeat(
                        session, server_id=server_id, agent_version=payload.agent_version
                    )
                    await session.commit()
            elif isinstance(payload, GpuTelemetry):
                await _handle_gpu_telemetry(factory, server_id=server_id, payload=payload)
            elif isinstance(payload, Pong):
                _log.debug("agent_ws.pong", server_id=server_id)
            elif isinstance(payload, AccountScanReport):
                async with factory() as session:
                    stats = await physical_account_service.sync_discovered(
                        session,
                        server_id=server_id,
                        lab_id=lab_id,
                        entries=[e.model_dump() for e in payload.entries],
                    )
                    await session.commit()
                _log.info(
                    "agent_ws.account_scan",
                    server_id=server_id,
                    mock=payload.mock,
                    **stats,
                )
            elif isinstance(payload, ScriptStartedEvent):
                async with factory() as session:
                    await script_lifecycle_service.on_script_started(
                        session, payload=payload, lab_id=lab_id
                    )
                    await session.commit()
            elif isinstance(payload, ScriptOutputChunkEvent):
                async with factory() as session:
                    await script_lifecycle_service.on_script_output_chunk(
                        session, payload=payload, lab_id=lab_id
                    )
                    await session.commit()
            elif isinstance(payload, ScriptFinishedEvent):
                async with factory() as session:
                    await script_lifecycle_service.on_script_finished(
                        session, payload=payload, lab_id=lab_id
                    )
                    await session.commit()
            elif isinstance(payload, ComplianceViolationEvent):
                # Phase 9 / FU-38 — third audit path (P8-8). Run the
                # notification + audit fan-out in one transaction; let
                # alert_service own its own commit afterwards because
                # alert_service.create_alert is the at-least-once
                # boundary for the WS alert.new push (Phase 8 P8-11).
                async with factory() as session:
                    await compliance_ingest_service.handle_violation(
                        session, event=payload, lab_id=lab_id
                    )
                    await session.commit()
                async with factory() as alert_sess:
                    await alert_service.create_alert(
                        alert_sess,
                        server_id=payload.server_id,
                        gpu_id=payload.gpu_id,
                        event_type=f"compliance.{payload.policy_key}",
                        severity=compliance_ingest_service.alert_severity_for(payload.severity),
                        payload={
                            "policy_key": payload.policy_key,
                            "severity": payload.severity,
                            "linux_username": payload.linux_username,
                            "linux_pid": payload.linux_pid,
                            "action_taken": payload.action_taken,
                            "downgraded_from": payload.downgraded_from,
                            "memory_used_mb": payload.memory_used_mb,
                            "memory_declared_mb": payload.memory_declared_mb,
                            "util_pct": payload.util_pct,
                            "details": payload.details,
                        },
                    )
    except WebSocketDisconnect:
        _log.info("agent_ws.disconnect", server_id=server_id)
    except Exception as exc:
        _log.exception("agent_ws.error", server_id=server_id, error=str(exc))
    finally:
        agent_hub.pool.unregister(server_id, websocket)
        async with factory() as session:
            await _mark_offline(session, server_id=server_id, lab_id=lab_id)
            await session.commit()


async def _mark_offline(session: AsyncSession, *, server_id: int, lab_id: int) -> None:
    from ..models import Server

    server = await session.get(Server, server_id)
    if server is None:
        return
    if server.approved_at is None:
        return
    if server.status == "online":
        server.status = "offline"
        await audit_service.write(
            session,
            action="server.offline",
            actor_user_id=None,
            lab_id=lab_id,
            target_type="server",
            target_id=server_id,
            target_lab_id=lab_id,
            target_server_id=server_id,
            payload={"at": datetime.now(UTC).isoformat()},
        )
