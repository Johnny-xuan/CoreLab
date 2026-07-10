"""Outbound WSS client with exponential-backoff reconnect.

While connected, runs two background tasks per
``docs/06-agent-protocol.md`` §2: a 30 s heartbeat and a
periodic gpu telemetry push (default 5 s in mock mode for visible
movement on the UI; 60 s in prod). Reconnect uses the Phase 1
backoff schedule.

Backoff schedule (configurable via :class:`~corelab_agent.config.AgentConfig`):
1s → 2s → 5s → 15s → 30s, then 30s capped.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, cast

import websockets
from corelab_protocol import (
    RPC_REQUEST_TYPES,
    AccountLinkCacheSyncRequest,
    AgentHeartbeat,
    AuthorizedKeyPushRequest,
    AuthorizedKeyReadRequest,
    AuthorizedKeyRevokeRequest,
    BackendCapabilitySync,
    BackendConfigUpdateUrlsRequest,
    CancelScriptRequest,
    ComplianceViolationEvent,
    ExecuteScriptRequest,
    GpuKillProcessRequest,
    LinuxUseraddRequest,
    LinuxUserdelRequest,
    MessageEnvelope,
    MessageType,
    PamVerifyRequest,
    PolicySyncRequest,
    SshVerifySigRequest,
    parse_envelope,
)
from pydantic import ValidationError
from websockets.asyncio.client import ClientConnection

from . import __version__ as _agent_version
from . import (
    account_scanner,
    authorized_keys,
    capabilities,
    linux_users,
    pam_handler,
    policy_cache,
    process_killer,
    reverse_cache,
    ssh_verifier,
)
from .config import AgentConfig
from .gpu_collector import GpuCollector
from .logging_setup import get_logger
from .script_runner import ScriptRunner

log = get_logger("corelab.agent.ws")

# Default backoff schedule in seconds; the last entry repeats once the
# tail is reached, capped at config.reconnect_max_seconds.
DEFAULT_BACKOFF_SCHEDULE: Sequence[float] = (1.0, 2.0, 5.0, 15.0, 30.0)


def next_backoff_seconds(attempt: int, max_seconds: float) -> float:
    """Compute the delay before the *attempt*-th reconnect.

    attempt is 1-based. Beyond the schedule it caps at max_seconds.
    """
    if attempt < 1:
        return 0.0
    idx = min(attempt - 1, len(DEFAULT_BACKOFF_SCHEDULE) - 1)
    return min(DEFAULT_BACKOFF_SCHEDULE[idx], max_seconds)


def _build_connect_url_from_base(config: AgentConfig, base: str) -> str:
    """Glue ``base`` + token + server_id query params onto /ws/agent.

    Defensively normalise the scheme: backends may push back a list
    that still has ``http://`` / ``https://`` (which is what
    ``lab.public_urls`` stores for user-facing display). We map those
    to ``ws://`` / ``wss://`` so the websockets client accepts the URI.
    """
    token = config.agent_token or config.enrollment_token
    base = base.rstrip("/")
    # Translate user-facing http(s):// to ws(s)://.
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    # ``base`` here might already include the /ws/agent path (legacy
    # toml shape) or be a bare host URL (new shape). Normalize.
    path = "" if base.endswith("/ws/agent") else "/ws/agent"
    params: list[str] = []
    if token:
        params.append(f"token={token}")
    if config.server_id is not None:
        params.append(f"server_id={config.server_id}")
    if params:
        return f"{base}{path}?{'&'.join(params)}"
    return f"{base}{path}"


def build_connect_url(config: AgentConfig) -> str:
    """Back-compat single-URL entry point. Returns first URL of the list."""
    if not config.backend_urls:
        raise ValueError("config has no backend URLs")
    return _build_connect_url_from_base(config, config.backend_urls[0])


def build_connect_urls(config: AgentConfig) -> list[str]:
    """Return all candidate connect URLs, one per backend_urls entry."""
    return [_build_connect_url_from_base(config, b) for b in config.backend_urls]


class WsClient:
    """Maintains an outbound WSS connection to backend with reconnect."""

    def __init__(
        self,
        config: AgentConfig,
        *,
        gpu_collector: GpuCollector | None = None,
        heartbeat_interval_seconds: float = 30.0,
        telemetry_interval_seconds: float = 60.0,
        on_message: Callable[[str], Awaitable[None]] | None = None,
        stop_event: asyncio.Event | None = None,
        config_path: Path | None = None,
    ) -> None:
        self._config = config
        # Used by `backend.config.update_urls` handler to persist new
        # URL list back to disk (Phase M v5 M-6). Can stay None in
        # tests; in that case the update is in-memory only.
        self._config_path = config_path
        self._gpu = gpu_collector
        self._heartbeat_interval = heartbeat_interval_seconds
        self._telemetry_interval = telemetry_interval_seconds
        self._on_message = on_message
        self._stop_event = stop_event or asyncio.Event()
        self._attempt = 0
        self._started_monotonic = time.monotonic()
        # Last account-scan snapshot actually sent — the rescan loop
        # only re-sends when this changes. Reset is unnecessary across
        # reconnects (connect always sends with force=True).
        self._last_account_snapshot: list[dict] | None = None
        # Phase 6 — current connection used by background script lifecycle
        # tasks to push agent.script.* events. Re-bound on every reconnect.
        self._current_conn: ClientConnection | None = None
        self._script_runner = ScriptRunner(
            push_event=self._push_envelope, mock_mode=config.mock_mode
        )

    def request_stop(self) -> None:
        """Signal the run loop to exit at next idle point (used by SIGTERM)."""
        self._stop_event.set()

    async def run(self) -> None:
        """Connect-and-pump forever, trying each URL in turn on failure.

        Phase M v5 — multi-URL native: when the first URL is unreachable
        (LAN mode + agent runs off-LAN, for example), the agent falls
        through to the next URL in ``backend_urls`` without waiting for
        the full reconnect backoff. The backoff still applies once every
        candidate URL fails — that's the case where every endpoint is
        down, not just one of them.
        """
        log.info(
            "ws.run.start",
            url_count=len(self._config.backend_urls),
            first_url_host=_redact_query(build_connect_urls(self._config)[0])
            if self._config.backend_urls
            else "",
        )

        while not self._stop_event.is_set():
            urls = build_connect_urls(self._config)
            any_connected = False
            for url in urls:
                try:
                    await self._connect_once(url)
                    any_connected = True
                    # Clean disconnect — break out so we run the global
                    # reconnect backoff once, instead of re-trying every
                    # URL immediately.
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning(
                        "ws.connect.failed",
                        error_type=type(exc).__name__,
                        error_msg=str(exc),
                        attempted_url_host=_redact_query(url),
                        attempt=self._attempt,
                    )
                if self._stop_event.is_set():
                    break

            if self._stop_event.is_set():
                break

            if not any_connected:
                # All candidate URLs failed — apply reconnect backoff
                # before starting the next outer loop.
                self._attempt += 1
                delay = next_backoff_seconds(self._attempt, self._config.reconnect_max_seconds)
                log.info("ws.reconnect.wait", attempt=self._attempt, delay_seconds=delay)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
            else:
                # Connected and disconnected cleanly — short backoff so
                # we don't slam-reconnect.
                self._attempt = 1
                delay = next_backoff_seconds(self._attempt, self._config.reconnect_max_seconds)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)

        log.info("ws.run.stopped")

    async def _connect_once(self, url: str) -> None:
        """Open one WSS session and pump messages until disconnect."""
        async with websockets.connect(url, max_size=1 << 20) as conn:
            self._attempt = 0
            self._current_conn = conn
            log.info("ws.connected")
            await self._send_account_scan(conn, force=True)
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(conn))
            account_scan_task = asyncio.create_task(self._account_scan_loop(conn))
            telemetry_task: asyncio.Task[None] | None = None
            if self._gpu is not None:
                telemetry_task = asyncio.create_task(self._telemetry_loop(conn))
            try:
                await self._pump(conn)
            finally:
                heartbeat_task.cancel()
                account_scan_task.cancel()
                if telemetry_task is not None:
                    telemetry_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
                with contextlib.suppress(asyncio.CancelledError):
                    await account_scan_task
                if telemetry_task is not None:
                    with contextlib.suppress(asyncio.CancelledError):
                        await telemetry_task
                self._current_conn = None

    async def _pump(self, conn: ClientConnection) -> None:
        async for raw in conn:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            if await self._maybe_dispatch_rpc(conn, raw):
                continue
            if self._on_message is not None:
                await self._on_message(raw)

    async def _maybe_dispatch_rpc(self, conn: ClientConnection, raw: str) -> bool:
        """If ``raw`` is an RPC request, handle it + send the typed response.

        Returns True iff the frame was dispatched (and thus shouldn't be
        forwarded to ``_on_message``). Parse errors close the connection
        with code 1003 — invalid frames cannot enter business logic.
        """
        try:
            data = json.loads(raw)
            envelope, payload = parse_envelope(data)
        except (json.JSONDecodeError, ValidationError, KeyError) as exc:
            log.warning("ws.rpc.invalid_frame", error=str(exc))
            await conn.close(code=1003, reason="invalid_frame")
            return True

        if envelope.type not in RPC_REQUEST_TYPES:
            return False

        mock = self._config.mock_mode
        response_type, response_payload = await self._invoke_handler(
            frame_type=envelope.type, payload=payload, mock=mock
        )
        reply = MessageEnvelope(
            type=cast(MessageType, response_type),
            payload=response_payload,
            correlation_id=envelope.id,
        )
        await conn.send(reply.model_dump_json())
        return True

    async def _invoke_handler(
        self,
        *,
        frame_type: str,
        payload: Any,
        mock: bool,
    ) -> tuple[str, dict[str, Any]]:
        """Run the correct handler for the request frame and return
        ``(response_frame_type, response_payload_dict)``."""
        if isinstance(payload, SshVerifySigRequest):
            result = await ssh_verifier.verify_sig(
                linux_username=payload.linux_username,
                nonce=payload.nonce,
                namespace=payload.namespace,
                signature_armored=payload.signature_armored,
                mock_mode=mock,
            )
            return "agent.ssh.verify_sig.response", {
                "ok": result.ok,
                "signer_fingerprint": result.signer_fingerprint,
                "error": result.error,
            }
        if isinstance(payload, PamVerifyRequest):
            pam_result = await pam_handler.verify(
                linux_username=payload.linux_username,
                password=payload.password,
                mock_mode=mock,
            )
            return "agent.pam.verify.response", {
                "verify_ok": pam_result.verify_ok,
                "mock_warning": pam_result.mock_warning,
                "error": pam_result.error,
            }
        if isinstance(payload, AuthorizedKeyPushRequest):
            push_result = await authorized_keys.push(
                linux_username=payload.linux_username,
                public_key=payload.public_key,
                label=payload.label,
                mock_mode=mock,
            )
            return "agent.authorized_key.push.response", {
                "installed_path": push_result.installed_path,
                "fingerprint": push_result.fingerprint,
                "mock_warning": push_result.mock_warning,
            }
        if isinstance(payload, AuthorizedKeyRevokeRequest):
            revoke_result = await authorized_keys.revoke(
                linux_username=payload.linux_username,
                fingerprint=payload.fingerprint,
                mock_mode=mock,
            )
            return "agent.authorized_key.revoke.response", {
                "revoked": revoke_result.revoked,
                "mock_warning": revoke_result.mock_warning,
            }
        if isinstance(payload, AuthorizedKeyReadRequest):
            try:
                read_result = await authorized_keys.read(
                    linux_username=payload.linux_username,
                    mock_mode=mock,
                )
                return "agent.authorized_key.read.response", {
                    "ok": True,
                    "authorized_keys_path": read_result.authorized_keys_path,
                    "line_count": read_result.line_count,
                    "invalid_line_count": read_result.invalid_line_count,
                    "keys": [
                        {
                            "line_number": key.line_number,
                            "fingerprint_sha256": key.fingerprint_sha256,
                            "key_type": key.key_type,
                            "comment": key.comment,
                        }
                        for key in read_result.keys
                    ],
                    "mock_warning": read_result.mock_warning,
                    "error": None,
                }
            except authorized_keys.AuthorizedKeysError as exc:
                return "agent.authorized_key.read.response", {
                    "ok": False,
                    "authorized_keys_path": None,
                    "line_count": 0,
                    "invalid_line_count": 0,
                    "keys": [],
                    "mock_warning": None,
                    "error": str(exc),
                }
        if isinstance(payload, ExecuteScriptRequest):
            exec_result = await self._script_runner.execute(payload)
            return "agent.script.execute.response", exec_result.model_dump(mode="json")
        if isinstance(payload, CancelScriptRequest):
            cancel_result = await self._script_runner.cancel(payload)
            return "agent.script.cancel.response", cancel_result.model_dump(mode="json")
        if isinstance(payload, BackendCapabilitySync):
            # Backend pushed the authoritative per-server capability
            # state. Apply it to the local gate so kill / useradd / etc.
            # reflect the admin's switches instead of permissive
            # defaults. Pushed on connect and after every flip.
            for entry in payload.capabilities:
                capabilities.set_enabled(entry.key, entry.enabled)
            log.info("ws.capability_sync.applied", n=len(payload.capabilities))
            return "agent.capability.sync.ack", {
                "ok": True,
                "applied_count": len(payload.capabilities),
                "error": None,
            }
        if isinstance(payload, GpuKillProcessRequest):
            # Admin pulled the manual-kill trigger from the alerts page.
            # process_killer re-checks the gpu.kill_process capability,
            # so this can never bypass the switch even if the backend
            # mis-routes.
            kill_result = await process_killer.kill_pid(
                payload.pid, mock_mode=mock, reason=payload.reason
            )
            return "agent.gpu.kill_process.response", {
                "ok": kill_result.ok,
                "killed": kill_result.killed,
                "error": kill_result.error,
                "mock_warning": kill_result.mock_warning,
            }
        if isinstance(payload, LinuxUseraddRequest):
            useradd_result = await linux_users.useradd(
                linux_username=payload.linux_username,
                uid=payload.uid,
                gid=payload.gid,
                home_directory=payload.home_directory,
                default_shell=payload.default_shell,
                mock_mode=mock,
            )
            return "agent.linux.useradd.response", {
                "ok": useradd_result.ok,
                "uid": useradd_result.uid,
                "gid": useradd_result.gid,
                "home_directory": useradd_result.home_directory,
                "default_shell": useradd_result.default_shell,
                "error": useradd_result.error,
                "mock_warning": useradd_result.mock_warning,
            }
        if isinstance(payload, LinuxUserdelRequest):
            userdel_result = await linux_users.userdel(
                linux_username=payload.linux_username,
                remove_home=payload.remove_home,
                mock_mode=mock,
            )
            return "agent.linux.userdel.response", {
                "ok": userdel_result.ok,
                "error": userdel_result.error,
                "mock_warning": userdel_result.mock_warning,
            }
        if isinstance(payload, PolicySyncRequest):
            # Phase 8 C2 — store agent_policy snapshot for the policy
            # handlers (C4) and compliance_monitor (C3) to read.
            policy_cache.apply_sync(
                server_id=payload.server_id,
                policies=[p.model_dump() for p in payload.policies],
                etag=payload.etag,
            )
            return "agent.policy.sync.response", {
                "ok": True,
                "applied": True,
                "etag_now": payload.etag,
                "error": None,
            }
        if isinstance(payload, BackendConfigUpdateUrlsRequest):
            # Phase M v5 M-6 — backend pushed a new URL list (e.g. admin
            # enabled cloudflare tunnel, added a custom domain). Apply
            # in-memory + persist to toml so the next reconnect uses
            # the new list. We don't drop the current connection — let
            # it ride out, the new list will take effect when this
            # session naturally rotates.
            new_urls = list(payload.urls)
            applied: list[str] = []
            ack_error: str | None = None
            if not new_urls:
                ack_error = "empty url list"
            else:
                try:
                    object.__setattr__(self._config, "backend_urls", new_urls)
                    if self._config_path is not None:
                        self._persist_backend_urls(new_urls)
                    applied = new_urls
                    log.info("ws.config.update_urls.applied", url_count=len(new_urls))
                except OSError as exc:
                    ack_error = f"persist failed: {exc}"
                    log.warning("ws.config.update_urls.persist_failed", error=str(exc))
            return "agent.config.update_urls.ack", {
                "ok": ack_error is None,
                "applied_urls": applied,
                "error": ack_error,
            }
        if isinstance(payload, AccountLinkCacheSyncRequest):
            # Phase 8 C2 — apply the reverse-lookup snapshot/delta so
            # the compliance_monitor tick can resolve all reverse
            # lookups locally (Worker Catch #1 architectural fix).
            entries = [e.model_dump(mode="json") for e in payload.entries]
            if payload.mode == "full":
                count = reverse_cache.apply_full_snapshot(
                    server_id=payload.server_id,
                    entries=entries,
                )
            else:
                count = reverse_cache.apply_incremental(
                    server_id=payload.server_id,
                    entries=entries,
                    removed_linux_usernames=list(payload.removed_linux_usernames),
                )
            return "agent.account_link_cache.sync.response", {
                "ok": True,
                "applied": True,
                "entries_count": count,
                "error": None,
            }
        raise RuntimeError(f"no handler registered for frame_type={frame_type!r}")

    def _persist_backend_urls(self, new_urls: list[str]) -> None:
        """Rewrite agent.toml's backend_urls field in place.

        Atomicity: read → rewrite buffer → write to temp file → rename
        over the target. Failure leaves the original toml intact.
        """
        path = self._config_path
        if path is None:
            return
        try:
            current_text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            current_text = ""
        new_text = _rewrite_backend_urls_in_toml(current_text, new_urls)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(path)

    async def push_envelope(self, env: MessageEnvelope) -> None:
        """Send a lifecycle envelope to the active backend connection.

        Used by ``ScriptRunner`` (script.started/output/finished) and
        Phase 9 ``PolicyDispatcher`` (agent.compliance.violation) to
        push events without a correlation_id. If the connection is gone
        (mid-reconnect), the event is logged + dropped — Phase 6 does
        not buffer; Phase 7 + FU-13 will revisit replay semantics on
        reconnect.
        """
        conn = self._current_conn
        if conn is None:
            log.warning("ws.push.no_connection", frame_type=env.type)
            return
        try:
            await conn.send(env.model_dump_json())
        except Exception as exc:
            log.warning("ws.push.send_failed", frame_type=env.type, error=str(exc))

    # Kept as an alias for any in-tree callers that still use the
    # underscore-prefixed name (script_runner, agent_rpc).
    _push_envelope = push_envelope

    async def push_compliance_violation(self, event: Any) -> None:
        """Push a structured compliance violation event to the backend."""
        payload = ComplianceViolationEvent(
            server_id=int(event.server_id),
            gpu_id=int(event.gpu_id),
            policy_key=str(event.policy_key),
            severity=event.severity,
            linux_username=event.linux_username,
            linux_pid=event.linux_pid,
            linked_platform_user_ids=list(event.linked_platform_user_ids),
            action_taken=event.action_taken,
            downgraded_from=event.downgraded_from,
            applied=True,
        )
        await self.push_envelope(
            MessageEnvelope(
                type="agent.compliance.violation",
                payload=payload.model_dump(mode="json"),
            )
        )

    async def kill_process_for_policy(self, pid: int) -> bool:
        """Auto-kill callback used by PolicyDispatcher."""
        result = await process_killer.kill_pid(
            pid,
            mock_mode=self._config.mock_mode,
            reason="policy auto_kill",
        )
        return result.ok and result.killed

    async def _send_account_scan(self, conn: ClientConnection, *, force: bool) -> None:
        """Push the human-account snapshot if it changed (or ``force``).

        Runs once on connect (force=True — backend upsert is idempotent
        and a fresh stamp keeps last_seen_at honest) and periodically
        via ``_account_scan_loop`` to catch useradd/userdel done while
        the connection stays up. Failure is logged and swallowed:
        discovery is best-effort and must never break the session.
        """
        try:
            report = account_scanner.scan(
                mock_mode=self._config.mock_mode,
                mock_accounts=self._config.mock_accounts,
            )
            snapshot = [e.model_dump() for e in report.entries]
            if not force and snapshot == self._last_account_snapshot:
                log.debug("ws.account_scan.unchanged", n_accounts=len(snapshot))
                return
            env = MessageEnvelope(type="agent.account_scan.report", payload=report.model_dump())
            await conn.send(env.model_dump_json())
            self._last_account_snapshot = snapshot
            log.info("ws.account_scan.sent", n_accounts=len(report.entries), mock=report.mock)
        except Exception as exc:
            log.warning("ws.account_scan.failed", error=str(exc))

    async def _account_scan_loop(self, conn: ClientConnection) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(self._config.account_scan_interval_seconds)
                await self._send_account_scan(conn, force=False)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("ws.account_scan_loop.failed", error=str(exc))
                return

    async def _heartbeat_loop(self, conn: ClientConnection) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(self._heartbeat_interval)
                payload = AgentHeartbeat(
                    uptime_seconds=int(time.monotonic() - self._started_monotonic),
                    agent_version=_agent_version,
                )
                env = MessageEnvelope(type="agent.heartbeat", payload=payload.model_dump())
                await conn.send(env.model_dump_json())
                log.debug("ws.heartbeat.sent")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("ws.heartbeat.failed", error=str(exc))
                return

    async def _telemetry_loop(self, conn: ClientConnection) -> None:
        assert self._gpu is not None
        while not self._stop_event.is_set():
            try:
                telemetry = await self._gpu.collect()
                env = MessageEnvelope(type="agent.gpu.telemetry", payload=telemetry.model_dump())
                await conn.send(env.model_dump_json())
                log.debug("ws.telemetry.sent", n_gpus=len(telemetry.gpus))
                await asyncio.sleep(self._telemetry_interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("ws.telemetry.failed", error=str(exc))
                return


def _redact_query(url: str) -> str:
    """Strip ``?token=...`` from URL for logging (token is sensitive)."""
    return url.split("?", 1)[0]


def _rewrite_backend_urls_in_toml(text: str, new_urls: list[str]) -> str:
    """Best-effort line-based rewrite of the backend_urls / backend_url field.

    The agent toml is small + line-oriented + written by install-agent.sh,
    so a hand-rolled rewriter is fine — pulling in tomlkit just to keep
    comments would be overkill. Drops legacy ``backend_url = "..."``
    line if present, writes a ``backend_urls = [...]`` line. Other
    lines untouched.
    """
    out_lines: list[str] = []
    backend_urls_written = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("backend_urls"):
            if not backend_urls_written:
                out_lines.append("backend_urls = [" + ", ".join(f'"{u}"' for u in new_urls) + "]")
                backend_urls_written = True
            # Drop any existing backend_urls line(s); we rewrote it.
            continue
        if stripped.startswith("backend_url ") or stripped.startswith("backend_url="):
            # Replace single-URL legacy line with the list form.
            if not backend_urls_written:
                out_lines.append("backend_urls = [" + ", ".join(f'"{u}"' for u in new_urls) + "]")
                backend_urls_written = True
            continue
        out_lines.append(line)
    if not backend_urls_written:
        out_lines.append("backend_urls = [" + ", ".join(f'"{u}"' for u in new_urls) + "]")
    return "\n".join(out_lines) + ("\n" if not text.endswith("\n") else "")
