"""``pam_handler`` unit tests — capability gate + Mac mock semantics.

Mock_mode accepts any *non-empty* password and refuses empty ones, so
the agent's contract with the backend stays consistent across mock /
prod: an empty payload is always a verify failure, not a permissive
pass.
"""

from __future__ import annotations

import pytest
from corelab_agent import capabilities
from corelab_agent.pam_handler import (
    CAPABILITY_KEY,
    MOCK_WARNING,
    InvalidUsernameError,
    verify,
)


@pytest.fixture(autouse=True)
def _reset_capabilities() -> None:
    capabilities.clear()


async def test_mock_accepts_non_empty_password() -> None:
    result = await verify(
        linux_username="yang_lab",
        password="anything-goes-in-mock",  # pragma: allowlist secret
        mock_mode=True,
    )
    assert result.verify_ok is True
    assert result.mock_warning == MOCK_WARNING
    assert result.error is None


async def test_mock_refuses_empty_password() -> None:
    """Defence in depth — mock_mode still treats empty as failure."""
    result = await verify(
        linux_username="yang_lab",
        password="",
        mock_mode=True,
    )
    assert result.verify_ok is False
    assert result.error == "empty_password"


_FAKE_PASSWORD = "pw"  # pragma: allowlist secret


async def test_capability_gate_blocks_when_disabled() -> None:
    capabilities.set_enabled(CAPABILITY_KEY, False)
    with pytest.raises(capabilities.CapabilityDisabledError):
        await verify(linux_username="yang_lab", password=_FAKE_PASSWORD, mock_mode=True)


async def test_invalid_username_rejected_before_pam() -> None:
    with pytest.raises(InvalidUsernameError):
        await verify(linux_username="bad name", password=_FAKE_PASSWORD, mock_mode=True)


async def test_mock_does_not_log_password_value(capfd: pytest.CaptureFixture[str]) -> None:
    """Invariant #4: PAM mock log line must not contain the plaintext password."""
    from corelab_agent import logging_setup

    logging_setup.configure_logging(level="WARNING", json_output=False)
    await verify(
        linux_username="yang_lab",
        password="SuperSecretCleartextPamPhase4",  # pragma: allowlist secret
        mock_mode=True,
    )
    captured = capfd.readouterr()
    assert "SuperSecretCleartextPamPhase4" not in captured.out + captured.err
