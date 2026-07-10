"""Phase 9 C2 — PolicyDispatcher fires the violation_event_callback
after routing (FU-38 agent side).

The callback receives a ``ViolationEventPayload`` with the effective
severity, action_taken, and downgraded_from fields the backend
ingest needs.
"""

from __future__ import annotations

import pytest
from corelab_agent import capabilities, grace_tracker, policy_cache
from corelab_agent.policy_handlers import (
    PolicyDispatcher,
    ViolationEventPayload,
)
from corelab_agent.violations import Violation


@pytest.fixture(autouse=True)
def _reset() -> None:
    policy_cache.reset()
    grace_tracker.reset()
    capabilities.clear()


def _seed_policy(
    *,
    key: str,
    severity: str,
    grace_period: int | None = None,
) -> None:
    policy_cache.apply_sync(
        server_id=7,
        policies=[
            {
                "key": key,
                "enabled": True,
                "severity": severity,
                "threshold_value": None,
                "grace_period_seconds": grace_period,
                "notify_admin": False,
            }
        ],
        etag="e1",
    )


@pytest.mark.asyncio
async def test_callback_receives_payload_for_warn() -> None:
    _seed_policy(key="preempt_others_reservation", severity="warn")
    captured: list[ViolationEventPayload] = []

    async def violation_cb(p: ViolationEventPayload) -> None:
        captured.append(p)

    dispatcher = PolicyDispatcher(
        server_id=7,
        violation_event_callback=violation_cb,
    )
    v = Violation(
        policy_key="preempt_others_reservation",
        server_id=7,
        gpu_id_local=3,
        linux_username="yang_lab",
        pid=22222,
        linked_user_ids=[42, 50],
        payload={"memory_used_mb": 1024},
    )
    severity = await dispatcher(v)
    assert severity == "warn"
    assert len(captured) == 1
    payload = captured[0]
    assert payload.policy_key == "preempt_others_reservation"
    assert payload.severity == "warn"
    assert payload.action_taken == "warn"
    assert payload.linux_username == "yang_lab"
    assert payload.linux_pid == 22222
    assert payload.linked_platform_user_ids == [42, 50]
    assert payload.downgraded_from is None


@pytest.mark.asyncio
async def test_callback_records_downgraded_from_when_capability_off() -> None:
    """auto_kill + gpu.kill_process capability=off → severity downgrades
    to warn AND downgraded_from='auto_kill' lands on the payload."""
    _seed_policy(key="gpu_hang", severity="auto_kill")
    # Capability default is off in test env (capabilities.reset_for_tests()).
    captured: list[ViolationEventPayload] = []

    async def violation_cb(p: ViolationEventPayload) -> None:
        captured.append(p)

    dispatcher = PolicyDispatcher(
        server_id=7,
        violation_event_callback=violation_cb,
    )
    v = Violation(
        policy_key="gpu_hang",
        server_id=7,
        gpu_id_local=0,
        linux_username=None,
        pid=None,
        linked_user_ids=[],
        payload={"util_zero_for_s": 700},
    )
    severity = await dispatcher(v)
    assert severity == "warn"  # downgraded
    assert len(captured) == 1
    payload = captured[0]
    assert payload.severity == "warn"
    assert payload.downgraded_from == "auto_kill"


@pytest.mark.asyncio
async def test_no_callback_does_not_crash() -> None:
    """Dispatcher must survive without a violation_event_callback —
    Phase 8 callers do not pass one."""
    _seed_policy(key="zombie_process", severity="notify")
    dispatcher = PolicyDispatcher(server_id=7)
    v = Violation(
        policy_key="zombie_process",
        server_id=7,
        gpu_id_local=1,
        linux_username="bob",
        pid=99,
        linked_user_ids=[10],
        payload={},
    )
    severity = await dispatcher(v)
    assert severity == "notify"


@pytest.mark.asyncio
async def test_callback_failure_does_not_propagate() -> None:
    _seed_policy(key="zombie_process", severity="notify")

    async def bad_cb(p: ViolationEventPayload) -> None:
        raise RuntimeError("network down")

    dispatcher = PolicyDispatcher(
        server_id=7,
        violation_event_callback=bad_cb,
    )
    v = Violation(
        policy_key="zombie_process",
        server_id=7,
        gpu_id_local=1,
        linux_username="bob",
        pid=99,
        linked_user_ids=[],
        payload={},
    )
    # Must not raise — failures are warn-logged only.
    severity = await dispatcher(v)
    assert severity == "notify"


@pytest.mark.asyncio
async def test_no_callback_for_log_only_still_runs_through_route() -> None:
    """log_only path runs _route → returns 'log_only' action_taken."""
    _seed_policy(key="zombie_process", severity="log_only")
    captured: list[ViolationEventPayload] = []

    async def violation_cb(p: ViolationEventPayload) -> None:
        captured.append(p)

    dispatcher = PolicyDispatcher(
        server_id=7,
        violation_event_callback=violation_cb,
    )
    v = Violation(
        policy_key="zombie_process",
        server_id=7,
        gpu_id_local=1,
        linux_username="bob",
        pid=99,
        linked_user_ids=[],
        payload={},
    )
    severity = await dispatcher(v)
    assert severity == "log_only"
    assert len(captured) == 1
    assert captured[0].action_taken == "log_only"


def test_violation_event_payload_repr() -> None:
    p = ViolationEventPayload(
        server_id=7,
        gpu_id=0,
        policy_key="gpu_hang",
        severity="warn",
        linux_username=None,
        linux_pid=None,
        linked_platform_user_ids=[],
        action_taken="warn",
        downgraded_from="auto_kill",
    )
    assert p.server_id == 7
    assert p.downgraded_from == "auto_kill"
