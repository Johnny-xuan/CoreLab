"""``ssh_verifier`` unit tests — capability gate + Mac mock sentinel.

The Linux subprocess path (``sudo -u <u> ssh-keygen -Y verify ...``)
lands in Phase 10 real-hardware verification. Phase 4 only ships the
mock + plumbing, so these tests cover:

- mock_mode returns ``ok=True`` + ``SHA256:MOCK-MAC-NO-VERIFY`` and
  emits a structured warning(so prod-mode regressions hitting the mock
  path are visible in logs).
- capability gate fires before any work happens.
- username regex rejects shell-injection-shaped inputs *before* the
  subprocess argv would be built.
- fingerprint parser pulls ``SHA256:...`` out of typical ssh-keygen
  output.
"""

from __future__ import annotations

import pytest
from corelab_agent import capabilities
from corelab_agent.ssh_verifier import (
    CAPABILITY_KEY,
    MOCK_FINGERPRINT,
    InvalidUsernameError,
    _parse_fingerprint,
    verify_sig,
)


@pytest.fixture(autouse=True)
def _reset_capabilities() -> None:
    capabilities.clear()


async def test_mock_mode_returns_sentinel_fingerprint() -> None:
    result = await verify_sig(
        linux_username="yang_lab",
        nonce="nonce-xyz",
        namespace="corelab",
        signature_armored="-----BEGIN SSH SIGNATURE-----\nAAA\n-----END SSH SIGNATURE-----",
        mock_mode=True,
    )
    assert result.ok is True
    assert result.signer_fingerprint == MOCK_FINGERPRINT
    assert result.error is None


async def test_mock_mode_warns_on_logger(capfd: pytest.CaptureFixture[str]) -> None:
    """Mock path must emit a structured warning so prod regressions stand out."""
    from corelab_agent import logging_setup

    logging_setup.configure_logging(level="WARNING", json_output=False)
    await verify_sig(
        linux_username="yang_lab",
        nonce="n",
        namespace="corelab",
        signature_armored="sig",
        mock_mode=True,
    )
    captured = capfd.readouterr()
    assert "ssh_verifier.mock_mode" in captured.out + captured.err


async def test_capability_gate_blocks_when_disabled() -> None:
    capabilities.set_enabled(CAPABILITY_KEY, False)
    with pytest.raises(capabilities.CapabilityDisabledError):
        await verify_sig(
            linux_username="yang_lab",
            nonce="n",
            namespace="corelab",
            signature_armored="sig",
            mock_mode=True,
        )


@pytest.mark.parametrize(
    "bad_user",
    [
        "yang lab",  # space
        "yang;rm",  # shell injection
        "../etc",  # path traversal
        "Yang",  # capital letter
        "1yang",  # leading digit
        "",  # empty
        "a" * 33,  # too long (>32)
    ],
)
async def test_invalid_username_rejected_before_subprocess(bad_user: str) -> None:
    with pytest.raises(InvalidUsernameError):
        await verify_sig(
            linux_username=bad_user,
            nonce="n",
            namespace="corelab",
            signature_armored="sig",
            mock_mode=True,
        )


def test_parse_fingerprint_matches_typical_output() -> None:
    sample = 'Good "corelab" signature for yang from RSA key SHA256:AbCdEfGhIjKlMnOp1234567890=='
    assert _parse_fingerprint(sample) == "SHA256:AbCdEfGhIjKlMnOp1234567890=="


def test_parse_fingerprint_none_when_absent() -> None:
    assert _parse_fingerprint("verify_failed\n") is None
