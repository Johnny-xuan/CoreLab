"""Phase 9 C4 — FU-21 revoke_reason='upgraded_to_verified' + CHECK (P9-8).

Covers:
* The expanded ``ck_link_revoke_reason`` accepts the new enum value
  and still rejects garbage.
* ``upgrade_admin_declared_to_ssh`` now stamps the dedicated reason
  on the revoked row.
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from corelab_backend.db import get_session_factory
from corelab_backend.models import AccountLink, Lab, PhysicalAccount, Server, User
from corelab_backend.security import hash_password, issue_access_token
from corelab_backend.services import account_link_service as als
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def world(integration_client: AsyncClient) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="P9 FU21", slug="p9-fu21")
        session.add(lab)
        await session.flush()
        alice = User(
            lab_id=lab.id,
            username="alice",
            email="alice@fu21.test",
            display_name="Alice",
            password_hash=hash_password("AlicePass!2024"),  # pragma: allowlist secret
            role="lab_admin",
        )
        bob = User(
            lab_id=lab.id,
            username="bob",
            email="bob@fu21.test",
            display_name="Bob",
            password_hash=hash_password("BobPass!2024"),  # pragma: allowlist secret
            role="user",
        )
        session.add_all([alice, bob])
        await session.flush()
        server = Server(
            lab_id=lab.id,
            hostname="gpu-fu21",
            display_name="FU21",
            status="online",
            created_by_user_id=alice.id,
        )
        session.add(server)
        await session.flush()
        pa = PhysicalAccount(
            server_id=server.id,
            linux_username="yang_lab",
            uid=2001,
            created_by_user_id=alice.id,
            source="admin_manual_register",
        )
        session.add(pa)
        await session.flush()
    return {
        "client": integration_client,
        "lab_id": lab.id,
        "alice_id": alice.id,
        "bob_id": bob.id,
        "server_id": server.id,
        "pa_id": pa.id,
    }


class TestUpgradeStampsDedicatedReason:
    async def test_upgrade_writes_upgraded_to_verified(self, world: dict[str, Any]) -> None:
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await als.admin_declare_link(
                session,
                physical_account_id=world["pa_id"],
                owner_user_id=world["bob_id"],
                reason="initial declaration before bob ssh-verifies",
                declared_by_user_id=world["alice_id"],
                lab_id=world["lab_id"],
                server_id=world["server_id"],
            )
        async with factory() as session, session.begin():
            new_link = await als.upgrade_admin_declared_to_ssh(
                session,
                user_id=world["bob_id"],
                physical_account_id=world["pa_id"],
                challenge_id="chal-up-9",
                signer_fingerprint="SHA256:phase9",
                lab_id=world["lab_id"],
                server_id=world["server_id"],
            )
            new_id = new_link.id
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(AccountLink)
                        .where(AccountLink.physical_account_id == world["pa_id"])
                        .order_by(AccountLink.id)
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 2
        old, new = rows
        assert old.source == "admin_declared"
        assert old.is_active == 0
        assert old.revoke_reason == "upgraded_to_verified"
        assert new.id == new_id
        assert new.source == "ssh_challenge"
        assert new.proof_evidence["upgraded_from_link_id"] == old.id


class TestRevokeReasonCheckConstraint:
    async def test_invalid_revoke_reason_rejected(self, world: dict[str, Any]) -> None:
        """A raw INSERT with a bogus revoke_reason must fail the CHECK."""
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await als.admin_declare_link(
                session,
                physical_account_id=world["pa_id"],
                owner_user_id=world["bob_id"],
                reason="initial",
                declared_by_user_id=world["alice_id"],
                lab_id=world["lab_id"],
                server_id=world["server_id"],
            )
        async with factory() as session:
            with pytest.raises(DatabaseError):
                async with session.begin():
                    await session.execute(
                        text(
                            "UPDATE account_link SET is_active=0, "
                            "revoke_reason='banana' WHERE physical_account_id=:pa"
                        ),
                        {"pa": world["pa_id"]},
                    )

    async def test_upgraded_to_verified_passes_check(self, world: dict[str, Any]) -> None:
        """Raw UPDATE to the new enum value passes the constraint."""
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await als.admin_declare_link(
                session,
                physical_account_id=world["pa_id"],
                owner_user_id=world["bob_id"],
                reason="initial",
                declared_by_user_id=world["alice_id"],
                lab_id=world["lab_id"],
                server_id=world["server_id"],
            )
        async with factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE account_link SET is_active=0, "
                    "revoke_reason='upgraded_to_verified' "
                    "WHERE physical_account_id=:pa"
                ),
                {"pa": world["pa_id"]},
            )


class TestAccountLinkApiContract:
    async def test_history_api_serializes_upgraded_to_verified(self, world: dict[str, Any]) -> None:
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await als.admin_declare_link(
                session,
                physical_account_id=world["pa_id"],
                owner_user_id=world["bob_id"],
                reason="initial declaration before bob ssh-verifies",
                declared_by_user_id=world["alice_id"],
                lab_id=world["lab_id"],
                server_id=world["server_id"],
            )
        async with factory() as session, session.begin():
            await als.upgrade_admin_declared_to_ssh(
                session,
                user_id=world["bob_id"],
                physical_account_id=world["pa_id"],
                challenge_id="chal-api-9",
                signer_fingerprint="SHA256:phase9-api",
                lab_id=world["lab_id"],
                server_id=world["server_id"],
            )

        token, _ = issue_access_token(
            user_id=world["bob_id"],
            lab_id=world["lab_id"],
            role="user",
        )
        resp = await world["client"].get(
            "/api/v1/users/me/account-links",
            params={"include_history": "true"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        rows = resp.json()
        assert [row["revoke_reason"] for row in rows] == [
            None,
            "upgraded_to_verified",
        ]
