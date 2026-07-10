"""Shared pydantic schema for the CoreLab backend ↔ agent WSS protocol.

The frame models live in :mod:`corelab_protocol.frames` (Phase 3+).
Wire format is canonically documented in ``docs/06-agent-protocol.md``.
"""

from __future__ import annotations

from .frames import (
    AGENT_TO_BACKEND_TYPES,
    BACKEND_TO_AGENT_TYPES,
    RPC_REQUEST_TO_RESPONSE,
    RPC_REQUEST_TYPES,
    RPC_RESPONSE_TYPES,
    SCRIPT_PUSH_EVENT_TYPES,
    AccountLinkCacheEntry,
    AccountLinkCacheSyncRequest,
    AccountLinkCacheSyncResponse,
    AccountScanEntry,
    AccountScanReport,
    ActiveReservationSnapshot,
    AgentCapabilitySyncAck,
    AgentConfigUpdateUrlsAck,
    AgentHeartbeat,
    AuthorizedKeyPushRequest,
    AuthorizedKeyPushResponse,
    AuthorizedKeyReadEntry,
    AuthorizedKeyReadRequest,
    AuthorizedKeyReadResponse,
    AuthorizedKeyRevokeRequest,
    AuthorizedKeyRevokeResponse,
    BackendAck,
    BackendCapabilitySync,
    BackendConfigUpdateUrlsRequest,
    BackendPing,
    CancelScriptRequest,
    CancelScriptResponse,
    CapabilityEntry,
    ComplianceViolationEvent,
    ComplianceViolationHolder,
    ExecuteScriptRequest,
    ExecuteScriptResponse,
    GpuKillProcessRequest,
    GpuKillProcessResponse,
    GpuProcessSnapshot,
    GpuTelemetry,
    GpuTelemetryEntry,
    LinuxUseraddRequest,
    LinuxUseraddResponse,
    LinuxUserdelRequest,
    LinuxUserdelResponse,
    MessageEnvelope,
    MessageType,
    PamVerifyRequest,
    PamVerifyResponse,
    PolicyEntry,
    PolicySyncRequest,
    PolicySyncResponse,
    Pong,
    ReverseLookupNotify,
    ScriptFinishedEvent,
    ScriptOutputChunkEvent,
    ScriptStartedEvent,
    SshVerifySigRequest,
    SshVerifySigResponse,
    parse_envelope,
)

__all__ = [
    "AGENT_TO_BACKEND_TYPES",
    "BACKEND_TO_AGENT_TYPES",
    "PROTOCOL_VERSION",
    "RPC_REQUEST_TO_RESPONSE",
    "RPC_REQUEST_TYPES",
    "RPC_RESPONSE_TYPES",
    "SCRIPT_PUSH_EVENT_TYPES",
    "AccountLinkCacheEntry",
    "AccountLinkCacheSyncRequest",
    "AccountLinkCacheSyncResponse",
    "AccountScanEntry",
    "AccountScanReport",
    "ActiveReservationSnapshot",
    "AgentCapabilitySyncAck",
    "AgentConfigUpdateUrlsAck",
    "AgentHeartbeat",
    "AuthorizedKeyPushRequest",
    "AuthorizedKeyPushResponse",
    "AuthorizedKeyReadEntry",
    "AuthorizedKeyReadRequest",
    "AuthorizedKeyReadResponse",
    "AuthorizedKeyRevokeRequest",
    "AuthorizedKeyRevokeResponse",
    "BackendAck",
    "BackendCapabilitySync",
    "BackendConfigUpdateUrlsRequest",
    "BackendPing",
    "CancelScriptRequest",
    "CancelScriptResponse",
    "CapabilityEntry",
    "ComplianceViolationEvent",
    "ComplianceViolationHolder",
    "ExecuteScriptRequest",
    "ExecuteScriptResponse",
    "GpuKillProcessRequest",
    "GpuKillProcessResponse",
    "GpuProcessSnapshot",
    "GpuTelemetry",
    "GpuTelemetryEntry",
    "LinuxUseraddRequest",
    "LinuxUseraddResponse",
    "LinuxUserdelRequest",
    "LinuxUserdelResponse",
    "MessageEnvelope",
    "MessageType",
    "PamVerifyRequest",
    "PamVerifyResponse",
    "PolicyEntry",
    "PolicySyncRequest",
    "PolicySyncResponse",
    "Pong",
    "ReverseLookupNotify",
    "ScriptFinishedEvent",
    "ScriptOutputChunkEvent",
    "ScriptStartedEvent",
    "SshVerifySigRequest",
    "SshVerifySigResponse",
    "__version__",
    "parse_envelope",
]

__version__ = "0.0.0"

# Bumped when the wire format changes in a way that requires both ends
# to agree on a new schema. Phase 4 → 0.3 introduces request/response
# RPC frames + correlation_id on the envelope so the backend can drive
# ssh-verify / PAM / authorized_key on the agent host. Phase 6 → 0.4
# adds the reservation script lifecycle frames (execute/cancel RPC +
# script.started / script.output_chunk / script.finished push events).
# Phase 8 → 0.5 adds backend.policy.sync + backend.account_link_cache.sync
# (the cache sync schema is extended with active_reservations so the
# agent compliance_monitor never needs to cross WSS per tick — Phase 8
# Worker Catch #1 architectural fix).
# Phase M → 0.6 wires backend.linux.useradd + backend.linux.userdel into
# the whitelist (docs/06 §5.3 / §5.5). Call sites already exist in the
# backend onboard-user flow; pre-0.6 the agent_rpc framework refused them
# with RpcNotYetWiredError.
# Phase M v5 → 0.7 adds backend.config.update_urls + agent.config.update_urls.ack
# so a running agent can absorb URL list changes (e.g. tunnel enabled
# post-install, primary URL rotated) without re-installation.
# 0.8 adds agent.account_scan.report (human-account discovery →
# physical_account.source='discovered_scan') and backend.gpu.kill_process
# + agent.gpu.kill_process.response (admin manual kill from the alerts
# page; same capability gate as policy auto_kill).
# 0.9 adds backend.authorized_key.read + agent.authorized_key.read.response
# so admins can reconcile CoreLab's key ledger against host-side key
# fingerprints without returning raw public key material.
PROTOCOL_VERSION: str = "0.9"
