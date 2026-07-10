"""Phase 2 integration walkthrough — exercises every stage gate from
``tmp_to_planner.md`` §6 against the real MySQL container.

Skipped when ``CORELAB_DATABASE_URL`` points to a placeholder (the
``async_client`` unit tests still run); inside the backend container
the env points at mysql:3306 so this runs end to end.
"""

from __future__ import annotations

import pytest
from corelab_backend.db import get_session_factory
from corelab_backend.models import AuditLog, RegistrationInvite, SshPublicKey, User
from httpx import AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


async def test_setup_flow_full(integration_client: AsyncClient) -> None:
    """Stage Gate §6.1: fresh DB → /setup/status false → init → status true."""
    status = await integration_client.get("/api/v1/setup/status")
    assert status.json() == {"initialized": False}

    init = await integration_client.post(
        "/api/v1/setup/init",
        json={
            "lab_name": "Example GPU Lab",
            "lab_slug": "example-gpu",
            "admin_username": "alice",
            "admin_email": "alice@example.com",
            "admin_display_name": "Alice Wang",
            "admin_password": "AlicePass!2024",  # pragma: allowlist secret
        },
    )
    assert init.status_code == 201
    payload = init.json()
    assert payload["admin"]["role"] == "lab_admin"

    again = await integration_client.get("/api/v1/setup/status")
    assert again.json() == {"initialized": True}


async def test_setup_init_only_once(seeded_admin: dict, integration_client: AsyncClient) -> None:
    """Stage Gate §6.5: second /setup/init → 409."""
    resp = await integration_client.post(
        "/api/v1/setup/init",
        json={
            "lab_name": "x",
            "lab_slug": "xx",
            "admin_username": "bobby",
            "admin_email": "b@x.com",
            "admin_display_name": "B",
            "admin_password": "BobbyPass!2024",  # pragma: allowlist secret
        },
    )
    assert resp.status_code == 409


async def test_invite_activate_login_loop(
    seeded_admin: dict, integration_client: AsyncClient
) -> None:
    """Stage Gate §6.2 + §6.6 audit chain on the invite/register path."""
    alice_login = await integration_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": seeded_admin["password"]},
    )
    assert alice_login.status_code == 200
    alice_token = alice_login.json()["access_token"]

    invite = await integration_client.post(
        "/api/v1/users/invitations",
        json={"role": "user"},
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert invite.status_code == 201
    invite_body = invite.json()
    plaintext = invite_body["setup_token"]
    assert invite_body["activation_url"].startswith("http://testserver/register?token=")
    assert invite_body["user"] is None
    assert invite_body["invitation_id"] is not None

    preview = await integration_client.get(
        "/api/v1/setup/activate/validate", params={"token": plaintext}
    )
    assert preview.status_code == 200
    assert preview.json() == {
        "user_id": None,
        "username": None,
        "email": None,
        "display_name": None,
        "purpose": "registration",
        "role": "user",
    }

    factory = get_session_factory()
    async with factory() as session:
        before_user = (
            await session.execute(select(User).where(User.username == "bobby"))
        ).scalar_one_or_none()
    assert before_user is None

    activate = await integration_client.post(
        "/api/v1/setup/activate",
        json={
            "token": plaintext,
            "username": "bobby",
            "email": "bobby@x.com",
            "display_name": "Bobby Tables",
            "password": "BobPass!2024",  # pragma: allowlist secret
            "ssh_key_label": "Laptop",
            "ssh_key_public_key": (
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILBxJ8nQfFqkY6yGD7nUHwZ7l8K8oRgcN5SmcXgYsK4f"
                " bobby@laptop"
            ),
        },
    )
    assert activate.status_code == 200
    activated = activate.json()
    assert activated["username"] == "bobby"
    assert activated["email"] == "bobby@x.com"
    assert activated["display_name"] == "Bobby Tables"

    # Activation is one-shot — second use is rejected.
    reuse = await integration_client.post(
        "/api/v1/setup/activate",
        json={
            "token": plaintext,
            "password": "BobPass!2024",  # pragma: allowlist secret
        },
    )
    assert reuse.status_code == 400

    bob_login = await integration_client.post(
        "/api/v1/auth/login",
        json={
            "username": "bobby",
            "password": "BobPass!2024",  # pragma: allowlist secret
        },
    )
    assert bob_login.status_code == 200

    async with factory() as session:
        user = (await session.execute(select(User).where(User.username == "bobby"))).scalar_one()
        used_invite = await session.get(RegistrationInvite, invite_body["invitation_id"])
        keys = (
            (await session.execute(select(SshPublicKey).where(SshPublicKey.user_id == user.id)))
            .scalars()
            .all()
        )
    assert len(keys) == 1
    assert keys[0].comment == "Laptop"
    assert used_invite is not None
    assert used_invite.used_by_user_id == user.id

    # Stage Gate §6.6 — every flow produced an audit_log row.
    async with factory() as session:
        rows = (await session.execute(select(AuditLog.action))).scalars().all()
    distinct = set(rows)
    expected = {
        "auth.login",
        "registration_invite.create",
        "registration_invite.consume",
        "user.register",
        "ssh_key.add",
    }
    assert expected <= distinct, f"missing audit actions: {expected - distinct}"


async def test_legacy_user_create_endpoint_mints_registration_invite_without_user(
    seeded_admin: dict, integration_client: AsyncClient
) -> None:
    """Old browser bundles must not resurrect admin-precreated invitations."""
    alice_login = await integration_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": seeded_admin["password"]},
    )
    alice_token = alice_login.json()["access_token"]
    auth = {"Authorization": f"Bearer {alice_token}"}

    legacy = await integration_client.post(
        "/api/v1/users",
        json={
            "username": "legacy",
            "email": "legacy@x.com",
            "display_name": "Legacy Invite",
            "role": "user",
        },
        headers=auth,
    )
    assert legacy.status_code == 201
    body = legacy.json()
    assert body["activation_url"].startswith("http://testserver/register?token=")
    assert body["user"] is None
    assert body["invitation_id"] is not None

    preview = await integration_client.get(
        "/api/v1/setup/activate/validate", params={"token": body["setup_token"]}
    )
    assert preview.status_code == 200
    assert preview.json() == {
        "user_id": None,
        "username": None,
        "email": None,
        "display_name": None,
        "purpose": "registration",
        "role": "user",
    }

    factory = get_session_factory()
    async with factory() as session:
        stale_user = (
            await session.execute(select(User).where(User.username == "legacy"))
        ).scalar_one_or_none()
        invite = await session.get(RegistrationInvite, body["invitation_id"])

    assert stale_user is None
    assert invite is not None
    assert invite.used_at is None


async def test_registration_conflict_does_not_consume_invite_token(
    seeded_admin: dict, integration_client: AsyncClient
) -> None:
    carol_password = "CarolPass!2024"  # pragma: allowlist secret
    alice_login = await integration_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": seeded_admin["password"]},
    )
    alice_token = alice_login.json()["access_token"]

    invite = await integration_client.post(
        "/api/v1/users/invitations",
        json={"role": "user"},
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    plaintext = invite.json()["setup_token"]

    conflict = await integration_client.post(
        "/api/v1/setup/activate",
        json={
            "token": plaintext,
            "username": "alice",
            "email": "carol@x.com",
            "display_name": "Carol",
            "password": carol_password,
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "username_taken"

    retry = await integration_client.post(
        "/api/v1/setup/activate",
        json={
            "token": plaintext,
            "username": "carol",
            "email": "carol@x.com",
            "display_name": "Carol",
            "password": carol_password,
        },
    )
    assert retry.status_code == 200

    login = await integration_client.post(
        "/api/v1/auth/login", json={"username": "carol", "password": carol_password}
    )
    assert login.status_code == 200


async def test_registration_invite_list_and_revoke(
    seeded_admin: dict, integration_client: AsyncClient
) -> None:
    alice_login = await integration_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": seeded_admin["password"]},
    )
    alice_token = alice_login.json()["access_token"]
    auth = {"Authorization": f"Bearer {alice_token}"}

    invite = await integration_client.post(
        "/api/v1/users/invitations",
        json={"role": "lab_admin"},
        headers=auth,
    )
    assert invite.status_code == 201
    invite_body = invite.json()
    invite_id = invite_body["invitation_id"]
    plaintext = invite_body["setup_token"]

    listing = await integration_client.get("/api/v1/users/invitations", headers=auth)
    assert listing.status_code == 200
    row = next(item for item in listing.json() if item["id"] == invite_id)
    assert row["role"] == "lab_admin"
    assert row["status"] == "active"
    assert row["can_revoke"] is True
    assert row["created_by"]["username"] == "alice"
    assert row["used_by"] is None

    preview = await integration_client.get(
        "/api/v1/setup/activate/validate", params={"token": plaintext}
    )
    assert preview.status_code == 200

    revoked = await integration_client.post(
        f"/api/v1/users/invitations/{invite_id}/revoke", headers=auth
    )
    assert revoked.status_code == 200
    revoked_body = revoked.json()
    assert revoked_body["status"] == "revoked"
    assert revoked_body["can_revoke"] is False
    assert revoked_body["used_by"] is None

    preview_after_revoke = await integration_client.get(
        "/api/v1/setup/activate/validate", params={"token": plaintext}
    )
    assert preview_after_revoke.status_code == 400
    assert preview_after_revoke.json()["detail"] == "token already used"

    revoke_again = await integration_client.post(
        f"/api/v1/users/invitations/{invite_id}/revoke", headers=auth
    )
    assert revoke_again.status_code == 409
    assert revoke_again.json()["detail"] == "registration_invite_not_revokable"

    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(select(AuditLog.action))).scalars().all()
    assert "registration_invite.revoke" in set(rows)


async def test_ssh_key_add_list_delete(seeded_admin: dict, integration_client: AsyncClient) -> None:
    """Stage Gate §6.3: SSH key add → list → delete → list empty."""
    token = (
        await integration_client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": seeded_admin["password"]},
        )
    ).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    add = await integration_client.post(
        "/api/v1/users/me/ssh-keys",
        json={
            "public_key": (
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILBxJ8nQfFqkY6yGD7nUHwZ7l8K8oRgcN5SmcXgYsK4f"
                " alice@laptop"
            ),
            "label": "Laptop",
        },
        headers=auth,
    )
    assert add.status_code == 201
    key = add.json()
    assert key["fingerprint_sha256"].startswith("SHA256:")
    assert len(key["fingerprint_sha256"]) == 50  # CHAR(50) confirmed end-to-end

    listing = await integration_client.get("/api/v1/users/me/ssh-keys", headers=auth)
    assert len(listing.json()) == 1

    deletion = await integration_client.delete(
        f"/api/v1/users/me/ssh-keys/{key['id']}", headers=auth
    )
    assert deletion.json()["result"] == "deleted"

    listing_after = await integration_client.get("/api/v1/users/me/ssh-keys", headers=auth)
    assert listing_after.json() == []


async def test_role_change_invariants(seeded_admin: dict, integration_client: AsyncClient) -> None:
    """Stage Gate §6.4: promote → self-demote refused; last-admin protection."""
    alice_token = (
        await integration_client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": seeded_admin["password"]},
        )
    ).json()["access_token"]
    auth = {"Authorization": f"Bearer {alice_token}"}

    invite = await integration_client.post(
        "/api/v1/users/invitations",
        json={"role": "user"},
        headers=auth,
    )
    bob_register = await integration_client.post(
        "/api/v1/setup/activate",
        json={
            "token": invite.json()["setup_token"],
            "username": "bob",
            "email": "b@x.com",
            "display_name": "B",
            "password": "BobPass!2024",  # pragma: allowlist secret
        },
    )
    assert bob_register.status_code == 200
    bob_id = bob_register.json()["id"]

    # Alice cannot demote herself.
    self_demote = await integration_client.patch(
        f"/api/v1/users/{seeded_admin['admin_id']}/role",
        json={"role": "user"},
        headers=auth,
    )
    assert self_demote.status_code == 400

    # Promote bob to lab_admin.
    promote = await integration_client.patch(
        f"/api/v1/users/{bob_id}/role", json={"role": "lab_admin"}, headers=auth
    )
    assert promote.json()["role"] == "lab_admin"

    # Alice cannot disable herself either.
    self_disable = await integration_client.patch(
        f"/api/v1/users/{seeded_admin['admin_id']}/disable", headers=auth
    )
    assert self_disable.status_code == 400


async def test_login_rejects_wrong_password_and_audits(
    seeded_admin: dict, integration_client: AsyncClient
) -> None:
    """Stage Gate §6.8 + §6.6 — failed login is denied + audited."""
    bad = await integration_client.post(
        "/api/v1/auth/login",
        json={
            "username": "alice",
            "password": "wrong-on-purpose",  # pragma: allowlist secret
        },
    )
    assert bad.status_code == 401

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(AuditLog.action, AuditLog.result, AuditLog.error_message).where(
                    AuditLog.action == "auth.login", AuditLog.result == "denied"
                )
            )
        ).all()
    assert any(r.error_message == "invalid_credentials" for r in rows)
