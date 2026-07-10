"""Phase 9 C2 — FU-38 agent.compliance.violation ingest (P9-4 / P9-5).

Covers the P8-8 "third audit path" closure: an agent-pushed
compliance violation must land in alert_event, notify the linked
occupiers + current reservation holders, AND write audit_log with
action='compliance.violation'.

Also covers the docs/06 §3.4 reconcile — frame type is now
``agent.compliance.violation`` (was ``compliance.violation`` in
§6.2b).
"""

from __future__ import annotations

from typing import Any

import pytest_asyncio
from corelab_backend.db import get_session_factory
from corelab_backend.models import (
    AlertEvent,
    AuditLog,
    Gpu,
    Lab,
    Notification,
    Server,
    User,
)
from corelab_backend.security import hash_password
from corelab_backend.services import compliance_ingest_service
from corelab_protocol import (
    AGENT_TO_BACKEND_TYPES,
    ComplianceViolationEvent,
    ComplianceViolationHolder,
    MessageEnvelope,
    parse_envelope,
)
from httpx import AsyncClient
from sqlalchemy import select


@pytest_asyncio.fixture
async def world(integration_client: AsyncClient) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="P9 Compliance", slug="p9-comp")
        session.add(lab)
        await session.flush()
        admin = User(
            lab_id=lab.id,
            username="admin",
            email="admin@comp.test",
            display_name="Admin",
            password_hash=hash_password("Pass!2024"),  # pragma: allowlist secret
            role="lab_admin",
        )
        occupier = User(
            lab_id=lab.id,
            username="occupier",
            email="occupier@comp.test",
            display_name="Occupier",
            password_hash=hash_password("Pass!2024"),  # pragma: allowlist secret
            role="user",
        )
        holder = User(
            lab_id=lab.id,
            username="holder",
            email="holder@comp.test",
            display_name="Holder",
            password_hash=hash_password("Pass!2024"),  # pragma: allowlist secret
            role="user",
        )
        session.add_all([admin, occupier, holder])
        await session.flush()
        server = Server(
            lab_id=lab.id,
            hostname="gpu-comp.test",
            display_name="Comp GPU",
            status="online",
            created_by_user_id=admin.id,
        )
        session.add(server)
        await session.flush()
        # alert_event.gpu_id has an FK to gpu — seed one so the WS leg
        # of the test can persist its alert row.
        gpu = Gpu(
            server_id=server.id,
            gpu_index=5,
            uuid="GPU-test-5",
            model="RTX 4090",
            memory_total_mb=24576,
        )
        session.add(gpu)
        await session.flush()
    return {
        "client": integration_client,
        "lab_id": lab.id,
        "admin_id": admin.id,
        "occupier_id": occupier.id,
        "holder_id": holder.id,
        "server_id": server.id,
        "gpu_id": gpu.id,
    }


def _build_event(world: dict[str, Any]) -> ComplianceViolationEvent:
    return ComplianceViolationEvent(
        server_id=world["server_id"],
        gpu_id=world["gpu_id"],
        policy_key="preempt_others_reservation",
        severity="warn",
        linux_username="yang_lab",
        linux_pid=22222,
        linked_platform_user_ids=[world["occupier_id"]],
        current_reservation_holders=[
            ComplianceViolationHolder(
                user_id=world["holder_id"],
                username="holder",
                reservation_id=105,
            )
        ],
        action_taken="warn",
        memory_used_mb=18432,
        memory_declared_mb=None,
        util_pct=95,
        downgraded_from=None,
        details="occupier preempts holder's reservation",
    )


class TestProtocolFrame:
    def test_round_trip_via_envelope(self) -> None:
        evt = ComplianceViolationEvent(
            server_id=10,
            gpu_id=5,
            policy_key="gpu_hang",
            severity="notify",
            linux_username="alice",
            linux_pid=42,
            linked_platform_user_ids=[1, 2],
            current_reservation_holders=[],
            action_taken="notify",
            memory_used_mb=1024,
            util_pct=0,
        )
        env = MessageEnvelope(
            type="agent.compliance.violation",
            payload=evt.model_dump(mode="json"),
        )
        parsed_env, payload = parse_envelope(env.model_dump(mode="json"))
        assert parsed_env.type == "agent.compliance.violation"
        assert isinstance(payload, ComplianceViolationEvent)
        assert payload.policy_key == "gpu_hang"
        assert payload.linked_platform_user_ids == [1, 2]

    def test_frame_type_registered_agent_to_backend(self) -> None:
        assert "agent.compliance.violation" in AGENT_TO_BACKEND_TYPES

    def test_kill_downgraded_action_taken_enum(self) -> None:
        evt = ComplianceViolationEvent(
            server_id=1,
            gpu_id=0,
            policy_key="gpu_hang",
            severity="warn",
            action_taken="kill_downgraded_to_warn",
            downgraded_from="auto_kill",
        )
        assert evt.action_taken == "kill_downgraded_to_warn"
        assert evt.downgraded_from == "auto_kill"


class TestIngestThreeAuditPaths:
    async def test_handle_writes_audit_log_third_path(self, world: dict[str, Any]) -> None:
        evt = _build_event(world)
        factory = get_session_factory()
        async with factory() as session:
            result = await compliance_ingest_service.handle_violation(
                session, event=evt, lab_id=world["lab_id"]
            )
            await session.commit()
        assert result["audit_action"] == "compliance.violation"
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == "compliance.violation")
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        audit_row = rows[0]
        assert audit_row.target_server_id == world["server_id"]
        assert audit_row.target_type == "gpu"
        assert audit_row.target_id == world["gpu_id"]
        assert audit_row.actor_user_id is None  # system/agent triggered
        assert audit_row.payload is not None
        assert audit_row.payload["policy_key"] == "preempt_others_reservation"
        assert audit_row.payload["action_taken"] == "warn"

    async def test_handle_creates_notifications_for_linked_and_holders(
        self, world: dict[str, Any]
    ) -> None:
        evt = _build_event(world)
        factory = get_session_factory()
        async with factory() as session:
            await compliance_ingest_service.handle_violation(
                session, event=evt, lab_id=world["lab_id"]
            )
            await session.commit()
        async with factory() as session:
            occupier_notifs = (
                (
                    await session.execute(
                        select(Notification).where(
                            Notification.recipient_user_id == world["occupier_id"],
                            Notification.type == "compliance.preempt_others_reservation",
                        )
                    )
                )
                .scalars()
                .all()
            )
            holder_notifs = (
                (
                    await session.execute(
                        select(Notification).where(
                            Notification.recipient_user_id == world["holder_id"],
                            Notification.type == "compliance.your_gpu_occupied",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(occupier_notifs) == 1
        assert len(holder_notifs) == 1


class TestAgentWsIntegration:
    """End-to-end: simulate the WS push that Phase 8 left as a TODO."""

    async def test_full_three_paths_via_ws_handler(self, world: dict[str, Any]) -> None:
        """alert_event + notification + audit_log all written by the
        full handler chain in ``agent_ws.py``."""
        # The agent_ws.py path runs ingest_service.handle_violation
        # (notifications + audit) and then alert_service.create_alert
        # (alert_event row). We exercise both legs explicitly here so
        # the test does not need the full WS connection harness.
        from corelab_backend.services import alert_service

        evt = _build_event(world)
        factory = get_session_factory()
        async with factory() as session:
            await compliance_ingest_service.handle_violation(
                session, event=evt, lab_id=world["lab_id"]
            )
            await session.commit()
        async with factory() as session:
            await alert_service.create_alert(
                session,
                server_id=evt.server_id,
                gpu_id=evt.gpu_id,
                event_type=f"compliance.{evt.policy_key}",
                severity=compliance_ingest_service.alert_severity_for(evt.severity),
                payload={"policy_key": evt.policy_key, "action_taken": evt.action_taken},
            )

        async with factory() as session:
            alert_rows = (await session.execute(select(AlertEvent))).scalars().all()
            audit_rows = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == "compliance.violation")
                    )
                )
                .scalars()
                .all()
            )
            notif_count = len((await session.execute(select(Notification))).scalars().all())

        # P8-8 三处 audit 全齐: alert_event + audit_log + (operations.log
        # exercised on the agent side; covered by agent unit tests).
        assert len(alert_rows) == 1
        assert alert_rows[0].event_type == "compliance.preempt_others_reservation"
        assert len(audit_rows) == 1
        # 1 occupier notification + 1 holder notification.
        assert notif_count == 2
