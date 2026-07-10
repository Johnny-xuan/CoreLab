"""Pydantic frame models for the agent ↔ backend WSS protocol.

Every frame is a `MessageEnvelope` carrying a typed ``payload``. Agent
and backend serialize/deserialize through the same module so any wire
drift surfaces as a validation error rather than a silent mismatch.

See ``docs/06-agent-protocol.md`` for the canonical spec.

Phase 4 (v0.3) introduces request/response RPC frames so the backend
can drive ssh-signature verification, PAM password checks, and
authorized_keys mutations on the agent's host. Responses set
``correlation_id`` to the request envelope's ``id`` so the in-process
RPC framework can match replies to in-flight calls.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


class _FrameBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ─── agent → backend ──────────────────────────────────────────────────


class AgentHeartbeat(_FrameBase):
    """Every 30 s — bumps server.last_heartbeat_at."""

    uptime_seconds: int = Field(ge=0)
    agent_version: str | None = None


class GpuProcessSnapshot(_FrameBase):
    pid: int
    linux_username: str
    memory_mb: int


class GpuTelemetryEntry(_FrameBase):
    gpu_index: int = Field(ge=0)
    uuid: str | None = None
    model: str | None = None
    memory_total_mb: int | None = None
    compute_capability: str | None = None
    util_pct: int = Field(ge=0, le=100)
    memory_used_mb: int | None = None
    temperature_c: int | None = None
    power_w: int | None = None
    processes: list[GpuProcessSnapshot] = Field(default_factory=list)


class GpuTelemetry(_FrameBase):
    """Every 60 s — upserted into the gpu table by (server_id, gpu_index)."""

    gpus: list[GpuTelemetryEntry]


class Pong(_FrameBase):
    """Reply to a backend `BackendPing`."""

    echo: str | None = None


class ReverseLookupNotify(_FrameBase):
    """Agent → backend hint that an unfamiliar process owner showed up.

    Phase 4 lands the frame so the protocol shape is stable; the
    compliance monitor that emits these starts in Phase 8.
    """

    server_id: int
    linux_username: str
    observed_pid: int | None = None
    observed_at: datetime = Field(default_factory=_now)


class AccountScanEntry(_FrameBase):
    """One human account from the agent's /etc/passwd scan."""

    linux_username: str = Field(min_length=1, max_length=32)
    uid: int | None = Field(default=None, ge=0)
    gid: int | None = Field(default=None, ge=0)
    home_directory: str | None = Field(default=None, max_length=255)
    default_shell: str | None = Field(default=None, max_length=100)


class AccountScanReport(_FrameBase):
    """Once per connection — snapshot of human accounts on the host.

    Backend upserts rows the lab doesn't know yet as
    ``physical_account.source='discovered_scan'`` so admins can map
    them to CoreLab users (the ClaimAccount "agent 发现" path).
    """

    entries: list[AccountScanEntry] = Field(max_length=4096)
    mock: bool = False


# ─── backend → agent ──────────────────────────────────────────────────


class BackendPing(_FrameBase):
    """Liveness probe; agent replies with Pong."""

    echo: str | None = None


class BackendAck(_FrameBase):
    """Generic ack for one-shot agent → backend messages."""

    ok: bool = True
    detail: str | None = None


# ─── RPC requests (backend → agent) ───────────────────────────────────


class SshVerifySigRequest(_FrameBase):
    """Ask the agent to ``ssh-keygen -Y verify`` a signature on the server.

    The agent runs the subprocess against
    ``/home/<linux_username>/.ssh/authorized_keys`` so passing
    verification means *both* the signer has the private key *and* the
    public key is already authorized on the box (docs/04-security.md §5).
    """

    linux_username: str
    nonce: str
    namespace: str = "corelab"
    signature_armored: str


class PamVerifyRequest(_FrameBase):
    """Ask the agent to validate a Linux password via PAM.

    The password is transient — held only in agent memory for the
    duration of the ``pamela.authenticate`` call and never written to
    disk or audit log (invariant #4).
    """

    linux_username: str
    password: str


class AuthorizedKeyPushRequest(_FrameBase):
    """Append an SSH public key to the PA's authorized_keys.

    Gated by the per-server ``ssh.push_authorized_key`` capability on
    the agent side. The ``label`` is appended as the key comment so
    operators can grep which platform user owns which line.
    """

    linux_username: str
    public_key: str
    label: str


class AuthorizedKeyRevokeRequest(_FrameBase):
    """Remove a key from the PA's authorized_keys by fingerprint."""

    linux_username: str
    fingerprint: str


class AuthorizedKeyReadRequest(_FrameBase):
    """Read a PA's authorized_keys metadata from the agent host.

    The response reports fingerprints and line metadata only; it does
    not return raw public key material from the host file.
    """

    linux_username: str


class ExecuteScriptRequest(_FrameBase):
    """Phase 6 — dispatch a reservation's script to the agent (long-running).

    Schema mirrors docs/06-agent-protocol.md §5.10 line 730. The agent
    acks immediately (see ExecuteScriptResponse) and pushes
    ``agent.script.started`` / ``agent.script.output_chunk`` /
    ``agent.script.finished`` events as the subprocess progresses.

    Gated by the per-server ``script.execute_as_user`` capability on
    the agent side. The agent enforces ``uid >= 1000`` on the resolved
    Linux user; Mac dev environments take the mock branch and never
    fork a real subprocess.
    """

    reservation_id: int
    linux_username: str
    script: str
    working_directory: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    max_runtime_seconds: int | None = None
    stdout_log_path_hint: str
    max_log_size_bytes: int = 4_194_304


class CancelScriptRequest(_FrameBase):
    """Phase 6 SP-5 — backend asks the agent to kill a running script
    because the owning reservation is being cancelled (user or admin).

    The agent SIGTERM's the subprocess, waits up to 5 s, then SIGKILLs
    if it has not exited. The eventual ``agent.script.finished`` event
    will carry ``killed_by_corelab=true`` and ``killed_reason`` set to
    this request's ``reason`` so the lifecycle handler on the backend
    knows the failure came from a cancel, not a script bug.
    """

    reservation_id: int
    reason: Literal["user_cancel", "admin_cancel", "system_timeout"]


# ─── RPC responses (agent → backend) ──────────────────────────────────


class SshVerifySigResponse(_FrameBase):
    ok: bool
    signer_fingerprint: str | None = None
    error: str | None = None


class PamVerifyResponse(_FrameBase):
    verify_ok: bool
    mock_warning: str | None = None
    error: str | None = None


class AuthorizedKeyPushResponse(_FrameBase):
    installed_path: str
    fingerprint: str
    mock_warning: str | None = None


class AuthorizedKeyRevokeResponse(_FrameBase):
    revoked: bool
    mock_warning: str | None = None


class AuthorizedKeyReadEntry(_FrameBase):
    line_number: int = Field(ge=1)
    fingerprint_sha256: str
    key_type: str | None = None
    comment: str | None = None


class AuthorizedKeyReadResponse(_FrameBase):
    ok: bool
    authorized_keys_path: str | None = None
    line_count: int = Field(default=0, ge=0)
    invalid_line_count: int = Field(default=0, ge=0)
    keys: list[AuthorizedKeyReadEntry] = Field(default_factory=list)
    mock_warning: str | None = None
    error: str | None = None


class ExecuteScriptResponse(_FrameBase):
    """Phase 6 — immediate ack for an ExecuteScriptRequest. The real
    lifecycle arrives later via ``agent.script.started`` /
    ``.output_chunk`` / ``.finished`` push events.
    """

    ok: bool
    started: bool = False
    pid: int | None = None
    log_path: str | None = None
    error: str | None = None
    mock_warning: str | None = None


class CancelScriptResponse(_FrameBase):
    """Phase 6 SP-5 — ack for the cancel ask. ``cancelled=false`` means
    the agent had no live process for this reservation (already
    finished, or was never started); the backend treats that as a
    no-op and proceeds to transition the reservation.
    """

    ok: bool
    cancelled: bool
    detail: str | None = None
    mock_warning: str | None = None


# ─── Phase M M-3 — Linux account lifecycle RPC ──────────────────────


class LinuxUseraddRequest(_FrameBase):
    """Create a Linux user on the server (docs/06 §5.3).

    Gated by the per-server ``linux.useradd`` capability — a dangerous
    capability that must be explicitly enabled (with a notes audit
    trail) before the agent will accept this request.

    ``home_directory`` / ``default_shell`` are hints; real agents may
    apply distro-specific overrides. ``uid`` / ``gid`` left to the
    agent to allocate (returned in the response) unless explicitly
    requested.
    """

    linux_username: str
    uid: int | None = None
    gid: int | None = None
    home_directory: str | None = None
    default_shell: str | None = None


class LinuxUseraddResponse(_FrameBase):
    """Result of a useradd. ``uid`` / ``gid`` / ``home_directory`` /
    ``default_shell`` mirror what the agent actually configured so the
    backend can fill in the matching physical_account row (rather than
    guess). ``ok=false`` carries a human-readable ``error``."""

    ok: bool
    uid: int | None = None
    gid: int | None = None
    home_directory: str | None = None
    default_shell: str | None = None
    error: str | None = None
    mock_warning: str | None = None


class LinuxUserdelRequest(_FrameBase):
    """Delete a Linux user on the server (docs/06 §5.5).

    Gated by the ``linux.userdel`` dangerous capability. ``remove_home``
    is a soft hint; the agent decides whether running ``userdel -r`` is
    safe (e.g. won't blow away a shared scratch directory).
    """

    linux_username: str
    remove_home: bool = False


class LinuxUserdelResponse(_FrameBase):
    ok: bool
    error: str | None = None
    mock_warning: str | None = None


# ─── Push events (agent → backend) ────────────────────────────────────


class ScriptStartedEvent(_FrameBase):
    """Phase 6 — agent has forked the subprocess; reservation.script_status
    transitions NULL -> running on receipt."""

    reservation_id: int
    pid: int
    started_at: datetime
    log_path: str | None = None


class ScriptOutputChunkEvent(_FrameBase):
    """Phase 6 (Phase 7-ready) — optional stdout / stderr stream. Phase 6
    agent does not send these by default; the frame is defined so the
    Phase 7 live-tail wiring does not need a protocol bump."""

    reservation_id: int
    stream: Literal["stdout", "stderr"]
    text: str
    ts: datetime


class ScriptFinishedEvent(_FrameBase):
    """Phase 6 — subprocess exited (naturally or via kill).

    ``killed_by_corelab=true`` plus ``killed_reason`` indicates the
    cancel / max_runtime path; reservation.script_status syncs to
    ``killed`` in that case, otherwise to ``completed`` (exit 0) or
    ``failed`` (exit != 0).
    """

    reservation_id: int
    exit_code: int | None = None
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    output_size_bytes: int = 0
    log_path: str | None = None
    killed_by_corelab: bool = False
    killed_reason: str | None = None


# ─── Phase 8 — policy + account_link cache sync (backend → agent) ────


class PolicyEntry(_FrameBase):
    """One row of the per-server agent_policy table, in the shape the
    agent stores locally. Mirrors docs/02 §5.18 minus the meta fields.

    Phase 9 / FU-37: ``threshold_value`` is now a per-policy_key dict
    (e.g. ``{util_zero_seconds, mem_floor_mb}`` for ``gpu_hang``,
    ``{value, unit}`` for ``memory_overuse`` / ``gpu_temp_high``).
    Validation lives in ``backend.agent_policy_service.THRESHOLD_SCHEMAS``.
    """

    key: str
    enabled: bool
    severity: Literal["log_only", "notify", "warn", "auto_kill"]
    threshold_value: dict[str, Any] | None = None
    grace_period_seconds: int | None = None
    notify_admin: bool = True


class PolicySyncRequest(_FrameBase):
    """Backend → agent. Push the *full* policy set for this server
    after enrollment, profile switch, or any policy_update.

    Schema is docs/06 §5.11b with corrected frame naming (Phase 7
    §3.4 reconcile carry — the original §5.11b text used
    ``rpc.policy_sync`` which violates the ``backend.<rpc>`` /
    ``agent.<rpc>.response`` convention).
    """

    server_id: int
    policies: list[PolicyEntry]
    etag: str


class PolicySyncResponse(_FrameBase):
    ok: bool
    applied: bool = False
    etag_now: str | None = None
    error: str | None = None


class ComplianceViolationHolder(_FrameBase):
    """Reservation holder currently displaced by an occupier (docs/06 §6.2b)."""

    user_id: int
    username: str | None = None
    reservation_id: int | None = None


class ComplianceViolationEvent(_FrameBase):
    """Push event from agent compliance_monitor (docs/06 §6.2b).

    Phase 9 / FU-38 — replaces the Phase 8 "agent operations.log only"
    audit path with a structured backend push so audit_log gets the
    third compliance trail (P8-8). Type was originally
    ``compliance.violation`` in §6.2b; Phase 9 reconciles to
    ``agent.compliance.violation`` per §3.4 prefix rule.
    """

    server_id: int
    gpu_id: int
    policy_key: str
    severity: Literal["log_only", "notify", "warn", "auto_kill"]
    linux_username: str | None = None
    linux_pid: int | None = None
    linked_platform_user_ids: list[int] = Field(default_factory=list)
    current_reservation_holders: list[ComplianceViolationHolder] = Field(default_factory=list)
    action_taken: Literal["log_only", "notify", "warn", "kill", "kill_downgraded_to_warn"] = (
        "notify"
    )
    memory_used_mb: int | None = None
    memory_declared_mb: int | None = None
    util_pct: int | None = None
    # Set when capability x policy co-invariant downgraded a configured
    # severity (e.g. ``auto_kill`` -> ``warn`` when gpu.kill_process
    # capability is off). NULL otherwise.
    downgraded_from: Literal["auto_kill"] | None = None
    details: str | None = None
    applied: bool = False
    etag_now: str | None = None
    error: str | None = None


class ActiveReservationSnapshot(_FrameBase):
    """A single active reservation for a (server, linked-user) pair.

    Cached at the agent so the 60s compliance_monitor tick never has to
    cross WSS for reservation lookups (Phase 8 Catch #3 architectural
    fix — docs/04 §9.7.2). ``status`` is always one of the live values
    (``scheduled`` / ``active``) since terminated reservations don't
    belong in the cache.

    ``source`` keeps the account_link source so the monitor can refuse
    to count ``admin_declared`` links against compliance (act-as
    invariant; docs/04 §9.8 P5).
    """

    reservation_id: int
    gpu_id: int
    start_at: datetime
    end_at: datetime
    status: Literal["scheduled", "active"]
    gpu_memory_mb: int | None = None
    gpu_compute_share_pct: int | None = None
    source: Literal[
        "ssh_challenge",
        "password_pam",
        "admin_prepared_then_ssh",
        "admin_declared",
    ]


class AccountLinkCacheEntry(_FrameBase):
    """One linux-user row for the reverse-lookup cache.

    ``active_reservations`` is keyed by ``str(user_id)`` (JSON object
    key constraint) and lists the platform-user-side active
    reservations on this server. Empty list = user has linked but has
    no current active reservation on the server.
    """

    linux_username: str
    user_ids: list[int]
    active_reservations: dict[str, list[ActiveReservationSnapshot]] = Field(default_factory=dict)


class AccountLinkCacheSyncRequest(_FrameBase):
    """Backend → agent. Full snapshot or incremental update of the
    reverse-lookup cache. Schema follows docs/06 §5.11c with the
    Phase 8 ``active_reservations`` extension (Worker Catch #1).
    """

    server_id: int
    mode: Literal["full", "incremental"]
    entries: list[AccountLinkCacheEntry] = Field(default_factory=list)
    removed_linux_usernames: list[str] = Field(default_factory=list)
    etag: str


class AccountLinkCacheSyncResponse(_FrameBase):
    ok: bool
    applied: bool = False
    entries_count: int = 0
    error: str | None = None


# Manual GPU-process kill — the admin pulled the trigger from the
# alerts page (the "warn gives admin a manual kill button" promise).
# Gated agent-side by the gpu.kill_process capability, same as
# auto_kill; the agent SIGTERMs then SIGKILLs after a short grace.
class GpuKillProcessRequest(_FrameBase):
    pid: int = Field(ge=1)
    linux_username: str | None = None  # sanity cross-check, optional
    reason: str | None = Field(default=None, max_length=500)


class GpuKillProcessResponse(_FrameBase):
    ok: bool
    killed: bool = False
    error: str | None = None
    mock_warning: str | None = None


# Phase M v5 M-6 — backend pushes the canonical list of public URLs to
# the agent so multi-URL try-each + URL rotation (e.g. tunnel enabled
# later) work without re-installing. Agent persists the new list to
# its toml and uses it on next reconnect.
class BackendConfigUpdateUrlsRequest(_FrameBase):
    urls: list[str]


class AgentConfigUpdateUrlsAck(_FrameBase):
    ok: bool
    applied_urls: list[str]
    error: str | None = None


# Capability sync — the backend's per-server agent_capability rows
# pushed to the agent so its local gate reflects the real switches
# (the Phase 8 docstring promise that never landed). Pushed on connect
# and after any capability flip. One-way: the agent applies it to its
# in-memory cache, no ack needed. Before the first sync the agent runs
# on permissive defaults; gpu.kill_process is the exception (it
# defaults OFF agent-side), so manual/auto kill only fire once this
# explicitly enables it.
class CapabilityEntry(_FrameBase):
    key: str
    enabled: bool


class BackendCapabilitySync(_FrameBase):
    capabilities: list[CapabilityEntry]


class AgentCapabilitySyncAck(_FrameBase):
    ok: bool
    applied_count: int = 0
    error: str | None = None


# ─── envelope ────────────────────────────────────────────────────────


MessageType = Literal[
    "agent.heartbeat",
    "agent.gpu.telemetry",
    "agent.pong",
    "agent.reverse_lookup.notify",
    "agent.account_scan.report",
    "backend.ping",
    "backend.ack",
    "backend.ssh.verify_sig",
    "agent.ssh.verify_sig.response",
    "backend.pam.verify",
    "agent.pam.verify.response",
    "backend.authorized_key.push",
    "agent.authorized_key.push.response",
    "backend.authorized_key.revoke",
    "agent.authorized_key.revoke.response",
    "backend.authorized_key.read",
    "agent.authorized_key.read.response",
    # Phase 6 — reservation script lifecycle (docs/06 §5.10 / §5.11).
    "backend.script.execute",
    "agent.script.execute.response",
    "backend.script.cancel",
    "agent.script.cancel.response",
    "agent.script.started",
    "agent.script.output_chunk",
    "agent.script.finished",
    # Phase 8 — policy + reverse-lookup cache sync.
    "backend.policy.sync",
    "agent.policy.sync.response",
    "backend.account_link_cache.sync",
    "agent.account_link_cache.sync.response",
    # Phase 9 / FU-38 — agent-pushed compliance violation event
    # (docs/06 §6.2b, type reconciled per §3.4 prefix rule).
    "agent.compliance.violation",
    # Phase M M-3.1/3.2 — Linux account lifecycle RPC. Spec'd in
    # docs/06 §5.3 (rpc.useradd) and §5.5 (rpc.userdel); call sites
    # already exist in backend physical_accounts onboard flow. Mock
    # agent grows handlers in scripts/mock_agent.py.
    "backend.linux.useradd",
    "agent.linux.useradd.response",
    "backend.linux.userdel",
    "agent.linux.userdel.response",
    # Manual GPU-process kill (admin trigger from alerts page)
    "backend.gpu.kill_process",
    "agent.gpu.kill_process.response",
    # Phase M v5 M-6 — multi-URL config push
    "backend.config.update_urls",
    "agent.config.update_urls.ack",
    "backend.capability.sync",
    "agent.capability.sync.ack",
]


AGENT_TO_BACKEND_TYPES: frozenset[str] = frozenset(
    {
        "agent.heartbeat",
        "agent.gpu.telemetry",
        "agent.pong",
        "agent.reverse_lookup.notify",
        "agent.account_scan.report",
        "agent.ssh.verify_sig.response",
        "agent.pam.verify.response",
        "agent.authorized_key.push.response",
        "agent.authorized_key.revoke.response",
        "agent.authorized_key.read.response",
        # Phase 6
        "agent.script.execute.response",
        "agent.script.cancel.response",
        "agent.script.started",
        "agent.script.output_chunk",
        "agent.script.finished",
        # Phase 8
        "agent.policy.sync.response",
        "agent.account_link_cache.sync.response",
        # Phase 9 / FU-38
        "agent.compliance.violation",
        # Phase M M-3
        "agent.linux.useradd.response",
        "agent.linux.userdel.response",
        "agent.gpu.kill_process.response",
        # Phase M v5 M-6
        "agent.config.update_urls.ack",
        "agent.capability.sync.ack",
    }
)
BACKEND_TO_AGENT_TYPES: frozenset[str] = frozenset(
    {
        "backend.ping",
        "backend.ack",
        "backend.ssh.verify_sig",
        "backend.pam.verify",
        "backend.authorized_key.push",
        "backend.authorized_key.revoke",
        "backend.authorized_key.read",
        # Phase 6
        "backend.script.execute",
        "backend.script.cancel",
        # Phase 8
        "backend.policy.sync",
        "backend.account_link_cache.sync",
        # Phase M M-3
        "backend.linux.useradd",
        "backend.linux.userdel",
        "backend.gpu.kill_process",
        # Phase M v5 M-6
        "backend.config.update_urls",
        # capability sync (one-way push)
        "backend.capability.sync",
    }
)

RPC_REQUEST_TYPES: frozenset[str] = frozenset(
    {
        "backend.ssh.verify_sig",
        "backend.pam.verify",
        "backend.authorized_key.push",
        "backend.authorized_key.revoke",
        "backend.authorized_key.read",
        # Phase 6
        "backend.script.execute",
        "backend.script.cancel",
        # Phase 8
        "backend.policy.sync",
        "backend.account_link_cache.sync",
        # Phase M M-3
        "backend.linux.useradd",
        "backend.linux.userdel",
        "backend.gpu.kill_process",
        # Phase M v5 M-6
        "backend.config.update_urls",
        "backend.capability.sync",
    }
)
RPC_RESPONSE_TYPES: frozenset[str] = frozenset(
    {
        "agent.ssh.verify_sig.response",
        "agent.pam.verify.response",
        "agent.authorized_key.push.response",
        "agent.authorized_key.revoke.response",
        "agent.authorized_key.read.response",
        # Phase 6
        "agent.script.execute.response",
        "agent.script.cancel.response",
        # Phase 8
        "agent.policy.sync.response",
        "agent.account_link_cache.sync.response",
        # Phase M M-3
        "agent.linux.useradd.response",
        "agent.linux.userdel.response",
        "agent.gpu.kill_process.response",
        # Phase M v5 M-6
        "agent.config.update_urls.ack",
        "agent.capability.sync.ack",
    }
)

# Phase 6 — script lifecycle is push-only (no correlation_id), tracked
# separately so the agent_rpc framework does not try to match them
# against any pending request.
SCRIPT_PUSH_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "agent.script.started",
        "agent.script.output_chunk",
        "agent.script.finished",
    }
)

# Each request type points at the response type that closes the
# round-trip — used by the agent_rpc framework to know what payload
# schema to apply when matching replies by correlation_id.
RPC_REQUEST_TO_RESPONSE: dict[str, str] = {
    "backend.ssh.verify_sig": "agent.ssh.verify_sig.response",
    "backend.pam.verify": "agent.pam.verify.response",
    "backend.authorized_key.push": "agent.authorized_key.push.response",
    "backend.authorized_key.revoke": "agent.authorized_key.revoke.response",
    "backend.authorized_key.read": "agent.authorized_key.read.response",
    # Phase 6
    "backend.script.execute": "agent.script.execute.response",
    "backend.script.cancel": "agent.script.cancel.response",
    # Phase 8
    "backend.policy.sync": "agent.policy.sync.response",
    "backend.account_link_cache.sync": "agent.account_link_cache.sync.response",
    # Phase M M-3
    "backend.linux.useradd": "agent.linux.useradd.response",
    "backend.linux.userdel": "agent.linux.userdel.response",
    "backend.gpu.kill_process": "agent.gpu.kill_process.response",
    # Phase M v5 M-6
    "backend.config.update_urls": "agent.config.update_urls.ack",
    "backend.capability.sync": "agent.capability.sync.ack",
}

_PAYLOAD_FOR_TYPE: dict[str, type[_FrameBase]] = {
    "agent.heartbeat": AgentHeartbeat,
    "agent.gpu.telemetry": GpuTelemetry,
    "agent.pong": Pong,
    "agent.reverse_lookup.notify": ReverseLookupNotify,
    "agent.account_scan.report": AccountScanReport,
    "backend.ping": BackendPing,
    "backend.ack": BackendAck,
    "backend.ssh.verify_sig": SshVerifySigRequest,
    "agent.ssh.verify_sig.response": SshVerifySigResponse,
    "backend.pam.verify": PamVerifyRequest,
    "agent.pam.verify.response": PamVerifyResponse,
    "backend.authorized_key.push": AuthorizedKeyPushRequest,
    "agent.authorized_key.push.response": AuthorizedKeyPushResponse,
    "backend.authorized_key.revoke": AuthorizedKeyRevokeRequest,
    "agent.authorized_key.revoke.response": AuthorizedKeyRevokeResponse,
    "backend.authorized_key.read": AuthorizedKeyReadRequest,
    "agent.authorized_key.read.response": AuthorizedKeyReadResponse,
    # Phase 6
    "backend.script.execute": ExecuteScriptRequest,
    "agent.script.execute.response": ExecuteScriptResponse,
    "backend.script.cancel": CancelScriptRequest,
    "agent.script.cancel.response": CancelScriptResponse,
    "agent.script.started": ScriptStartedEvent,
    "agent.script.output_chunk": ScriptOutputChunkEvent,
    "agent.script.finished": ScriptFinishedEvent,
    # Phase 8
    "backend.policy.sync": PolicySyncRequest,
    "agent.policy.sync.response": PolicySyncResponse,
    "backend.account_link_cache.sync": AccountLinkCacheSyncRequest,
    "agent.account_link_cache.sync.response": AccountLinkCacheSyncResponse,
    # Phase 9 / FU-38
    "agent.compliance.violation": ComplianceViolationEvent,
    # Phase M M-3
    "backend.linux.useradd": LinuxUseraddRequest,
    "agent.linux.useradd.response": LinuxUseraddResponse,
    "backend.linux.userdel": LinuxUserdelRequest,
    "backend.gpu.kill_process": GpuKillProcessRequest,
    "agent.gpu.kill_process.response": GpuKillProcessResponse,
    "agent.linux.userdel.response": LinuxUserdelResponse,
    # Phase M v5 M-6
    "backend.config.update_urls": BackendConfigUpdateUrlsRequest,
    "agent.config.update_urls.ack": AgentConfigUpdateUrlsAck,
    "backend.capability.sync": BackendCapabilitySync,
    "agent.capability.sync.ack": AgentCapabilitySyncAck,
}


class MessageEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: MessageType
    id: str = Field(default_factory=_uuid)
    ts: datetime = Field(default_factory=_now)
    payload: dict[str, Any] = Field(default_factory=dict)
    # Set on RPC responses to point at the originating request's id.
    correlation_id: str | None = None


def parse_envelope(raw: dict[str, Any]) -> tuple[MessageEnvelope, _FrameBase]:
    """Validate the envelope + the typed payload it carries.

    Returns ``(envelope, decoded_payload_model)``. Raises ``pydantic.ValidationError``
    if either layer is malformed; callers should close the WS connection
    on failure (invariant: invalid frames cannot enter business logic).
    """
    envelope = MessageEnvelope.model_validate(raw)
    payload_cls = _PAYLOAD_FOR_TYPE[envelope.type]
    payload = payload_cls.model_validate(envelope.payload)
    return envelope, payload
