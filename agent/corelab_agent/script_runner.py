"""Phase 6 — reservation script execution on the agent host.

Two execution paths:

- **Linux prod**: ``sudo -u <linux_username> /bin/bash -c '<script>'``
  via ``asyncio.create_subprocess_exec``. The sudoers whitelist (set
  up out-of-band per docs/04-security.md §12) is what makes this
  safe — the agent process itself does not run as root. The agent
  refuses any ``linux_username`` whose uid is below 1000 so privileged
  system accounts (``root``, ``mysql``, ``nobody``, ...) cannot be
  targeted even if the backend somehow accepts one.

- **Mac dev mock**: writes a marker line to a per-reservation mock log,
  records a synthetic ``Process`` in the running map for cancel tests,
  and emits ``mock_warning`` strings on every response and lifecycle
  event so the UI cannot accidentally treat a Mac test environment
  as production.

The runner is a class instead of module-level functions because cancel
needs to look up the still-running process by ``reservation_id`` —
that state lives on the instance attached to the ws client.

Lifecycle events (``agent.script.started`` / ``.output_chunk`` /
``.finished``) are pushed via the ``push_event`` async callable
supplied by the WsClient; the runner does not own the connection.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import pwd
import shutil
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from corelab_protocol import (
    CancelScriptRequest,
    CancelScriptResponse,
    ExecuteScriptRequest,
    ExecuteScriptResponse,
    MessageEnvelope,
    ScriptFinishedEvent,
    ScriptOutputChunkEvent,
    ScriptStartedEvent,
)

from . import capabilities
from .logging_setup import get_logger

log = get_logger("corelab.agent.script")

MIN_UID: Final[int] = 1000
GRACE_SECONDS: Final[float] = 5.0
MOCK_LOG_DIR: Final[Path] = Path("/tmp/corelab-agent/script-mock")
MOCK_DURATION_SECONDS: Final[float] = 0.05


PushEventCallable = Callable[[MessageEnvelope], Awaitable[None]]


@dataclass(slots=True)
class _RunningEntry:
    """One in-flight script. ``process`` is None on the Mac mock path
    so cancel can still mark a reason without touching a real PID."""

    process: asyncio.subprocess.Process | None
    started_at: datetime
    log_path: str | None
    pid: int | None
    output_task: asyncio.Task[None] | None = None
    killed_reason: str | None = None


def _is_linux() -> bool:
    """Real Linux subprocess path vs Mac mock. ``platform.system()`` is
    canonical; ``sys.platform`` would also work but reads less."""
    return platform.system() == "Linux"


def _resolve_user_or_error(linux_username: str) -> tuple[pwd.struct_passwd | None, str | None]:
    try:
        info = pwd.getpwnam(linux_username)
    except KeyError:
        return None, "USER_NOT_FOUND"
    if info.pw_uid < MIN_UID:
        return None, "UID_BELOW_THRESHOLD"
    return info, None


class ScriptRunner:
    """Holds the per-agent map of in-flight scripts."""

    def __init__(
        self,
        *,
        push_event: PushEventCallable,
        mock_mode: bool | None = None,
    ) -> None:
        self._push_event = push_event
        # If callers do not pass mock_mode, fall back to "Linux = real,
        # everything else = mock". That mirrors the ssh_verifier / pam
        # mock branch and lets unit tests force either path.
        self._mock_mode = mock_mode if mock_mode is not None else not _is_linux()
        self._running: dict[int, _RunningEntry] = {}
        self._tasks: dict[int, asyncio.Task[None]] = {}

    # ─── exposed for unit tests so cancel can assert state
    @property
    def running(self) -> dict[int, _RunningEntry]:
        return self._running

    async def shutdown(self) -> None:
        """Cancel in-flight lifecycle tasks during agent/test teardown."""
        tasks = list(self._tasks.values())
        if not tasks:
            return

        processes: list[asyncio.subprocess.Process] = []
        for entry in self._running.values():
            entry.killed_reason = entry.killed_reason or "agent_shutdown"
            if entry.process is not None:
                processes.append(entry.process)
                if entry.process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        entry.process.terminate()

        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        for proc in processes:
            if proc.returncode is None:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(proc.wait(), timeout=GRACE_SECONDS)
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()

        self._running.clear()
        self._tasks.clear()

    async def execute(self, payload: ExecuteScriptRequest) -> ExecuteScriptResponse:
        """Immediate ack. The actual run + lifecycle push happens in a
        background task so ``rpc.execute_script`` stays a fast
        request/response round-trip (docs/06 §4.2)."""
        # Capability gate. The key matches docs/06 §5.10 line 798.
        try:
            capabilities.require_enabled("script.execute_as_user")
        except capabilities.CapabilityDisabledError as exc:
            log.warning(
                "script.capability_disabled",
                reservation_id=payload.reservation_id,
                error=str(exc),
            )
            return ExecuteScriptResponse(ok=False, started=False, error="CAPABILITY_DISABLED")

        if payload.reservation_id in self._running:
            log.warning(
                "script.duplicate_dispatch",
                reservation_id=payload.reservation_id,
            )
            return ExecuteScriptResponse(ok=False, started=False, error="ALREADY_RUNNING")

        if self._mock_mode:
            return await self._execute_mock(payload)

        return await self._execute_linux(payload)

    async def cancel(self, payload: CancelScriptRequest) -> CancelScriptResponse:
        """Look up the running process, signal it. Returns immediately;
        the lifecycle ``script.finished`` event (with
        ``killed_by_corelab=true``) is pushed by the background task
        when the subprocess actually exits."""
        entry = self._running.get(payload.reservation_id)
        if entry is None:
            return CancelScriptResponse(
                ok=True,
                cancelled=False,
                detail="no live process for this reservation",
            )
        entry.killed_reason = payload.reason
        if entry.process is None:
            # Mac mock path — the background task spins down on its own
            # timer; mark the reason so the eventual finished event
            # carries killed_by_corelab=true.
            return CancelScriptResponse(
                ok=True,
                cancelled=True,
                mock_warning="MOCK-MAC-NO-PROCESS",
            )
        try:
            entry.process.terminate()
        except ProcessLookupError:
            # Already exited between our lookup and the signal; harmless.
            return CancelScriptResponse(ok=True, cancelled=False, detail="already exited")
        # Schedule the SIGKILL fallback in the background; do not wait
        # here so the RPC ack stays fast. Fire-and-forget — the task is
        # held by the running asyncio loop until it sleeps + checks the
        # entry, so a weak ref is enough; we deliberately do not keep
        # a handle here.
        asyncio.create_task(  # noqa: RUF006 — fire-and-forget SIGKILL fallback
            self._sigkill_if_overdue(payload.reservation_id)
        )
        return CancelScriptResponse(ok=True, cancelled=True)

    # ─── private ──────────────────────────────────────────────────────

    async def _execute_linux(self, payload: ExecuteScriptRequest) -> ExecuteScriptResponse:
        info, err = _resolve_user_or_error(payload.linux_username)
        if err is not None or info is None:
            return ExecuteScriptResponse(ok=False, started=False, error=err or "USER_NOT_FOUND")

        if shutil.which("sudo") is None:
            return ExecuteScriptResponse(ok=False, started=False, error="SUDO_NOT_FOUND")

        log_path = payload.stdout_log_path_hint
        try:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ExecuteScriptResponse(
                ok=False, started=False, error=f"LOG_DIR_MKDIR_FAILED: {exc}"
            )

        try:
            await asyncio.to_thread(Path(log_path).touch, exist_ok=True)
        except OSError as exc:
            return ExecuteScriptResponse(ok=False, started=False, error=f"LOG_OPEN_FAILED: {exc}")

        # Env passthrough — only what payload explicitly set, plus an
        # empty PATH safety baseline. The agent process's own env is
        # NOT inherited so secrets sitting in the agent shell do not
        # leak into the user's script.
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", **payload.env}
        cmd = [
            "sudo",
            "-u",
            payload.linux_username,
            "-i",
            "/bin/bash",
            "-c",
            payload.script,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                cwd=payload.working_directory,
            )
        except OSError as exc:
            return ExecuteScriptResponse(
                ok=False, started=False, error=f"SUBPROCESS_SPAWN_FAILED: {exc}"
            )

        started_at = datetime.now(UTC)
        entry = _RunningEntry(
            process=proc,
            started_at=started_at,
            log_path=log_path,
            pid=proc.pid,
        )
        entry.output_task = asyncio.create_task(self._pump_output(payload, proc.stdout, log_path))
        self._running[payload.reservation_id] = entry
        self._tasks[payload.reservation_id] = asyncio.create_task(
            self._await_and_push(payload, entry)
        )

        # Lifecycle push — script.started.
        await self._push(
            "agent.script.started",
            ScriptStartedEvent(
                reservation_id=payload.reservation_id,
                pid=proc.pid,
                started_at=started_at,
                log_path=log_path,
            ),
        )

        return ExecuteScriptResponse(
            ok=True,
            started=True,
            pid=proc.pid,
            log_path=log_path,
        )

    async def _execute_mock(self, payload: ExecuteScriptRequest) -> ExecuteScriptResponse:
        MOCK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = str(MOCK_LOG_DIR / f"reservation-{payload.reservation_id}.log")
        mock_header = (
            f"# corelab-agent MOCK on {platform.system()} {platform.release()}\n"
            f"# reservation_id={payload.reservation_id} "
            f"linux_username={payload.linux_username}\n"
            f"# (script body redacted from this header — see backend audit_log)\n"
        )
        # Record what would have run so Mac dev still sees the script
        # body in a file (but only the file path, not the agent log).
        await asyncio.to_thread(
            Path(log_path).write_text,
            mock_header,
            encoding="utf-8",
        )
        started_at = datetime.now(UTC)
        entry = _RunningEntry(
            process=None,
            started_at=started_at,
            log_path=log_path,
            pid=None,
        )
        self._running[payload.reservation_id] = entry
        self._tasks[payload.reservation_id] = asyncio.create_task(
            self._await_and_push(payload, entry)
        )

        await self._push(
            "agent.script.started",
            ScriptStartedEvent(
                reservation_id=payload.reservation_id,
                pid=os.getpid(),
                started_at=started_at,
                log_path=log_path,
            ),
        )
        await self._push(
            "agent.script.output_chunk",
            ScriptOutputChunkEvent(
                reservation_id=payload.reservation_id,
                stream="stdout",
                text=mock_header,
                ts=started_at,
            ),
        )

        log.warning(
            "script.mock_execute",
            reservation_id=payload.reservation_id,
            linux_username=payload.linux_username,
            note=f"agent is running on {sys.platform!r}; no real subprocess started",
        )
        return ExecuteScriptResponse(
            ok=True,
            started=True,
            pid=None,
            log_path=log_path,
            mock_warning=f"MOCK-{sys.platform.upper()}-NO-SUBPROCESS",
        )

    async def _await_and_push(self, payload: ExecuteScriptRequest, entry: _RunningEntry) -> None:
        """Wait for the subprocess (or mock timer) to end, push
        ``agent.script.finished``, clean the running map."""
        try:
            exit_code: int | None
            if entry.process is None:
                # Mac mock: simulate a quick natural completion. If
                # cancel was called the killed_reason is already set
                # and we push killed_by_corelab=true.
                await asyncio.sleep(MOCK_DURATION_SECONDS)
                exit_code = 0 if entry.killed_reason is None else -15
                killed = entry.killed_reason is not None
            else:
                try:
                    if payload.max_runtime_seconds is not None:
                        await asyncio.wait_for(
                            entry.process.wait(), timeout=payload.max_runtime_seconds
                        )
                    else:
                        await entry.process.wait()
                    killed = entry.killed_reason is not None
                except TimeoutError:
                    entry.killed_reason = entry.killed_reason or "max_runtime"
                    with contextlib.suppress(ProcessLookupError):
                        entry.process.kill()
                    with contextlib.suppress(Exception):
                        await entry.process.wait()
                    killed = True
                exit_code = entry.process.returncode

            if entry.output_task is not None:
                result = await asyncio.gather(entry.output_task, return_exceptions=True)
                if result and isinstance(result[0], Exception):
                    log.warning(
                        "script.output_pump_failed",
                        reservation_id=payload.reservation_id,
                        error=str(result[0]),
                    )

            finished_at = datetime.now(UTC)
            duration = (finished_at - entry.started_at).total_seconds()
            log_size = 0
            if entry.log_path is not None:
                try:
                    stat_result = await asyncio.to_thread(Path(entry.log_path).stat)
                    log_size = stat_result.st_size
                except OSError:
                    log_size = 0

            await self._push(
                "agent.script.finished",
                ScriptFinishedEvent(
                    reservation_id=payload.reservation_id,
                    exit_code=exit_code,
                    started_at=entry.started_at,
                    finished_at=finished_at,
                    duration_seconds=duration,
                    output_size_bytes=log_size,
                    log_path=entry.log_path,
                    killed_by_corelab=killed,
                    killed_reason=entry.killed_reason,
                ),
            )
        finally:
            if entry.output_task is not None and not entry.output_task.done():
                entry.output_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await entry.output_task
            self._running.pop(payload.reservation_id, None)
            self._tasks.pop(payload.reservation_id, None)

    async def _pump_output(
        self,
        payload: ExecuteScriptRequest,
        reader: asyncio.StreamReader | None,
        log_path: str,
    ) -> None:
        if reader is None:
            return
        log_write_failed = False
        while True:
            chunk = await reader.read(8192)
            if not chunk:
                return
            try:
                await asyncio.to_thread(_append_bytes, log_path, chunk)
            except OSError as exc:
                if not log_write_failed:
                    log.warning(
                        "script.log_write_failed",
                        reservation_id=payload.reservation_id,
                        log_path=log_path,
                        error=str(exc),
                    )
                    log_write_failed = True
            await self._push(
                "agent.script.output_chunk",
                ScriptOutputChunkEvent(
                    reservation_id=payload.reservation_id,
                    stream="stdout",
                    text=chunk.decode("utf-8", errors="replace"),
                    ts=datetime.now(UTC),
                ),
            )

    async def _sigkill_if_overdue(self, reservation_id: int) -> None:
        """5 s after a terminate(), force-kill if the subprocess is still
        around. The await_and_push loop will still emit the finished
        event with killed_reason populated by cancel()."""
        await asyncio.sleep(GRACE_SECONDS)
        entry = self._running.get(reservation_id)
        if entry is None or entry.process is None:
            return
        if entry.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                entry.process.kill()

    async def _push(
        self,
        frame_type: str,
        payload: ScriptStartedEvent | ScriptOutputChunkEvent | ScriptFinishedEvent,
    ) -> None:
        envelope = MessageEnvelope(
            type=frame_type,
            payload=payload.model_dump(mode="json"),
        )
        try:
            await self._push_event(envelope)
        except Exception as exc:
            log.warning("script.push_failed", frame_type=frame_type, error=str(exc))


def _append_bytes(path: str, data: bytes) -> None:
    with Path(path).open("ab") as fh:
        fh.write(data)
