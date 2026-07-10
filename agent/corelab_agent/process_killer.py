"""GPU-process kill — shared by policy auto_kill and the admin's
manual kill button (backend.gpu.kill_process RPC).

Linux: SIGTERM first, give the process a short grace to flush/exit,
then SIGKILL if it is still alive. System-mode installs run as the
``corelab-agent`` service user, so student-owned processes normally
need the installer-managed sudoers kill whitelist. PermissionError
falls back to ``sudo -n /bin/kill`` (or ``CORELAB_KILL_BIN``).

Both entry points are gated by the ``gpu.kill_process`` capability —
the policy dispatcher checks it before calling (and downgrades to
warn), and :func:`kill_pid` re-checks so a manual RPC can never
side-step the switch.

Mac in ``mock_mode``: never signals anything; returns
``mock_warning`` so prod-mode regressions hitting the mock path stand
out (same convention as authorized_keys / pam_handler).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
from dataclasses import dataclass

from . import capabilities
from .logging_setup import get_logger

_log = get_logger("corelab.agent.process_killer")

_KILL_CAPABILITY = "gpu.kill_process"
_SIGTERM_GRACE_SECONDS = 3.0


@dataclass
class KillResult:
    ok: bool
    killed: bool = False
    error: str | None = None
    mock_warning: str | None = None


@dataclass
class _SignalAttempt:
    ok: bool
    missing: bool = False
    used_sudo: bool = False
    error: str | None = None


def _kill_bin() -> str | None:
    env_path = os.environ.get("CORELAB_KILL_BIN")
    for candidate in (env_path, "/bin/kill", "/usr/bin/kill"):
        if candidate and os.path.isabs(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _signal_flag(sig: signal.Signals) -> str:
    if sig == signal.SIGTERM:
        return "-TERM"
    if sig == signal.SIGKILL:
        return "-KILL"
    return f"-{int(sig)}"


def _looks_missing(message: str | None) -> bool:
    if not message:
        return False
    lowered = message.lower()
    return "no such process" in lowered or "not found" in lowered


def _sudo_signal(pid: int, sig: signal.Signals) -> _SignalAttempt:
    sudo_bin = shutil.which("sudo")
    kill_bin = _kill_bin()
    if sudo_bin is None:
        return _SignalAttempt(ok=False, error="sudo not found")
    if kill_bin is None:
        return _SignalAttempt(ok=False, error="kill binary not found")

    proc = subprocess.run(
        [sudo_bin, "-n", kill_bin, _signal_flag(sig), str(pid)],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return _SignalAttempt(ok=True, used_sudo=True)
    error = (proc.stderr or proc.stdout).strip() or f"sudo kill exited {proc.returncode}"
    if _looks_missing(error):
        return _SignalAttempt(ok=True, missing=True, used_sudo=True, error=error)
    return _SignalAttempt(ok=False, used_sudo=True, error=error)


def _send_signal(pid: int, sig: signal.Signals) -> _SignalAttempt:
    try:
        os.kill(pid, sig)
        return _SignalAttempt(ok=True)
    except ProcessLookupError:
        return _SignalAttempt(ok=True, missing=True)
    except PermissionError as exc:
        sudo_attempt = _sudo_signal(pid, sig)
        if sudo_attempt.ok:
            return sudo_attempt
        error = sudo_attempt.error or str(exc)
        return _SignalAttempt(
            ok=False,
            used_sudo=sudo_attempt.used_sudo,
            error=f"permission denied; sudo fallback failed: {error}",
        )


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


async def kill_pid(pid: int, *, mock_mode: bool, reason: str | None = None) -> KillResult:
    """Terminate ``pid`` (SIGTERM → grace → SIGKILL). Never raises."""
    if not capabilities.is_enabled(_KILL_CAPABILITY, default=False):
        _log.warning("kill.capability_disabled", pid=pid, reason=reason)
        return KillResult(ok=False, error="gpu.kill_process capability is disabled")
    if mock_mode:
        _log.info("kill.mock", pid=pid, reason=reason)
        return KillResult(
            ok=True,
            killed=True,
            mock_warning="MOCK-MAC-NO-KILL: no process was signalled",
        )
    term = _send_signal(pid, signal.SIGTERM)
    if term.missing:
        # Already gone — the goal state is reached; report it honestly.
        _log.info("kill.already_gone", pid=pid)
        return KillResult(ok=True, killed=False, error="process not found (already exited)")
    if not term.ok:
        _log.warning("kill.permission_denied", pid=pid, error=term.error)
        return KillResult(ok=False, error=f"permission denied signalling pid {pid}: {term.error}")

    await asyncio.sleep(_SIGTERM_GRACE_SECONDS)
    if _alive(pid):
        kill = _send_signal(pid, signal.SIGKILL)
        if not kill.ok and not kill.missing:
            _log.warning("kill.sigkill_permission_denied", pid=pid, error=kill.error)
            return KillResult(
                ok=False, error=f"SIGKILL permission denied for pid {pid}: {kill.error}"
            )
    _log.info("kill.done", pid=pid, reason=reason, sudo=term.used_sudo)
    return KillResult(ok=True, killed=True)
