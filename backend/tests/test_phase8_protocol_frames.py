"""Phase 8 C2 — protocol-level frame parsing for new policy + cache sync.

No DB needed — just exercises the corelab_protocol package round-trip
to make sure the new frame types validate, the MessageType literal
recognises them, and the request → response mapping is wired up.
"""

from __future__ import annotations

from corelab_protocol import (
    AGENT_TO_BACKEND_TYPES,
    BACKEND_TO_AGENT_TYPES,
    PROTOCOL_VERSION,
    RPC_REQUEST_TO_RESPONSE,
    RPC_REQUEST_TYPES,
    RPC_RESPONSE_TYPES,
    MessageEnvelope,
    parse_envelope,
)


def test_protocol_version_at_least_0_5() -> None:
    # Floor for the Phase 8 frames this file exercises; the live version
    # advances as later phases add frames (0.8 = account scan + manual
    # gpu kill). Compare numerically so the assertion doesn't need a
    # bump every protocol revision.
    major, minor = (int(x) for x in PROTOCOL_VERSION.split("."))
    assert (major, minor) >= (0, 5)


def test_policy_sync_request_parses() -> None:
    env = MessageEnvelope(
        type="backend.policy.sync",
        payload={
            "server_id": 7,
            "policies": [
                {
                    "key": "no_reservation_occupy",
                    "enabled": True,
                    "severity": "notify",
                    "threshold_value": None,
                    "grace_period_seconds": 300,
                    "notify_admin": True,
                }
            ],
            "etag": "policy-abc",
        },
    )
    raw = env.model_dump(mode="json")
    parsed_env, payload = parse_envelope(raw)
    assert parsed_env.type == "backend.policy.sync"
    assert payload.server_id == 7  # type: ignore[attr-defined]
    assert len(payload.policies) == 1  # type: ignore[attr-defined]


def test_policy_sync_response_parses_agent_ack() -> None:
    env = MessageEnvelope(
        type="agent.policy.sync.response",
        correlation_id="backend-frame-1",
        payload={
            "ok": True,
            "applied": True,
            "etag_now": "policy-abc",
            "error": None,
        },
    )

    parsed_env, payload = parse_envelope(env.model_dump(mode="json"))

    assert parsed_env.type == "agent.policy.sync.response"
    assert payload.ok is True  # type: ignore[attr-defined]
    assert payload.applied is True  # type: ignore[attr-defined]
    assert payload.etag_now == "policy-abc"  # type: ignore[attr-defined]
    assert payload.error is None  # type: ignore[attr-defined]


def test_account_link_cache_sync_full_with_active_reservations() -> None:
    """Worker Catch #1 schema extension — active_reservations is present."""
    env = MessageEnvelope(
        type="backend.account_link_cache.sync",
        payload={
            "server_id": 7,
            "mode": "full",
            "entries": [
                {
                    "linux_username": "yang_lab",
                    "user_ids": [42, 50],
                    "active_reservations": {
                        "42": [
                            {
                                "reservation_id": 101,
                                "gpu_id": 5,
                                "start_at": "2026-06-05T03:00:00+00:00",
                                "end_at": "2026-06-05T07:00:00+00:00",
                                "status": "active",
                                "gpu_memory_mb": None,
                                "gpu_compute_share_pct": None,
                                "source": "ssh_challenge",
                            }
                        ],
                        "50": [],
                    },
                }
            ],
            "removed_linux_usernames": [],
            "etag": "links-abc",
        },
    )
    raw = env.model_dump(mode="json")
    parsed_env, payload = parse_envelope(raw)
    assert parsed_env.type == "backend.account_link_cache.sync"
    assert payload.mode == "full"  # type: ignore[attr-defined]
    entries = payload.entries  # type: ignore[attr-defined]
    assert len(entries) == 1
    assert entries[0].user_ids == [42, 50]
    assert entries[0].active_reservations["42"][0].reservation_id == 101
    assert entries[0].active_reservations["42"][0].source == "ssh_challenge"


def test_account_link_cache_sync_incremental_with_removals() -> None:
    env = MessageEnvelope(
        type="backend.account_link_cache.sync",
        payload={
            "server_id": 7,
            "mode": "incremental",
            "entries": [{"linux_username": "ivy_lab", "user_ids": [70]}],
            "removed_linux_usernames": ["old_lab"],
            "etag": "links-xyz",
        },
    )
    _parsed_env, payload = parse_envelope(env.model_dump(mode="json"))
    assert payload.mode == "incremental"  # type: ignore[attr-defined]
    assert payload.removed_linux_usernames == ["old_lab"]  # type: ignore[attr-defined]


def test_rpc_request_to_response_includes_phase_8() -> None:
    assert RPC_REQUEST_TO_RESPONSE["backend.policy.sync"] == "agent.policy.sync.response"
    assert (
        RPC_REQUEST_TO_RESPONSE["backend.account_link_cache.sync"]
        == "agent.account_link_cache.sync.response"
    )
    assert (
        RPC_REQUEST_TO_RESPONSE["backend.authorized_key.read"]
        == "agent.authorized_key.read.response"
    )


def test_direction_sets_include_phase_8_types() -> None:
    assert "backend.policy.sync" in BACKEND_TO_AGENT_TYPES
    assert "backend.account_link_cache.sync" in BACKEND_TO_AGENT_TYPES
    assert "agent.policy.sync.response" in AGENT_TO_BACKEND_TYPES
    assert "agent.account_link_cache.sync.response" in AGENT_TO_BACKEND_TYPES
    assert "backend.policy.sync" in RPC_REQUEST_TYPES
    assert "agent.policy.sync.response" in RPC_RESPONSE_TYPES
    assert "backend.authorized_key.read" in BACKEND_TO_AGENT_TYPES
    assert "agent.authorized_key.read.response" in AGENT_TO_BACKEND_TYPES
    assert "backend.authorized_key.read" in RPC_REQUEST_TYPES
    assert "agent.authorized_key.read.response" in RPC_RESPONSE_TYPES


def test_authorized_key_read_response_parses_without_raw_key_material() -> None:
    env = MessageEnvelope(
        type="agent.authorized_key.read.response",
        payload={
            "ok": True,
            "authorized_keys_path": "/home/ivy_lab/.ssh/authorized_keys",
            "line_count": 1,
            "invalid_line_count": 0,
            "keys": [
                {
                    "line_number": 2,
                    "fingerprint_sha256": "SHA256:abc",
                    "key_type": "ssh-ed25519",
                    "comment": "corelab:user=44",
                }
            ],
        },
    )

    _parsed_env, payload = parse_envelope(env.model_dump(mode="json"))

    assert payload.ok is True  # type: ignore[attr-defined]
    assert payload.keys[0].fingerprint_sha256 == "SHA256:abc"  # type: ignore[attr-defined]
    assert not hasattr(payload.keys[0], "public_key")  # type: ignore[attr-defined]
