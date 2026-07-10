"""Phase 2 FU-7: structlog redaction strips sensitive fragments.

Asserts that calling ``logger.info(...)`` with any of the canonical
sensitive keys (password / token / nonce / signature / secret /
private_key / authorization) emits ``***REDACTED***`` instead of the
plaintext, end to end through the configured renderer.
"""

from __future__ import annotations

import pytest
from corelab_backend.logging_setup import configure_logging, get_logger


def test_redact_password_token_setup_token_enrollment_token(
    capfd: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="INFO", json_output=True)
    log = get_logger("corelab.test.redaction")
    log.info(
        "test.redaction",
        password="plaintext_password_xyz",  # pragma: allowlist secret
        token="plaintext_jwt_xyz",
        setup_token="plaintext_setup_xyz",
        agent_token="plaintext_agent_xyz",
        enrollment_token="plaintext_enroll_xyz",
        nonce="nonce_xyz",
        signature="sig_xyz",
        secret="secret_xyz",  # pragma: allowlist secret
        private_key="-----BEGIN OPENSSH PRIVATE KEY-----",  # pragma: allowlist secret
        authorization="Bearer eyJ.xyz",
    )
    captured = capfd.readouterr()
    out = captured.out + captured.err
    for needle in (
        "plaintext_password_xyz",
        "plaintext_jwt_xyz",
        "plaintext_setup_xyz",
        "plaintext_agent_xyz",
        "plaintext_enroll_xyz",
        "nonce_xyz",
        "sig_xyz",
        "secret_xyz",
        "BEGIN OPENSSH PRIVATE KEY",  # pragma: allowlist secret
        "Bearer eyJ.xyz",
    ):
        assert needle not in out, f"sensitive value leaked: {needle!r} -> {out!r}"
    assert "***REDACTED***" in out


def test_redact_phase4_ssh_challenge_pam_payload(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Phase 4 invariant #4 + #11: SSH challenge + PAM payload fields stay redacted.

    Any of the Phase 4 RPC payload kwargs — ``signature_armored`` /
    ``ssh_signature`` / ``pam_password`` / ``challenge_nonce`` — must hit
    the ``signature`` / ``password`` / ``nonce`` fragment matcher and be
    replaced with ``***REDACTED***`` before reaching stdout.
    """
    configure_logging(level="INFO", json_output=True)
    log = get_logger("corelab.test.redaction.phase4")
    log.info(
        "test.redaction.phase4",
        signature_armored="-----BEGIN SSH SIGNATURE-----PHASE4XYZ-----END SSH SIGNATURE-----",
        ssh_signature="ARMORED_SIG_PHASE4XYZ",
        pam_password="ClearTextPamPhase4XYZ",  # pragma: allowlist secret
        challenge_nonce="NoncePhase4XYZ",
        agent_token_hash="bcrypt$2b$Phase4XYZ",  # pragma: allowlist secret
    )
    captured = capfd.readouterr()
    out = captured.out + captured.err
    for needle in (
        "PHASE4XYZ",  # both signature variants
        "ClearTextPamPhase4XYZ",
        "NoncePhase4XYZ",
        "bcrypt$2b$Phase4XYZ",
    ):
        assert needle not in out, f"phase-4 sensitive value leaked: {needle!r} -> {out!r}"
    assert "***REDACTED***" in out


def test_redact_phase6_script_body(capfd: pytest.CaptureFixture[str]) -> None:
    """Phase 6 FU-24: reservation script bodies never appear verbatim in logs.

    ``script`` / ``script_body`` / ``request.script`` / ``event.script``
    are length-redacted to ``<REDACTED N chars>`` so audit can still
    prove the column was non-empty. Metadata keys that *contain* the
    substring (``script_status`` / ``script_started_at`` / etc.) stay
    readable — they are diagnostics, not the script payload.
    """
    configure_logging(level="INFO", json_output=True)
    log = get_logger("corelab.test.redaction.phase6")
    # 38 chars — pick something long enough for the length placeholder
    # to differ from any non-script value we might mistakenly catch.
    body = "export AWS_SECRET_ACCESS_KEY=ScRiPtPh6XYZ"
    log.info(
        "test.redaction.phase6",
        script=body,
        script_body=body,
        script_status="running",  # metadata — must stay readable
        script_started_at="2026-06-05T00:00:00Z",  # metadata — must stay
        script_max_runtime_seconds=3600,  # metadata — must stay
        reservation_id=42,
    )
    captured = capfd.readouterr()
    out = captured.out + captured.err
    assert "ScRiPtPh6XYZ" not in out, f"script body leaked: {out!r}"
    assert "AWS_SECRET_ACCESS_KEY" not in out, f"script body leaked: {out!r}"
    assert f"<REDACTED {len(body)} chars>" in out
    # Metadata keys retain their readable value
    assert "running" in out
    assert "2026-06-05T00:00:00Z" in out
    assert "3600" in out
    assert '"reservation_id": 42' in out
