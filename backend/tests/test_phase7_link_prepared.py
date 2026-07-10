"""Phase 7 follow-up — link.prepared notification (FU-32 / P7-8 5th type).

approve_request → emits a link.prepared notification to the requester.
Two paths covered: push succeeded (severity=info) + push failed
agent-unreachable (severity=warn). Both still emit the notification —
the user needs to know admin took action either way.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from corelab_backend.db import get_session_factory
from corelab_backend.models import (
    AuditLog,
    AuthorizedKeyEntry,
    Lab,
    Notification,
    PhysicalAccount,
    Server,
    SshPublicKey,
    User,
)
from corelab_backend.security import hash_password
from corelab_backend.services import (
    account_link_request_service,
    agent_rpc,
    physical_account_service,
)
from httpx import AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def world(integration_client: AsyncClient) -> dict[str, Any]:
    del integration_client
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="Phase7 link.prepared Lab", slug="phase7-link-prep")
        session.add(lab)
        await session.flush()
        admin = User(
            lab_id=lab.id,
            username="adm",
            email="adm@lp.test",
            display_name="Admin",
            password_hash=hash_password("AdmPass!2024"),  # pragma: allowlist secret
            role="lab_admin",
        )
        ivy = User(
            lab_id=lab.id,
            username="ivy",
            email="ivy@lp.test",
            display_name="Ivy",
            password_hash=hash_password("IvyPass!2024"),  # pragma: allowlist secret
            role="user",
        )
        session.add_all([admin, ivy])
        await session.flush()
        server = Server(
            lab_id=lab.id,
            hostname="gpu-lp.lp",
            display_name="GPU LP",
            status="online",
            created_by_user_id=admin.id,
        )
        session.add(server)
        await session.flush()
        pa = PhysicalAccount(
            server_id=server.id,
            linux_username="ivy_lab",
            uid=1200,
            source="admin_manual_register",
            created_by_user_id=admin.id,
        )
        session.add(pa)
        await session.flush()
        # Ivy needs an active SSH key for approve to succeed.
        key = SshPublicKey(
            user_id=ivy.id,
            public_key="ssh-ed25519 AAAA test-key",  # pragma: allowlist secret
            fingerprint_sha256="SHA256:linkpreparedfingerprint01234567890123456789",
            key_type="ssh-ed25519",
            comment="laptop",
            is_active=1,
        )
        session.add(key)
        await session.flush()

    factory2 = get_session_factory()
    async with factory2() as session:
        req = await account_link_request_service.create_request(
            session,
            requester_user_id=ivy.id,
            physical_account_id=pa.id,
            request_note=None,
            lab_id=lab.id,
        )
        await session.commit()

    return {
        "lab_id": lab.id,
        "admin_id": admin.id,
        "ivy_id": ivy.id,
        "pa_id": pa.id,
        "server_id": server.id,
        "request_id": req.id,
    }


async def test_approve_emits_link_prepared_info_on_push_ok(
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_request(**_: Any) -> dict[str, Any]:
        return {
            "installed_path": "/home/ivy_lab/.ssh/authorized_keys",
            "fingerprint": "SHA256:linkprepared",
        }

    monkeypatch.setattr(agent_rpc, "request_response", fake_request)

    factory = get_session_factory()
    async with factory() as session, session.begin():
        await account_link_request_service.approve_request(
            session,
            request_id=world["request_id"],
            decision_note="all good",
            admin_user_id=world["admin_id"],
            lab_id=world["lab_id"],
        )

    async with factory() as session:
        notifs = (
            (
                await session.execute(
                    select(Notification).where(
                        Notification.recipient_user_id == world["ivy_id"],
                        Notification.type == "link.prepared",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(notifs) == 1
        notif = notifs[0]
        assert notif.severity == "info"
        assert notif.payload is not None
        assert notif.payload.get("push_ok") is True
        assert notif.payload.get("account_link_request_id") == world["request_id"]


async def test_approve_emits_link_prepared_warn_on_push_failure(
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_request(**_: Any) -> dict[str, Any]:
        raise agent_rpc.AgentOfflineError("no live agent connection")

    monkeypatch.setattr(agent_rpc, "request_response", fake_request)

    factory = get_session_factory()
    async with factory() as session, session.begin():
        await account_link_request_service.approve_request(
            session,
            request_id=world["request_id"],
            decision_note="agent offline",
            admin_user_id=world["admin_id"],
            lab_id=world["lab_id"],
        )

    async with factory() as session:
        notifs = (
            (
                await session.execute(
                    select(Notification).where(
                        Notification.recipient_user_id == world["ivy_id"],
                        Notification.type == "link.prepared",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(notifs) == 1
        assert notifs[0].severity == "warn"
        payload = notifs[0].payload or {}
        assert payload.get("push_ok") is False


async def test_retry_push_after_failed_approval_activates_key_entry(
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def offline_request(**_: Any) -> dict[str, Any]:
        raise agent_rpc.AgentOfflineError("no live agent connection")

    monkeypatch.setattr(agent_rpc, "request_response", offline_request)

    factory = get_session_factory()
    async with factory() as session, session.begin():
        await account_link_request_service.approve_request(
            session,
            request_id=world["request_id"],
            decision_note="agent offline first",
            admin_user_id=world["admin_id"],
            lab_id=world["lab_id"],
        )

    async def retry_ok(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["frame_type"] == "backend.authorized_key.push"
        assert kwargs["payload"]["linux_username"] == "ivy_lab"
        assert "retry=1" in kwargs["payload"]["label"]
        return {
            "installed_path": "/home/ivy_lab/.ssh/authorized_keys",
            "fingerprint": "SHA256:retryfingerprint",
        }

    monkeypatch.setattr(agent_rpc, "request_response", retry_ok)

    async with factory() as session, session.begin():
        _row, outcome = await account_link_request_service.retry_push_for_request(
            session,
            request_id=world["request_id"],
            admin_user_id=world["admin_id"],
            lab_id=world["lab_id"],
        )
        assert outcome["ok"] is True
        entry_id = outcome["authorized_key_entry_id"]

    async with factory() as session:
        entry = await session.get(AuthorizedKeyEntry, entry_id)
        assert entry is not None
        assert entry.is_active == 1
        assert entry.physical_account_id == world["pa_id"]
        assert entry.pushed_for_user_id == world["ivy_id"]
        retry_audits = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "account_link_request.key_push_retried",
                        AuditLog.target_id == world["request_id"],
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(retry_audits) == 1
        retry_notif = (
            (
                await session.execute(
                    select(Notification).where(
                        Notification.recipient_user_id == world["ivy_id"],
                        Notification.type == "link.prepared",
                        Notification.severity == "info",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert any((n.payload or {}).get("retry") is True for n in retry_notif)


async def test_retry_push_skips_when_key_entry_already_active(
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def push_ok(**_: Any) -> dict[str, Any]:
        return {
            "installed_path": "/home/ivy_lab/.ssh/authorized_keys",
            "fingerprint": "SHA256:alreadyactive",
        }

    monkeypatch.setattr(agent_rpc, "request_response", push_ok)

    factory = get_session_factory()
    async with factory() as session, session.begin():
        await account_link_request_service.approve_request(
            session,
            request_id=world["request_id"],
            decision_note="ok",
            admin_user_id=world["admin_id"],
            lab_id=world["lab_id"],
        )

    async def should_not_call(**_: Any) -> dict[str, Any]:
        raise AssertionError("retry should not call agent when entry is already active")

    monkeypatch.setattr(agent_rpc, "request_response", should_not_call)

    async with factory() as session, session.begin():
        _row, outcome = await account_link_request_service.retry_push_for_request(
            session,
            request_id=world["request_id"],
            admin_user_id=world["admin_id"],
            lab_id=world["lab_id"],
        )

    assert outcome["ok"] is True
    assert outcome["attempted"] is False
    assert outcome["already_active"] is True


async def test_onboard_authorized_key_retry_activates_inactive_entry(
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        key = (
            await session.execute(
                select(SshPublicKey).where(SshPublicKey.user_id == world["ivy_id"])
            )
        ).scalar_one()
        entry = AuthorizedKeyEntry(
            physical_account_id=world["pa_id"],
            ssh_public_key_id=key.id,
            pushed_by_user_id=world["admin_id"],
            pushed_for_user_id=world["ivy_id"],
            is_active=0,
        )
        session.add(entry)
        await session.flush()
        entry_id = entry.id

    async def retry_ok(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["frame_type"] == "backend.authorized_key.push"
        assert kwargs["payload"]["linux_username"] == "ivy_lab"
        assert "onboard=" in kwargs["payload"]["label"]
        return {
            "installed_path": "/home/ivy_lab/.ssh/authorized_keys",
            "fingerprint": "SHA256:onboardretry",
        }

    monkeypatch.setattr(agent_rpc, "request_response", retry_ok)

    async with factory() as session, session.begin():
        outcome = await physical_account_service.retry_authorized_key_push(
            session,
            pa_id=world["pa_id"],
            authorized_key_entry_id=entry_id,
            actor_user_id=world["admin_id"],
            lab_id=world["lab_id"],
        )
        assert outcome["ok"] is True

    async with factory() as session:
        entry = await session.get(AuthorizedKeyEntry, entry_id)
        assert entry is not None
        assert entry.is_active == 1


async def test_authorized_key_inventory_lists_statuses_for_one_server(
    world: dict[str, Any],
) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        key_active = SshPublicKey(
            user_id=world["ivy_id"],
            public_key="ssh-ed25519 AAAA inventory-active",  # pragma: allowlist secret
            fingerprint_sha256="SHA256:inventoryactive012345678901234567890123",
            key_type="ssh-ed25519",
            comment="active laptop",
            is_active=1,
        )
        key_failed = SshPublicKey(
            user_id=world["ivy_id"],
            public_key="ssh-ed25519 AAAA inventory-failed",  # pragma: allowlist secret
            fingerprint_sha256="SHA256:inventoryfailed012345678901234567890123",
            key_type="ssh-ed25519",
            comment="needs retry",
            is_active=1,
        )
        key_removed = SshPublicKey(
            user_id=world["ivy_id"],
            public_key="ssh-ed25519 AAAA inventory-removed",  # pragma: allowlist secret
            fingerprint_sha256="SHA256:inventoryremoved0123456789012345678901",
            key_type="ssh-ed25519",
            comment="removed key",
            is_active=1,
        )
        session.add_all([key_active, key_failed, key_removed])
        await session.flush()
        session.add_all(
            [
                AuthorizedKeyEntry(
                    physical_account_id=world["pa_id"],
                    ssh_public_key_id=key_active.id,
                    pushed_by_user_id=world["admin_id"],
                    pushed_for_user_id=world["ivy_id"],
                    is_active=1,
                ),
                AuthorizedKeyEntry(
                    physical_account_id=world["pa_id"],
                    ssh_public_key_id=key_failed.id,
                    pushed_by_user_id=world["admin_id"],
                    pushed_for_user_id=world["ivy_id"],
                    is_active=0,
                ),
                AuthorizedKeyEntry(
                    physical_account_id=world["pa_id"],
                    ssh_public_key_id=key_removed.id,
                    pushed_by_user_id=world["admin_id"],
                    pushed_for_user_id=world["ivy_id"],
                    is_active=0,
                    removed_at=datetime.now(UTC),
                    removed_by_user_id=world["admin_id"],
                ),
            ]
        )

    async with factory() as session:
        rows = await physical_account_service.list_authorized_key_inventory(
            session,
            server_id=world["server_id"],
            lab_id=world["lab_id"],
        )

    by_comment = {row["key_comment"]: row for row in rows}
    assert by_comment["active laptop"]["status"] == "active"
    assert by_comment["active laptop"]["can_retry"] is False
    assert by_comment["needs retry"]["status"] == "push_failed"
    assert by_comment["needs retry"]["can_retry"] is True
    assert by_comment["removed key"]["status"] == "removed"
    assert by_comment["removed key"]["can_retry"] is False
    assert by_comment["removed key"]["removed_by_username"] == "adm"
    assert {
        row["linux_username"]
        for row in rows
        if row["key_comment"] in {"active laptop", "needs retry", "removed key"}
    } == {"ivy_lab"}

    async with factory() as session:
        assert (
            await physical_account_service.list_authorized_key_inventory(
                session,
                server_id=world["server_id"],
                lab_id=world["lab_id"] + 999,
            )
        ) == []


async def test_authorized_key_inventory_endpoint_returns_server_scoped_rows(
    world: dict[str, Any],
    integration_client: AsyncClient,
) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        key = (
            await session.execute(
                select(SshPublicKey).where(SshPublicKey.user_id == world["ivy_id"])
            )
        ).scalar_one()
        session.add(
            AuthorizedKeyEntry(
                physical_account_id=world["pa_id"],
                ssh_public_key_id=key.id,
                pushed_by_user_id=world["admin_id"],
                pushed_for_user_id=world["ivy_id"],
                is_active=0,
            )
        )

    login = await integration_client.post(
        "/api/v1/auth/login",
        json={"username": "adm", "password": "AdmPass!2024"},  # pragma: allowlist secret
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = await integration_client.get(
        f"/api/v1/servers/{world['server_id']}/authorized-key-entries",
        headers=headers,
    )

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["physical_account_id"] == world["pa_id"]
    assert rows[0]["linux_username"] == "ivy_lab"
    assert rows[0]["pushed_for_username"] == "ivy"
    assert rows[0]["status"] == "push_failed"
    assert rows[0]["can_retry"] is True


async def test_authorized_key_readback_reconciles_host_and_managed_rows(
    world: dict[str, Any],
    integration_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed_fingerprint = "SHA256:managedreadback012345678901234567890123456"
    unknown_fingerprint = "SHA256:unknownhostkey0123456789012345678901234567"
    factory = get_session_factory()
    async with factory() as session, session.begin():
        key = (
            await session.execute(
                select(SshPublicKey).where(SshPublicKey.user_id == world["ivy_id"])
            )
        ).scalar_one()
        key.fingerprint_sha256 = managed_fingerprint
        session.add(
            AuthorizedKeyEntry(
                physical_account_id=world["pa_id"],
                ssh_public_key_id=key.id,
                pushed_for_user_id=world["ivy_id"],
                pushed_by_user_id=world["admin_id"],
                pushed_at=datetime.now(UTC),
                is_active=1,
            )
        )

    async def fake_readback(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["server_id"] == world["server_id"]
        assert kwargs["frame_type"] == "backend.authorized_key.read"
        assert kwargs["payload"] == {"linux_username": "ivy_lab"}
        return {
            "ok": True,
            "authorized_keys_path": "/home/ivy_lab/.ssh/authorized_keys",
            "line_count": 2,
            "invalid_line_count": 0,
            "keys": [
                {
                    "line_number": 1,
                    "fingerprint_sha256": managed_fingerprint,
                    "key_type": "ssh-ed25519",
                    "comment": "corelab:user=44",
                },
                {
                    "line_number": 2,
                    "fingerprint_sha256": unknown_fingerprint,
                    "key_type": "ssh-ed25519",
                    "comment": "manual laptop",
                },
            ],
        }

    monkeypatch.setattr(agent_rpc, "request_response", fake_readback)
    login = await integration_client.post(
        "/api/v1/auth/login",
        json={"username": "adm", "password": "AdmPass!2024"},  # pragma: allowlist secret
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = await integration_client.post(
        f"/api/v1/physical-accounts/{world['pa_id']}/authorized-key-readback",
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["authorized_keys_path"] == "/home/ivy_lab/.ssh/authorized_keys"
    assert body["managed_entries"][0]["fingerprint_sha256"] == managed_fingerprint
    assert body["managed_entries"][0]["present_on_host"] is True
    assert body["unknown_host_keys"][0]["fingerprint_sha256"] == unknown_fingerprint
    assert "public_key" not in body["host_keys"][0]

    async with factory() as session:
        audits = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "physical_account.authorized_key_readback",
                        AuditLog.target_id == world["pa_id"],
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(audits) == 1
    assert audits[0].payload is not None
    assert audits[0].payload["unknown_host_key_count"] == 1
