"""SSH signature verification handler — runs ``ssh-keygen -Y verify``.

On Linux: ``sudo -u <linux_username> ssh-keygen -Y verify -f
/home/<u>/.ssh/authorized_keys -n corelab -s <sig-file> < <nonce-file>``.
A passing exit code means *both* (1) the signature was produced by the
private key matching some public key and (2) that public key is already
authorized on the box. This is the security model from
``docs/04-security.md`` §5 — the backend never sees the file.

On Mac in ``mock_mode``: returns ``ok=True`` with a sentinel fingerprint
``SHA256:MOCK-MAC-NO-VERIFY`` and a structured warning log so prod-mode
regressions accidentally hitting the mock path are obvious.

The username is matched against ``^[a-z_][a-z0-9_-]{0,31}$`` *before* it
goes into the subprocess argv so command injection can't sneak through.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import capabilities
from .logging_setup import get_logger

_log = get_logger("corelab.agent.ssh_verifier")

_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
CAPABILITY_KEY = "ssh.verify_signature"
MOCK_FINGERPRINT = "SHA256:MOCK-MAC-NO-VERIFY"


class SshVerifierError(Exception):
    pass


class InvalidUsernameError(SshVerifierError):
    pass


@dataclass(frozen=True, slots=True)
class VerifyResult:
    ok: bool
    signer_fingerprint: str | None
    error: str | None = None


def _validate_username(linux_username: str) -> None:
    if not _USERNAME_RE.fullmatch(linux_username):
        raise InvalidUsernameError(f"invalid linux_username {linux_username!r}")


async def verify_sig(
    *,
    linux_username: str,
    nonce: str,
    namespace: str,
    signature_armored: str,
    mock_mode: bool,
) -> VerifyResult:
    capabilities.require_enabled(CAPABILITY_KEY)
    _validate_username(linux_username)

    if mock_mode:
        _log.warning(
            "ssh_verifier.mock_mode",
            linux_username=linux_username,
            platform="darwin",
            note="returning MOCK-MAC-NO-VERIFY without touching ssh-keygen",
        )
        return VerifyResult(ok=True, signer_fingerprint=MOCK_FINGERPRINT)

    with tempfile.TemporaryDirectory(prefix="corelab-ssh-verify-") as tmp:
        tmp_path = Path(tmp)
        nonce_file = tmp_path / "nonce"
        sig_file = tmp_path / "sig"
        nonce_file.write_text(nonce, encoding="utf-8")
        sig_file.write_text(signature_armored, encoding="utf-8")

        cmd = [
            "sudo",
            "-n",
            "-u",
            linux_username,
            "ssh-keygen",
            "-Y",
            "verify",
            "-f",
            f"/home/{linux_username}/.ssh/authorized_keys",
            "-I",
            "",
            "-n",
            namespace,
            "-s",
            str(sig_file),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate(input=nonce_file.read_bytes())
        except FileNotFoundError as exc:
            return VerifyResult(ok=False, signer_fingerprint=None, error=str(exc))

    if proc.returncode == 0:
        # ssh-keygen prints "Good "corelab" signature ... ... SHA256:<fp>" on stdout/stderr
        fp = _parse_fingerprint(stdout.decode("utf-8", "replace")) or _parse_fingerprint(
            stderr.decode("utf-8", "replace")
        )
        return VerifyResult(ok=True, signer_fingerprint=fp)

    return VerifyResult(
        ok=False,
        signer_fingerprint=None,
        error=stderr.decode("utf-8", "replace").strip() or "verify_failed",
    )


_FP_RE = re.compile(r"(SHA256:[A-Za-z0-9+/=]+)")


def _parse_fingerprint(text: str) -> str | None:
    m = _FP_RE.search(text)
    return m.group(1) if m else None
