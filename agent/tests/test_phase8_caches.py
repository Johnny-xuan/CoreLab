"""Phase 8 C2 — agent-side reverse_cache + policy_cache unit tests.

Verifies the cache modules in isolation. ws_client integration round-
trips are exercised through the backend integration suite (which
runs the actual /ws/agent connection).
"""

from __future__ import annotations

import time

import pytest
from corelab_agent import policy_cache, reverse_cache


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    reverse_cache.reset()
    policy_cache.reset()


class TestReverseCache:
    def test_full_snapshot_stores_user_ids_and_reservations(self) -> None:
        n = reverse_cache.apply_full_snapshot(
            server_id=7,
            entries=[
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
        )
        assert n == 1
        assert reverse_cache.lookup_user_ids(7, "yang_lab") == [42, 50]
        res = reverse_cache.lookup_active_reservations(7, "yang_lab", 42)
        assert len(res) == 1
        assert res[0].reservation_id == 101
        assert res[0].source == "ssh_challenge"

    def test_incremental_upsert(self) -> None:
        reverse_cache.apply_full_snapshot(
            server_id=7,
            entries=[{"linux_username": "yang_lab", "user_ids": [42]}],
        )
        reverse_cache.apply_incremental(
            server_id=7,
            entries=[{"linux_username": "yang_lab", "user_ids": [42, 70]}],
        )
        assert reverse_cache.lookup_user_ids(7, "yang_lab") == [42, 70]

    def test_incremental_remove(self) -> None:
        reverse_cache.apply_full_snapshot(
            server_id=7,
            entries=[
                {"linux_username": "yang_lab", "user_ids": [42]},
                {"linux_username": "shared_lab", "user_ids": [42, 50]},
            ],
        )
        reverse_cache.apply_incremental(
            server_id=7,
            entries=[],
            removed_linux_usernames=["shared_lab"],
        )
        assert reverse_cache.lookup_user_ids(7, "shared_lab") == []
        assert reverse_cache.lookup_user_ids(7, "yang_lab") == [42]

    def test_full_snapshot_replaces_existing_server_slice(self) -> None:
        reverse_cache.apply_full_snapshot(
            server_id=7,
            entries=[{"linux_username": "yang_lab", "user_ids": [42]}],
        )
        # Different server — should not be touched by the next call.
        reverse_cache.apply_full_snapshot(
            server_id=9,
            entries=[{"linux_username": "other_lab", "user_ids": [99]}],
        )
        # Re-snapshot server 7 with a different set; server 9 must survive.
        reverse_cache.apply_full_snapshot(
            server_id=7,
            entries=[{"linux_username": "new_lab", "user_ids": [200]}],
        )
        assert reverse_cache.lookup_user_ids(7, "yang_lab") == []
        assert reverse_cache.lookup_user_ids(7, "new_lab") == [200]
        assert reverse_cache.lookup_user_ids(9, "other_lab") == [99]

    def test_needs_full_sync_starts_true_then_false(self) -> None:
        assert reverse_cache.needs_full_sync() is True
        reverse_cache.apply_full_snapshot(server_id=7, entries=[])
        assert reverse_cache.needs_full_sync() is False

    def test_needs_full_sync_after_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reverse_cache.apply_full_snapshot(server_id=7, entries=[])
        # Pretend a lot of time has passed.
        original_monotonic = time.monotonic
        monkeypatch.setattr(
            time,
            "monotonic",
            lambda: original_monotonic() + reverse_cache.CACHE_TTL_SECONDS + 10,
        )
        assert reverse_cache.needs_full_sync() is True


class TestPolicyCache:
    def test_apply_sync_stores_all_entries(self) -> None:
        n = policy_cache.apply_sync(
            server_id=7,
            policies=[
                {
                    "key": k,
                    "enabled": True,
                    "severity": "notify",
                    "threshold_value": None,
                    "grace_period_seconds": None,
                    "notify_admin": True,
                }
                for k in policy_cache.POLICY_KEYS
            ],
            etag="policy-deadbeef",
        )
        assert n == 8
        for k in policy_cache.POLICY_KEYS:
            entry = policy_cache.get(7, k)
            assert entry is not None
            assert entry.severity == "notify"
        assert policy_cache.etag(7) == "policy-deadbeef"

    def test_apply_sync_replaces_previous_for_same_server(self) -> None:
        policy_cache.apply_sync(
            server_id=7,
            policies=[
                {
                    "key": "no_reservation_occupy",
                    "enabled": True,
                    "severity": "notify",
                    "threshold_value": None,
                    "grace_period_seconds": 300,
                    "notify_admin": True,
                }
            ],
            etag="e1",
        )
        policy_cache.apply_sync(
            server_id=7,
            policies=[
                {
                    "key": "no_reservation_occupy",
                    "enabled": True,
                    "severity": "warn",
                    "threshold_value": None,
                    "grace_period_seconds": 60,
                    "notify_admin": True,
                }
            ],
            etag="e2",
        )
        entry = policy_cache.get(7, "no_reservation_occupy")
        assert entry is not None
        assert entry.severity == "warn"
        assert entry.grace_period_seconds == 60
        assert policy_cache.etag(7) == "e2"
