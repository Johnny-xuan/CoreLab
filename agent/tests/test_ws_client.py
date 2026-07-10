"""WsClient unit tests — backoff math, URL builder, redaction, stop loop.

We do not spin up a real WS server in Phase 1 — that's an integration
test for Phase 3. Phase 1 tests cover the pieces deterministically:
backoff schedule, URL construction with/without token, query redaction
for logging, and that ``run()`` exits promptly when ``request_stop()``
is called even while the loop is between reconnect attempts.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from corelab_agent import capabilities, policy_cache
from corelab_agent.config import AgentConfig
from corelab_agent.policy_handlers import ViolationEventPayload
from corelab_agent.ws_client import (
    DEFAULT_BACKOFF_SCHEDULE,
    WsClient,
    _redact_query,
    build_connect_url,
    next_backoff_seconds,
)
from corelab_protocol import (
    BackendConfigUpdateUrlsRequest,
    LinuxUseraddRequest,
    LinuxUserdelRequest,
    PolicySyncRequest,
)


class TestBackoff:
    def test_first_attempt_returns_first_step(self) -> None:
        assert next_backoff_seconds(1, max_seconds=30.0) == DEFAULT_BACKOFF_SCHEDULE[0]

    def test_caps_at_max(self) -> None:
        # max smaller than schedule tail caps the delay.
        assert next_backoff_seconds(5, max_seconds=10.0) == 10.0

    def test_tail_uses_last_schedule_entry(self) -> None:
        # attempt beyond schedule length reuses the last entry (capped).
        assert next_backoff_seconds(99, max_seconds=60.0) == DEFAULT_BACKOFF_SCHEDULE[-1]

    def test_zero_attempt_is_zero(self) -> None:
        assert next_backoff_seconds(0, max_seconds=30.0) == 0.0


class TestUrlBuilder:
    def test_uses_agent_token_when_present(self) -> None:
        cfg = AgentConfig(
            backend_url="wss://lab.example.com/",
            agent_token="agent-tok",
            enrollment_token="should-be-ignored",
        )
        url = build_connect_url(cfg)
        assert url == "wss://lab.example.com/ws/agent?token=agent-tok"

    def test_falls_back_to_enrollment_token(self) -> None:
        cfg = AgentConfig(
            backend_url="wss://lab.example.com",
            enrollment_token="enroll-tok",
        )
        url = build_connect_url(cfg)
        assert url == "wss://lab.example.com/ws/agent?token=enroll-tok"

    def test_no_token_yields_no_query(self) -> None:
        cfg = AgentConfig(backend_url="wss://lab.example.com")
        url = build_connect_url(cfg)
        assert url == "wss://lab.example.com/ws/agent"

    def test_redact_strips_query(self) -> None:
        assert _redact_query("wss://x/ws/agent?token=abc") == "wss://x/ws/agent"
        assert _redact_query("wss://x/ws/agent") == "wss://x/ws/agent"


class TestRunStops:
    async def test_request_stop_aborts_reconnect_wait(self) -> None:
        """run() should exit promptly when stop_event is set during backoff."""
        cfg = AgentConfig(
            # Unreachable port — connect_once will fail immediately and we go
            # into backoff, where stop_event takes effect.
            backend_url="wss://127.0.0.1:1",
            reconnect_initial_seconds=1.0,
            reconnect_max_seconds=1.0,
        )
        stop = asyncio.Event()
        client = WsClient(cfg, stop_event=stop)

        async def stopper() -> None:
            # Let one connect attempt fail, then request stop.
            await asyncio.sleep(0.3)
            client.request_stop()

        # Bound the whole test so a regression can't hang CI.
        await asyncio.wait_for(
            asyncio.gather(client.run(), stopper()),
            timeout=5.0,
        )

    async def test_stop_before_run_exits_immediately(self) -> None:
        cfg = AgentConfig(backend_url="wss://127.0.0.1:1")
        stop = asyncio.Event()
        stop.set()
        client = WsClient(cfg, stop_event=stop)
        await asyncio.wait_for(client.run(), timeout=2.0)


class TestLinuxUserHandlers:
    async def test_useradd_request_is_dispatched(self) -> None:
        capabilities.clear()
        client = WsClient(AgentConfig(backend_url="wss://lab.example.com", mock_mode=True))

        response_type, payload = await client._invoke_handler(
            frame_type="backend.linux.useradd",
            payload=LinuxUseraddRequest(linux_username="alice_lab"),
            mock=True,
        )

        assert response_type == "agent.linux.useradd.response"
        assert payload["ok"] is True
        assert payload["home_directory"] == "/home/alice_lab"
        assert payload["mock_warning"] is not None

    async def test_userdel_request_is_dispatched(self) -> None:
        capabilities.clear()
        client = WsClient(AgentConfig(backend_url="wss://lab.example.com", mock_mode=True))

        response_type, payload = await client._invoke_handler(
            frame_type="backend.linux.userdel",
            payload=LinuxUserdelRequest(linux_username="alice_lab"),
            mock=True,
        )

        assert response_type == "agent.linux.userdel.response"
        assert payload["ok"] is True
        assert payload["mock_warning"] is not None


class TestPolicySyncHandler:
    async def test_policy_sync_updates_cache_and_returns_wire_ack(self) -> None:
        policy_cache.reset()
        client = WsClient(AgentConfig(backend_url="wss://lab.example.com", mock_mode=True))

        response_type, payload = await client._invoke_handler(
            frame_type="backend.policy.sync",
            payload=PolicySyncRequest(
                server_id=7,
                policies=[
                    {
                        "key": "no_reservation_occupy",
                        "enabled": True,
                        "severity": "warn",
                        "threshold_value": None,
                        "grace_period_seconds": 300,
                        "notify_admin": True,
                    }
                ],
                etag="policy-abc",
            ),
            mock=True,
        )

        assert response_type == "agent.policy.sync.response"
        assert payload == {
            "ok": True,
            "applied": True,
            "etag_now": "policy-abc",
            "error": None,
        }
        assert "_stored" not in payload
        assert policy_cache.etag(7) == "policy-abc"
        assert policy_cache.entry_count(7) == 1


class TestConfigUpdateUrlsHandler:
    async def test_update_urls_replaces_in_memory_and_persisted_list(self, tmp_path) -> None:
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            'backend_urls = ["wss://old.trycloudflare.com/ws/agent"]\nagent_token = "tok"\n',
            encoding="utf-8",
        )
        cfg = AgentConfig(
            backend_urls=["wss://old.trycloudflare.com/ws/agent"],
            agent_token="tok",
        )
        client = WsClient(cfg, config_path=config_path)

        response_type, payload = await client._invoke_handler(
            frame_type="backend.config.update_urls",
            payload=BackendConfigUpdateUrlsRequest(urls=["ws://localhost:8080/ws/agent"]),
            mock=False,
        )

        assert response_type == "agent.config.update_urls.ack"
        assert payload == {
            "ok": True,
            "applied_urls": ["ws://localhost:8080/ws/agent"],
            "error": None,
        }
        assert cfg.backend_urls == ["ws://localhost:8080/ws/agent"]
        assert config_path.read_text(encoding="utf-8") == (
            'backend_urls = ["ws://localhost:8080/ws/agent"]\nagent_token = "tok"'
        )


class _FakeConn:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


class TestGovernanceCallbacks:
    async def test_push_compliance_violation_serializes_event(self) -> None:
        client = WsClient(AgentConfig(backend_url="wss://lab.example.com", server_id=7))
        fake = _FakeConn()
        client._current_conn = fake  # exercise the public push helper without a real socket

        await client.push_compliance_violation(
            ViolationEventPayload(
                server_id=7,
                gpu_id=0,
                policy_key="no_reservation_occupy",
                severity="warn",
                linux_username="ivy_lab",
                linux_pid=1234,
                linked_platform_user_ids=[42],
                action_taken="warn",
                downgraded_from=None,
            )
        )

        assert len(fake.sent) == 1
        frame = json.loads(fake.sent[0])
        assert frame["type"] == "agent.compliance.violation"
        assert frame["payload"]["server_id"] == 7
        assert frame["payload"]["policy_key"] == "no_reservation_occupy"
        assert frame["payload"]["linux_pid"] == 1234
        assert frame["payload"]["applied"] is True


class TestMockModeWarning:
    def test_warn_if_linux_no_op_on_mac(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Mac the warning is suppressed; just confirm it doesn't raise."""
        from corelab_agent import mock_mode

        monkeypatch.setattr(mock_mode.platform, "system", lambda: "Darwin")
        mock_mode.warn_if_linux()  # no exception, no log

    def test_warn_if_linux_emits_on_linux(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        """On Linux the warning is emitted to stderr via structlog."""
        from corelab_agent import logging_setup, mock_mode

        # Non-JSON so the event name appears verbatim in captured stderr.
        logging_setup.configure_logging(level="WARNING", json_output=False)
        monkeypatch.setattr(mock_mode.platform, "system", lambda: "Linux")
        mock_mode.warn_if_linux()

        # Captured stderr should contain the structured event name.
        captured = capfd.readouterr()
        assert "mock_mode.active_on_linux" in captured.err
