"""PAM password verification handler.

Linux path: ``pamela.authenticate(linux_username, password, service='login')``.
The password lives in agent memory only for the duration of that call —
no log, no file, no audit body (invariant #4 + docs/04-security.md §6).

Mac in ``mock_mode``: accepts any non-empty password and returns
``verify_ok=True`` with a ``mock_warning`` string so the call site can
flag mock-leakage in tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import capabilities
from .logging_setup import get_logger

_log = get_logger("corelab.agent.pam_handler")

_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
CAPABILITY_KEY = "pam.authenticate"
MOCK_WARNING = "platform=Darwin, no real PAM — verify_ok faked by mock_mode"


class PamHandlerError(Exception):
    pass


class InvalidUsernameError(PamHandlerError):
    pass


@dataclass(frozen=True, slots=True)
class PamResult:
    verify_ok: bool
    mock_warning: str | None = None
    error: str | None = None


def _validate_username(linux_username: str) -> None:
    if not _USERNAME_RE.fullmatch(linux_username):
        raise InvalidUsernameError(f"invalid linux_username {linux_username!r}")


async def verify(
    *,
    linux_username: str,
    password: str,
    mock_mode: bool,
) -> PamResult:
    capabilities.require_enabled(CAPABILITY_KEY)
    _validate_username(linux_username)

    if mock_mode:
        # Do NOT log the password or even its length — just the
        # decision. (invariant #4 / structlog filter already redacts
        # ``password*`` but defence-in-depth.)
        _log.warning(
            "pam_handler.mock_mode",
            linux_username=linux_username,
            platform="darwin",
            note="accepting any non-empty password",
        )
        if not password:
            return PamResult(verify_ok=False, error="empty_password")
        return PamResult(verify_ok=True, mock_warning=MOCK_WARNING)

    # Linux: real PAM. pamela is platform-gated in pyproject so the
    # import is fine here.
    try:
        import pamela
    except ImportError as exc:
        return PamResult(verify_ok=False, error=f"pamela_unavailable: {exc}")

    try:
        pamela.authenticate(linux_username, password, service="login")
        return PamResult(verify_ok=True)
    except pamela.PAMError as exc:
        return PamResult(verify_ok=False, error=str(exc))
    except Exception as exc:
        # Any unexpected PAM failure is a verify failure — never a
        # security pass-through.
        return PamResult(verify_ok=False, error=f"pam_internal: {exc.__class__.__name__}")
