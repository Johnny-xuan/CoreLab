"""Agent-side structlog redaction (Phase 6 FU-24 + Phase 1/4 fragments).

Mirrors backend/tests/test_logging_redaction.py for the agent's own
logging_setup so a future divergence (someone removes the fragment
list, or forgets to keep the agent in sync with the backend) fails
loud at the agent test run.
"""

from __future__ import annotations

import pytest
from corelab_agent.logging_setup import configure_logging, get_logger


def test_agent_redact_password_token_signature_nonce(
    capfd: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="INFO", json_output=True)
    log = get_logger("corelab.test.agent.redaction")
    log.info(
        "test.agent.redaction",
        password="agent_pw_xyz",  # pragma: allowlist secret
        agent_token="agent_token_xyz",  # pragma: allowlist secret
        nonce="agent_nonce_xyz",
        signature_armored="-----BEGIN SSH SIGNATURE-----AGENTXYZ-----END SSH SIGNATURE-----",
    )
    captured = capfd.readouterr()
    out = captured.out + captured.err
    for needle in (
        "agent_pw_xyz",
        "agent_token_xyz",
        "agent_nonce_xyz",
        "AGENTXYZ",
    ):
        assert needle not in out, f"agent sensitive value leaked: {needle!r} -> {out!r}"
    assert "***REDACTED***" in out


def test_agent_redact_phase6_script_body(capfd: pytest.CaptureFixture[str]) -> None:
    """Phase 6 FU-24 — agent side. Same exact-key policy as backend:
    ``script`` / ``script_body`` / ``request.script`` / ``event.script``
    are length-redacted; ``script_status`` / ``script_started_at`` /
    ``script_max_runtime_seconds`` stay readable.
    """
    configure_logging(level="INFO", json_output=True)
    log = get_logger("corelab.test.agent.redaction.phase6")
    body = "export AWS_SECRET_ACCESS_KEY=AgentScriptPh6XYZ"
    log.info(
        "test.agent.redaction.phase6",
        script=body,
        script_body=body,
        script_status="running",
        script_started_at="2026-06-05T00:00:00Z",
        script_max_runtime_seconds=600,
        reservation_id=99,
    )
    captured = capfd.readouterr()
    out = captured.out + captured.err
    assert "AgentScriptPh6XYZ" not in out
    assert "AWS_SECRET_ACCESS_KEY" not in out
    assert f"<REDACTED {len(body)} chars>" in out
    assert "running" in out
    assert "2026-06-05T00:00:00Z" in out
    assert "600" in out
    assert '"reservation_id": 99' in out
