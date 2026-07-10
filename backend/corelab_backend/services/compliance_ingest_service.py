"""Phase 9 C2 — ingest agent-pushed ``agent.compliance.violation`` frames.

Closes the P8-8 third audit path (Phase 8 only landed
``operations.log`` on the agent + ``alert_event``; the third
``audit_log action='compliance.violation'`` row never wrote because no
push frame existed). Phase 9 / FU-38 adds the frame + this handler.

Pattern: alert + notifications + audit, in the caller's session. The
caller (``agent_ws.py``) commits once. ``alert_service.create_alert``
owns its own commit for at-least-once durability with respect to the
WS fan-out — handle that *outside* this service so we do not
nest-commit. We therefore call the helpers that do NOT commit
(``notification_service.create_notification`` lets the caller commit;
``audit_service.write`` flushes only).

This service is intentionally narrow: 1 frame in, 4 side-effects out.
"""

from __future__ import annotations

from typing import Any, Literal

from corelab_protocol import ComplianceViolationEvent
from sqlalchemy.ext.asyncio import AsyncSession

from ..logging_setup import get_logger
from . import audit_service, notification_service

NotificationSeverity = Literal["info", "warn", "error"]
AlertSeverity = Literal["info", "warn", "critical"]

_log = get_logger("corelab.compliance_ingest")


async def handle_violation(
    session: AsyncSession,
    *,
    event: ComplianceViolationEvent,
    lab_id: int,
) -> dict[str, Any]:
    """Persist the 3 audit + notification artefacts for one violation.

    Returns a small summary dict (``{alert_event_pending, notifications,
    audit_id}``) so the caller can log + tests can assert.

    Note on ordering: notifications + audit_log first (same session,
    same commit). The alert_event row goes through
    :mod:`alert_service.create_alert` which manages its own commit —
    the caller therefore calls ``alert_service.create_alert`` AFTER
    awaiting this function, OR (more cleanly) lets a separate task
    fire on the WS dispatcher. For Phase 9 we keep the alert insert
    inside ``agent_ws.py`` so this service stays single-commit.
    """
    payload_for_logs: dict[str, Any] = {
        "policy_key": event.policy_key,
        "severity": event.severity,
        "linux_username": event.linux_username,
        "linux_pid": event.linux_pid,
        "linked_platform_user_ids": event.linked_platform_user_ids,
        "current_reservation_holders": [
            {
                "user_id": h.user_id,
                "username": h.username,
                "reservation_id": h.reservation_id,
            }
            for h in event.current_reservation_holders
        ],
        "action_taken": event.action_taken,
        "memory_used_mb": event.memory_used_mb,
        "memory_declared_mb": event.memory_declared_mb,
        "util_pct": event.util_pct,
        "downgraded_from": event.downgraded_from,
        "details": event.details,
    }

    # 2) Notifications — fan to linked occupiers + current holders.
    #    Dedup inside notification_service swallows replays.
    notify_count = 0
    notify_type = f"compliance.{event.policy_key}"
    for uid in event.linked_platform_user_ids:
        await notification_service.create_notification(
            session,
            recipient_user_id=uid,
            type=notify_type,
            title=_compose_occupier_title(event),
            severity=_notify_severity(event.severity),
            payload={
                "alert_event_id": None,  # caller fills in after alert insert
                "server_id": event.server_id,
                "gpu_id": event.gpu_id,
                "policy_key": event.policy_key,
                "action_taken": event.action_taken,
            },
        )
        notify_count += 1
    for holder in event.current_reservation_holders:
        await notification_service.create_notification(
            session,
            recipient_user_id=holder.user_id,
            type="compliance.your_gpu_occupied",
            title=_compose_holder_title(event, holder.username),
            severity=_notify_severity(event.severity),
            payload={
                "server_id": event.server_id,
                "gpu_id": event.gpu_id,
                "reservation_id": holder.reservation_id,
                "occupier_linux_username": event.linux_username,
            },
        )
        notify_count += 1

    # 3) audit_log — P8-8 third path. action='compliance.violation' is
    #    docs/06 §6.2b "Backend 处理" line 1253 字面.
    await audit_service.write(
        session,
        action="compliance.violation",
        actor_user_id=None,  # system / agent-triggered
        lab_id=lab_id,
        target_type="gpu",
        target_id=event.gpu_id,
        target_lab_id=lab_id,
        target_server_id=event.server_id,
        payload=payload_for_logs,
        result="ok",
    )

    _log.info(
        "compliance_ingest.handled",
        server_id=event.server_id,
        gpu_id=event.gpu_id,
        policy_key=event.policy_key,
        severity=event.severity,
        notify_count=notify_count,
    )
    return {
        "notifications": notify_count,
        "audit_action": "compliance.violation",
    }


def _notify_severity(effective: str) -> NotificationSeverity:
    """Convert policy severity to notification severity vocabulary
    (notification only knows info/warn/error)."""
    if effective == "auto_kill":
        return "error"
    if effective == "warn":
        return "warn"
    return "info"


def alert_severity_for(effective: str) -> AlertSeverity:
    """Map a policy_handlers effective severity to alert_event severity."""
    mapping: dict[str, AlertSeverity] = {
        "log_only": "info",
        "notify": "info",
        "warn": "warn",
        "auto_kill": "critical",
    }
    return mapping.get(effective, "info")


def _compose_occupier_title(event: ComplianceViolationEvent) -> str:
    return f"Compliance: {event.policy_key} on GPU-{event.gpu_id} ({event.action_taken})"


def _compose_holder_title(event: ComplianceViolationEvent, occupier: str | None) -> str:
    who = occupier or "an unknown user"
    return f"GPU-{event.gpu_id} occupied by {who} (your reservation)"
