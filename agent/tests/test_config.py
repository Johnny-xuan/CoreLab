"""Config loading + validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from corelab_agent.config import AgentConfig, load_config


class TestAgentConfig:
    def test_minimal_valid_config(self) -> None:
        cfg = AgentConfig.model_validate({"backend_url": "wss://corelab.example"})
        assert cfg.backend_url == "wss://corelab.example"
        assert cfg.mock_mode is False
        assert cfg.reconnect_initial_seconds == 1.0
        assert cfg.reconnect_max_seconds == 30.0

    def test_rejects_unknown_keys(self) -> None:
        with pytest.raises(ValueError):
            AgentConfig.model_validate({"backend_url": "wss://x", "rogue_field": 42})

    def test_server_id_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            AgentConfig.model_validate({"backend_url": "wss://x", "server_id": 0})


class TestLoadConfig:
    def test_loads_mock_fixture(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "mock-config.toml"
        cfg = load_config(fixture)
        assert cfg.backend_url == "wss://localhost:8443"
        assert cfg.mock_mode is True
        assert cfg.log_json is False
        assert cfg.reconnect_initial_seconds == 0.5

    def test_missing_file_raises_filenotfound(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "does-not-exist.toml")

    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.toml"
        bad.write_text("mock_mode = true\n")
        with pytest.raises(ValueError):
            load_config(bad)
