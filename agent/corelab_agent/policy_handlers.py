"""Phase 8 C4 — agent-side policy dispatcher (P8-3 / P8-7 / P8-12).

Takes a :class:`Violation` from :mod:`compliance_monitor` and routes
it to one of four severity actions per docs/04 §9.7.3:

    log_only  — local-only structured log
    notify    — push compliance.violation event to backend
    warn      — same notify + the frontend gives admin a manual kill
                button (backend decision; agent only signals state)
    auto_kill — kill the offending PID via the ``gpu.kill_process``
                capability, *iff* enabled — otherwise downgrade to warn
                (P8-7 capability x policy co-invariant)

grace_period_seconds defers the action until the violation has
persisted for that long (handled by :mod:`grace_tracker`).

The actual notify push goes through the agent's WS client via a
``notify_callback`` injected at wire-up time so this module stays
import-free of ws_client (and therefore unit-testable in isolation).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from . import capabilities, grace_tracker, policy_cache
from .logging_setup import get_logger
from .violations import Violation

log = get_logger("corelab.agent.policy_handlers")

# Most policy_keys that *might* auto-kill all need the same capability
# (gpu.kill_process). docs/02 §5.18 line 1466 + Phase 8 brief §5.
_KILL_CAPABILITY: str = "gpu.kill_process"

# auto_kill is intentionally narrow. A machine may terminate a process
# on its own in only two cases — and they mirror the backend's
# _AUTO_KILL_SETTABLE_KEYS exactly (defence in depth):
#   preempt_others_reservation — true preemption: somebody holds a live
#       reservation on this GPU and the occupier's linked platform
#       users do not. This is the case the user asked auto_kill to own.
#   script_overrun_grace — the owner's own script ran past the
#       max_runtime they declared; killing it is honoring their opt-in,
#       not seizing someone else's work. (In production this never
#       reaches this dispatcher — script_runner enforces the timeout
#       directly — but keeping it allowed makes the two layers agree.)
# Every other policy_key (memory_overuse, gpu_hang, …) downgrades to
# warn even if a hand-edited DB row says auto_kill, so the worst a
# machine does there is alert a human (the manual kill button).
_AUTO_KILL_ALLOWED_KEYS: frozenset[str] = frozenset(
    {"preempt_others_reservation", "script_overrun_grace"}
)

# Notify callback: takes the compliance-violation envelope payload +
# pushes ``agent.reverse_lookup.notify`` (or a Phase 8 alert-event push
# if/when that frame is added). For Phase 8 we wire compliance violation
# notifications onto the existing ReverseLookupNotify frame so we do
# not need a protocol bump beyond the C2 0.5.
NotifyCallback = Callable[[Violation, str], Awaitable[None]]
KillCallback = Callable[[int], Awaitable[bool]]

# Phase 9 / FU-38 — structured push of the ``agent.compliance.violation``
# frame to backend so audit_log gets the third compliance trail
# (P8-8). Optional — when None, the dispatcher falls back to the
# Phase 8 notify-only path.
ViolationEventCallback = Callable[["ViolationEventPayload"], Awaitable[None]]


class ViolationEventPayload:
    __slots__ = (
        "action_taken",
        "downgraded_from",
        "gpu_id",
        "linked_platform_user_ids",
        "linux_pid",
        "linux_username",
        "policy_key",
        "server_id",
        "severity",
    )

    def __init__(
        self,
        *,
        server_id: int,
        gpu_id: int,
        policy_key: str,
        severity: str,
        linux_username: str | None,
        linux_pid: int | None,
        linked_platform_user_ids: list[int],
        action_taken: str,
        downgraded_from: str | None,
    ) -> None:
        self.server_id = server_id
        self.gpu_id = gpu_id
        self.policy_key = policy_key
        self.severity = severity
        self.linux_username = linux_username
        self.linux_pid = linux_pid
        self.linked_platform_user_ids = linked_platform_user_ids
        self.action_taken = action_taken
        self.downgraded_from = downgraded_from


class PolicyDispatcher:
    """Stateful dispatcher — knows the server it's running for + the
    callbacks for WS notify / kill. Created once at agent startup and
    handed to :class:`ComplianceMonitor`."""

    def __init__(
        self,
        *,
        server_id: int,
        notify_callback: NotifyCallback | None = None,
        kill_callback: KillCallback | None = None,
        violation_event_callback: ViolationEventCallback | None = None,
    ) -> None:
        self._server_id = server_id
        self._notify = notify_callback
        self._kill = kill_callback
        # Phase 9 / FU-38 — when set, dispatcher emits the structured
        # ``agent.compliance.violation`` push event after routing.
        self._violation_event = violation_event_callback

    async def __call__(self, violation: Violation) -> str:
        """Dispatch one violation. Returns the severity that was
        actually applied (which may differ from configured if
        auto_kill was downgraded to warn due to capability=off).
        """
        entry = policy_cache.get(self._server_id, violation.policy_key)
        if entry is None or not entry.enabled:
            # Policy not yet pushed by backend, or admin disabled it →
            # log_only fallback so the audit trail still shows the
            # detection happened.
            log.info(
                "policy.disabled_or_unknown",
                policy_key=violation.policy_key,
                payload=violation.payload,
            )
            return "log_only"

        # grace_period — defer if the policy says so.
        should_act = grace_tracker.record(
            policy_key=violation.policy_key,
            server_id=violation.server_id,
            gpu_id=violation.gpu_id_local,
            linux_username=violation.linux_username,
            pid=violation.pid,
            grace_period_seconds=entry.grace_period_seconds,
        )
        if not should_act:
            log.debug(
                "policy.grace_pending",
                policy_key=violation.policy_key,
                grace_period_seconds=entry.grace_period_seconds,
            )
            return "deferred"

        effective = entry.severity
        downgraded_from: str | None = None

        # Scope guard — auto_kill is only ever honored for true
        # preemption. Any other policy_key configured to auto_kill is
        # downgraded to warn here, so the worst a machine does on a
        # memory/hang/temp violation is alert a human.
        if effective == "auto_kill" and violation.policy_key not in _AUTO_KILL_ALLOWED_KEYS:
            log.info(
                "policy.auto_kill_downgraded_to_warn_scope",
                policy_key=violation.policy_key,
            )
            effective = "warn"
            downgraded_from = "auto_kill"

        # Capability x policy co-invariant (P8-7) — if auto_kill but the
        # kill capability is off, downgrade to warn so the agent will
        # notify + give backend the chance to escalate manually.
        if effective == "auto_kill" and not capabilities.is_enabled(
            _KILL_CAPABILITY, default=False
        ):
            log.info(
                "policy.auto_kill_downgraded_to_warn_capability_off",
                policy_key=violation.policy_key,
                capability=_KILL_CAPABILITY,
            )
            effective = "warn"
            downgraded_from = "auto_kill"

        action_taken = await self._route(effective, violation)

        # Phase 9 / FU-38 — emit the structured event for the third
        # audit path. Failures are non-fatal so a backend outage does
        # not break local enforcement.
        if self._violation_event is not None:
            payload = ViolationEventPayload(
                server_id=self._server_id,
                gpu_id=violation.gpu_id_local,
                policy_key=violation.policy_key,
                severity=effective,
                linux_username=violation.linux_username,
                linux_pid=violation.pid,
                linked_platform_user_ids=list(violation.linked_user_ids or []),
                action_taken=action_taken,
                downgraded_from=downgraded_from,
            )
            try:
                await self._violation_event(payload)
            except Exception as exc:
                log.warning(
                    "policy.violation_event_push_failed",
                    policy_key=violation.policy_key,
                    error=str(exc),
                )
        return effective

    async def _route(self, severity: str, violation: Violation) -> str:
        """Apply the configured severity. Returns ``action_taken`` —
        one of ``log_only`` / ``notify`` / ``warn`` / ``kill`` /
        ``kill_downgraded_to_warn`` — for the Phase 9 violation event
        push (FU-38)."""
        if severity == "log_only":
            log.info(
                "compliance.log_only",
                policy_key=violation.policy_key,
                gpu_id=violation.gpu_id_local,
                linux_username=violation.linux_username,
                pid=violation.pid,
                payload=violation.payload,
            )
            return "log_only"
        if severity == "notify":
            await self._do_notify(violation, severity)
            return "notify"
        if severity == "warn":
            await self._do_notify(violation, severity)
            return "warn"
        if severity == "auto_kill":
            # Kill the PID first so the bad behavior stops, then notify
            # so the operator audit trail shows what was done.
            killed = False
            if violation.pid is not None and self._kill is not None:
                try:
                    killed = await self._kill(violation.pid)
                except Exception as exc:
                    log.warning(
                        "policy.kill_failed",
                        pid=violation.pid,
                        policy_key=violation.policy_key,
                        error=str(exc),
                    )
            violation_with_action = Violation(
                policy_key=violation.policy_key,
                server_id=violation.server_id,
                gpu_id_local=violation.gpu_id_local,
                linux_username=violation.linux_username,
                pid=violation.pid,
                linked_user_ids=violation.linked_user_ids,
                payload={**violation.payload, "action_taken": "killed", "killed": killed},
            )
            await self._do_notify(violation_with_action, severity)
            return "kill" if killed else "kill_downgraded_to_warn"
        log.warning("policy.unknown_severity", severity=severity)
        return "log_only"

    async def _do_notify(self, violation: Violation, severity: str) -> None:
        if self._notify is None:
            log.debug(
                "policy.notify_dropped",
                reason="no_callback",
                policy_key=violation.policy_key,
            )
            return
        try:
            await self._notify(violation, severity)
        except Exception as exc:
            log.warning(
                "policy.notify_failed",
                policy_key=violation.policy_key,
                error=str(exc),
            )


# 8 policy_key handler names — exported as a constant so the test suite
# can assert all are referenced from the routing logic. The
# dispatcher itself reads them from policy_cache (key-driven).
HANDLER_FUNCTIONS: tuple[str, ...] = policy_cache.POLICY_KEYS


# Helper that the wire-up code in ws_client can use to compose a
# notify payload for the backend. Kept here next to the routing logic
# so payload shape and routing decisions co-evolve.
def compose_notify_payload(violation: Violation, severity: str) -> dict[str, Any]:
    return {
        "policy_key": violation.policy_key,
        "server_id": violation.server_id,
        "gpu_id_local": violation.gpu_id_local,
        "linux_username": violation.linux_username,
        "pid": violation.pid,
        "linked_user_ids": violation.linked_user_ids,
        "severity": severity,
        "payload": violation.payload,
    }
