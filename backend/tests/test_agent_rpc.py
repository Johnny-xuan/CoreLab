from __future__ import annotations

from corelab_backend.services import agent_rpc


def test_optional_response_consumes_expected_fire_and_forget_ack() -> None:
    assert agent_rpc.expect_optional_response(
        correlation_id="optional-1",
        frame_type="backend.policy.sync",
    )

    assert (
        agent_rpc.deliver_response(
            correlation_id="optional-1",
            frame_type="agent.policy.sync.response",
            payload={"ok": True},
        )
        is True
    )
    assert (
        agent_rpc.deliver_response(
            correlation_id="optional-1",
            frame_type="agent.policy.sync.response",
            payload={"ok": True},
        )
        is False
    )


def test_optional_response_rejects_wrong_ack_type() -> None:
    assert agent_rpc.expect_optional_response(
        correlation_id="optional-2",
        frame_type="backend.policy.sync",
    )

    assert (
        agent_rpc.deliver_response(
            correlation_id="optional-2",
            frame_type="agent.capability.sync.ack",
            payload={"ok": True},
        )
        is False
    )


def test_optional_response_ignores_unknown_request_type() -> None:
    assert (
        agent_rpc.expect_optional_response(
            correlation_id="optional-3",
            frame_type="backend.not_registered",
        )
        is False
    )
