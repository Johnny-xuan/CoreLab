"""Phase 8 C4 — policy_handlers dispatcher + grace_tracker tests
(P8-3 / P8-7 / P8-12 / P8-13).

Covers:
* 4 severity routing: log_only / notify / warn / auto_kill
* grace_period 0 → immediate act
* grace_period N → first call deferred, second call past N → act
* auto_kill + capability gpu.kill_process=OFF → downgrade to warn (no kill)
* auto_kill + capability ON → kill_callback invoked + notify includes action_taken=killed
* disabled or unknown policy_key → log_only fallback
* 8 policy_key constant matches the agent-side POLICY_KEYS tuple
"""

from __future__ import annotations

import time

import pytest
from corelab_agent import capabilities, grace_tracker, policy_cache, policy_handlers
from corelab_agent.violations import Violation


@pytest.fixture(autouse=True)
def _reset_everything() -> None:
    policy_cache.reset()
    grace_tracker.reset()
    capabilities.clear()


def _seed_policy(
    server_id: int,
    key: str,
    severity: str,
    grace: int | None = None,
    threshold: int | None = None,
) -> None:
    policy_cache.apply_sync(
        server_id=server_id,
        policies=[
            {
                "key": key,
                "enabled": True,
                "severity": severity,
                "threshold_value": threshold,
                "grace_period_seconds": grace,
                "notify_admin": True,
            }
        ],
        etag=f"e-{key}-{severity}",
    )


def _violation(
    policy_key: str = "no_reservation_occupy",
    pid: int = 999,
    linux_username: str = "ivy_lab",
) -> Violation:
    return Violation(
        policy_key=policy_key,
        server_id=7,
        gpu_id_local=0,
        linux_username=linux_username,
        pid=pid,
        linked_user_ids=[42],
        payload={"memory_used_mb": 8000},
    )


class TestSeverityRouting:
    async def test_log_only_does_not_call_notify(self) -> None:
        notify_calls: list[Violation] = []

        async def notify(v: Violation, sev: str) -> None:
            notify_calls.append(v)

        _seed_policy(7, "no_reservation_occupy", "log_only")
        dispatcher = policy_handlers.PolicyDispatcher(server_id=7, notify_callback=notify)
        applied = await dispatcher(_violation())
        assert applied == "log_only"
        assert notify_calls == []

    async def test_notify_invokes_callback(self) -> None:
        notify_calls: list[tuple[Violation, str]] = []

        async def notify(v: Violation, sev: str) -> None:
            notify_calls.append((v, sev))

        _seed_policy(7, "no_reservation_occupy", "notify")
        dispatcher = policy_handlers.PolicyDispatcher(server_id=7, notify_callback=notify)
        applied = await dispatcher(_violation())
        assert applied == "notify"
        assert len(notify_calls) == 1
        assert notify_calls[0][1] == "notify"

    async def test_warn_invokes_callback_with_warn(self) -> None:
        notify_calls: list[tuple[Violation, str]] = []

        async def notify(v: Violation, sev: str) -> None:
            notify_calls.append((v, sev))

        _seed_policy(7, "no_reservation_occupy", "warn")
        dispatcher = policy_handlers.PolicyDispatcher(server_id=7, notify_callback=notify)
        applied = await dispatcher(_violation())
        assert applied == "warn"
        assert notify_calls[0][1] == "warn"


class TestAutoKillCapabilitySynergy:
    async def test_auto_kill_with_capability_on_invokes_kill(self) -> None:
        capabilities.set_enabled("gpu.kill_process", True)
        killed_pids: list[int] = []
        notify_calls: list[tuple[Violation, str]] = []

        async def killer(pid: int) -> bool:
            killed_pids.append(pid)
            return True

        async def notify(v: Violation, sev: str) -> None:
            notify_calls.append((v, sev))

        _seed_policy(7, "script_overrun_grace", "auto_kill")
        dispatcher = policy_handlers.PolicyDispatcher(
            server_id=7, notify_callback=notify, kill_callback=killer
        )
        applied = await dispatcher(_violation(policy_key="script_overrun_grace", pid=12345))
        assert applied == "auto_kill"
        assert killed_pids == [12345]
        assert notify_calls[0][1] == "auto_kill"
        assert notify_calls[0][0].payload.get("action_taken") == "killed"
        assert notify_calls[0][0].payload.get("killed") is True

    async def test_auto_kill_with_capability_off_downgrades_to_warn(self) -> None:
        """P8-7 — even if admin set policy=auto_kill, capability OFF →
        agent never kills, downgrades to warn."""
        capabilities.set_enabled("gpu.kill_process", False)
        killed_pids: list[int] = []
        notify_calls: list[tuple[Violation, str]] = []

        async def killer(pid: int) -> bool:
            killed_pids.append(pid)
            return True

        async def notify(v: Violation, sev: str) -> None:
            notify_calls.append((v, sev))

        _seed_policy(7, "script_overrun_grace", "auto_kill")
        dispatcher = policy_handlers.PolicyDispatcher(
            server_id=7, notify_callback=notify, kill_callback=killer
        )
        applied = await dispatcher(_violation(policy_key="script_overrun_grace", pid=12345))
        assert applied == "warn"
        assert killed_pids == [], "kill must not fire when capability is off"
        assert notify_calls[0][1] == "warn"


class TestAutoKillScopeGuard:
    """auto_kill is honored ONLY for preempt_others_reservation (+ the
    owner's own script_overrun_grace). Any other policy_key set to
    auto_kill downgrades to warn agent-side, even with capability ON."""

    async def test_non_preempt_auto_kill_downgrades(self) -> None:
        capabilities.set_enabled("gpu.kill_process", True)
        killed_pids: list[int] = []
        notify_calls: list[tuple[Violation, str]] = []

        async def killer(pid: int) -> bool:
            killed_pids.append(pid)
            return True

        async def notify(v: Violation, sev: str) -> None:
            notify_calls.append((v, sev))

        # memory_overuse=auto_kill should NOT kill — scope guard wins.
        _seed_policy(7, "memory_overuse", "auto_kill", grace=0)
        dispatcher = policy_handlers.PolicyDispatcher(
            server_id=7, notify_callback=notify, kill_callback=killer
        )
        applied = await dispatcher(_violation(policy_key="memory_overuse", pid=777))
        assert applied == "warn"
        assert killed_pids == [], "non-preempt key must never auto-kill"
        assert notify_calls[0][1] == "warn"

    async def test_preempt_auto_kill_still_kills(self) -> None:
        capabilities.set_enabled("gpu.kill_process", True)
        killed_pids: list[int] = []

        async def killer(pid: int) -> bool:
            killed_pids.append(pid)
            return True

        async def notify(v: Violation, sev: str) -> None:
            return None

        _seed_policy(7, "preempt_others_reservation", "auto_kill", grace=0)
        dispatcher = policy_handlers.PolicyDispatcher(
            server_id=7, notify_callback=notify, kill_callback=killer
        )
        applied = await dispatcher(_violation(policy_key="preempt_others_reservation", pid=888))
        assert applied == "auto_kill"
        assert killed_pids == [888], "preemption is the one case a machine may kill"


class TestGracePeriod:
    async def test_grace_zero_acts_immediately(self) -> None:
        notify_calls: list[tuple[Violation, str]] = []

        async def notify(v: Violation, sev: str) -> None:
            notify_calls.append((v, sev))

        _seed_policy(7, "memory_overuse", "notify", grace=0)
        dispatcher = policy_handlers.PolicyDispatcher(server_id=7, notify_callback=notify)
        applied = await dispatcher(_violation(policy_key="memory_overuse"))
        assert applied == "notify"
        assert len(notify_calls) == 1

    async def test_grace_period_defers_first_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        notify_calls: list[tuple[Violation, str]] = []

        async def notify(v: Violation, sev: str) -> None:
            notify_calls.append((v, sev))

        _seed_policy(7, "no_reservation_occupy", "notify", grace=300)
        dispatcher = policy_handlers.PolicyDispatcher(server_id=7, notify_callback=notify)
        applied1 = await dispatcher(_violation())
        assert applied1 == "deferred"
        assert notify_calls == []

        # Advance time past the grace window.
        real = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: real + 301.0)
        applied2 = await dispatcher(_violation())
        assert applied2 == "notify"
        assert len(notify_calls) == 1


class TestPolicyDisabledOrUnknown:
    async def test_unknown_policy_falls_back_to_log_only(self) -> None:
        notify_calls: list[tuple[Violation, str]] = []

        async def notify(v: Violation, sev: str) -> None:
            notify_calls.append((v, sev))

        # No policy cached — pure fallback.
        dispatcher = policy_handlers.PolicyDispatcher(server_id=7, notify_callback=notify)
        applied = await dispatcher(_violation())
        assert applied == "log_only"
        assert notify_calls == []

    async def test_disabled_policy_falls_back_to_log_only(self) -> None:
        notify_calls: list[tuple[Violation, str]] = []

        async def notify(v: Violation, sev: str) -> None:
            notify_calls.append((v, sev))

        policy_cache.apply_sync(
            server_id=7,
            policies=[
                {
                    "key": "no_reservation_occupy",
                    "enabled": False,
                    "severity": "warn",
                    "threshold_value": None,
                    "grace_period_seconds": None,
                    "notify_admin": True,
                }
            ],
            etag="e-disabled",
        )
        dispatcher = policy_handlers.PolicyDispatcher(server_id=7, notify_callback=notify)
        applied = await dispatcher(_violation())
        assert applied == "log_only"
        assert notify_calls == []


class TestPolicyKeyCoverage:
    def test_all_eight_keys_referenced_in_handlers_module(self) -> None:
        # P8-3 — 8 policy_key constant matches docs/02 §5.18.
        assert set(policy_handlers.HANDLER_FUNCTIONS) == set(policy_cache.POLICY_KEYS)
        assert len(policy_handlers.HANDLER_FUNCTIONS) == 8


class TestGraceTracker:
    def test_zero_grace_returns_true_immediately(self) -> None:
        assert (
            grace_tracker.record(
                policy_key="x",
                server_id=7,
                gpu_id=0,
                linux_username="u",
                pid=1,
                grace_period_seconds=0,
            )
            is True
        )

    def test_n_grace_first_call_false(self) -> None:
        assert (
            grace_tracker.record(
                policy_key="x",
                server_id=7,
                gpu_id=0,
                linux_username="u",
                pid=1,
                grace_period_seconds=60,
            )
            is False
        )
        # Same key, same tick: still false (not enough time passed).
        assert (
            grace_tracker.record(
                policy_key="x",
                server_id=7,
                gpu_id=0,
                linux_username="u",
                pid=1,
                grace_period_seconds=60,
            )
            is False
        )
        assert grace_tracker.pending_count() == 1

    def test_clear_drops_entry(self) -> None:
        grace_tracker.record(
            policy_key="x",
            server_id=7,
            gpu_id=0,
            linux_username="u",
            pid=1,
            grace_period_seconds=60,
        )
        grace_tracker.clear(
            policy_key="x",
            server_id=7,
            gpu_id=0,
            linux_username="u",
            pid=1,
        )
        assert grace_tracker.pending_count() == 0
