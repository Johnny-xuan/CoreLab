"""Phase 4 account_link_service integration tests.

Drives the service helpers directly through a real session (no HTTP) so
the assertions stay close to the security invariants in
``docs/04-security.md`` §5 + §6:

- ``get_active_link_for_actas`` raises ``AdminDeclaredCannotActAsError``
  for ``source='admin_declared'`` (invariant #5 / R8).
- ``upgrade_admin_declared_to_ssh`` flips the old row inactive and
  writes a new ``source='ssh_challenge'`` row with proof_evidence
  pointing back at the old id.
- ``reverse_lookup_users`` returns every active link regardless of
  source — including admin_declared (invariant #7 / shared-account
  audit limit assumes this is true).
- ``establish_via_*`` refuses duplicate active links (idempotency
  invariant).
- ``revoke_link`` writes the audit row + flips ``is_active=0``.
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from corelab_backend.db import get_session_factory
from corelab_backend.models import (
    AccountLink,
    AuditLog,
    Lab,
    PhysicalAccount,
    Server,
    User,
)
from corelab_backend.security import hash_password
from corelab_backend.services import account_link_service as als
from httpx import AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def linked_world(integration_client: AsyncClient) -> dict[str, Any]:
    """Seed a minimal Phase 4 world: lab + 2 users + server + PA.

    The ``integration_client`` dependency ensures the conftest wipe has
    already run, so we start from an empty DB even if a prior test left
    rows.
    """
    del integration_client  # only here to trigger the wipe fixture
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="Phase4 Lab", slug="phase4")
        session.add(lab)
        await session.flush()
        alice = User(
            lab_id=lab.id,
            username="alice",
            email="alice@phase4.test",
            display_name="Alice",
            password_hash=hash_password("AlicePass!2024"),
            role="lab_admin",
        )
        bob = User(
            lab_id=lab.id,
            username="bob",
            email="bob@phase4.test",
            display_name="Bob",
            password_hash=hash_password("BobPass!2024"),
            role="user",
        )
        session.add_all([alice, bob])
        await session.flush()
        server = Server(
            lab_id=lab.id,
            hostname="gpu-01.phase4",
            display_name="GPU 01",
            status="online",
            created_by_user_id=alice.id,
        )
        session.add(server)
        await session.flush()
        pa = PhysicalAccount(
            server_id=server.id,
            linux_username="yang_lab",
            uid=1001,
            gid=1001,
            home_directory="/home/yang_lab",
            default_shell="/bin/bash",
            source="admin_manual_register",
            created_by_user_id=alice.id,
        )
        session.add(pa)
        await session.flush()
        return {
            "lab_id": lab.id,
            "alice_id": alice.id,
            "bob_id": bob.id,
            "server_id": server.id,
            "pa_id": pa.id,
        }


async def test_admin_declared_link_refuses_actas(linked_world: dict[str, Any]) -> None:
    """Invariant #5: ``get_active_link_for_actas`` refuses admin_declared."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await als.admin_declare_link(
            session,
            physical_account_id=linked_world["pa_id"],
            owner_user_id=linked_world["bob_id"],
            reason="Bob already SSH'd in before CoreLab was set up.",
            declared_by_user_id=linked_world["alice_id"],
            lab_id=linked_world["lab_id"],
            server_id=linked_world["server_id"],
        )

    async with factory() as session:
        with pytest.raises(als.AdminDeclaredCannotActAsError):
            await als.get_active_link_for_actas(
                session,
                user_id=linked_world["bob_id"],
                physical_account_id=linked_world["pa_id"],
            )


async def test_ssh_challenge_link_allows_actas(linked_world: dict[str, Any]) -> None:
    """Sanity counterpart: verified link returns from get_active_link_for_actas."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await als.establish_via_ssh_challenge(
            session,
            user_id=linked_world["bob_id"],
            physical_account_id=linked_world["pa_id"],
            challenge_id="chal-xyz",
            signer_fingerprint="SHA256:abc",
            lab_id=linked_world["lab_id"],
            server_id=linked_world["server_id"],
        )

    async with factory() as session:
        link = await als.get_active_link_for_actas(
            session,
            user_id=linked_world["bob_id"],
            physical_account_id=linked_world["pa_id"],
        )
        assert link.source == "ssh_challenge"


async def test_upgrade_admin_declared_to_ssh_chain(linked_world: dict[str, Any]) -> None:
    """Invariant #8: upgrade flips old row inactive + writes a new ssh_challenge row."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await als.admin_declare_link(
            session,
            physical_account_id=linked_world["pa_id"],
            owner_user_id=linked_world["bob_id"],
            reason="Initial admin-declared link before Bob ran SSH challenge.",
            declared_by_user_id=linked_world["alice_id"],
            lab_id=linked_world["lab_id"],
            server_id=linked_world["server_id"],
        )

    async with factory() as session, session.begin():
        new_link = await als.upgrade_admin_declared_to_ssh(
            session,
            user_id=linked_world["bob_id"],
            physical_account_id=linked_world["pa_id"],
            challenge_id="chal-upg",
            signer_fingerprint="SHA256:upgraded",
            lab_id=linked_world["lab_id"],
            server_id=linked_world["server_id"],
        )
        new_link_id = new_link.id

    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(AccountLink)
                    .where(
                        AccountLink.user_id == linked_world["bob_id"],
                        AccountLink.physical_account_id == linked_world["pa_id"],
                    )
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
        # Phase 9 / FU-21 — dedicated reason instead of overloaded 'self'.
        assert old.revoke_reason == "upgraded_to_verified"
        assert new.source == "ssh_challenge"
        assert new.is_active == 1
        assert new.id == new_link_id
        assert new.proof_evidence["upgraded_from_link_id"] == old.id


async def test_reverse_lookup_returns_all_sources(linked_world: dict[str, Any]) -> None:
    """Invariant #7: reverse lookup surfaces every source, including admin_declared."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        # admin_declared link for Bob
        await als.admin_declare_link(
            session,
            physical_account_id=linked_world["pa_id"],
            owner_user_id=linked_world["bob_id"],
            reason="Pre-existing Bob access to yang_lab shared account.",
            declared_by_user_id=linked_world["alice_id"],
            lab_id=linked_world["lab_id"],
            server_id=linked_world["server_id"],
        )
        # ssh_challenge link for Alice (admin can also own a PA link)
        await als.establish_via_ssh_challenge(
            session,
            user_id=linked_world["alice_id"],
            physical_account_id=linked_world["pa_id"],
            challenge_id="chal-alice",
            signer_fingerprint="SHA256:alice",
            lab_id=linked_world["lab_id"],
            server_id=linked_world["server_id"],
        )

    async with factory() as session:
        links = await als.reverse_lookup_users(
            session, server_id=linked_world["server_id"], linux_username="yang_lab"
        )
        sources = {link.source for link in links}
        assert sources == {"admin_declared", "ssh_challenge"}
        user_ids = {link.user_id for link in links}
        assert user_ids == {linked_world["alice_id"], linked_world["bob_id"]}


async def test_reverse_lookup_unknown_pa_returns_empty(linked_world: dict[str, Any]) -> None:
    """Calling reverse_lookup for a non-existent Linux account returns []."""
    factory = get_session_factory()
    async with factory() as session:
        links = await als.reverse_lookup_users(
            session, server_id=linked_world["server_id"], linux_username="ghost"
        )
        assert links == []


async def test_duplicate_active_link_refused(linked_world: dict[str, Any]) -> None:
    """Can't write two active links for the same (user, PA)."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await als.establish_via_pam(
            session,
            user_id=linked_world["bob_id"],
            physical_account_id=linked_world["pa_id"],
            lab_id=linked_world["lab_id"],
            server_id=linked_world["server_id"],
        )

    async with factory() as session, session.begin():
        with pytest.raises(als.LinkAlreadyActiveError):
            await als.establish_via_pam(
                session,
                user_id=linked_world["bob_id"],
                physical_account_id=linked_world["pa_id"],
                lab_id=linked_world["lab_id"],
                server_id=linked_world["server_id"],
            )


async def test_revoke_link_audits(linked_world: dict[str, Any]) -> None:
    """Revoke writes account_link.revoked audit row + flips is_active=0."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        link = await als.establish_via_pam(
            session,
            user_id=linked_world["bob_id"],
            physical_account_id=linked_world["pa_id"],
            lab_id=linked_world["lab_id"],
            server_id=linked_world["server_id"],
        )
        link_id = link.id

    async with factory() as session, session.begin():
        live = await session.get(AccountLink, link_id)
        assert live is not None
        await als.revoke_link(
            session,
            link=live,
            actor_user_id=linked_world["bob_id"],
            reason="self",
            lab_id=linked_world["lab_id"],
            server_id=linked_world["server_id"],
        )

    async with factory() as session:
        revoked = await session.get(AccountLink, link_id)
        assert revoked is not None
        assert revoked.is_active == 0
        assert revoked.revoke_reason == "self"
        actions = (
            (await session.execute(select(AuditLog.action).where(AuditLog.target_id == link_id)))
            .scalars()
            .all()
        )
        assert "account_link.established" in actions
        assert "account_link.revoked" in actions


async def test_upgrade_refuses_when_source_not_admin_declared(
    linked_world: dict[str, Any],
) -> None:
    """Upgrade path is only for admin_declared → ssh_challenge."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await als.establish_via_pam(
            session,
            user_id=linked_world["bob_id"],
            physical_account_id=linked_world["pa_id"],
            lab_id=linked_world["lab_id"],
            server_id=linked_world["server_id"],
        )

    async with factory() as session, session.begin():
        with pytest.raises(als.AccountLinkError, match="only admin_declared"):
            await als.upgrade_admin_declared_to_ssh(
                session,
                user_id=linked_world["bob_id"],
                physical_account_id=linked_world["pa_id"],
                challenge_id="chal",
                signer_fingerprint="SHA256:x",
                lab_id=linked_world["lab_id"],
                server_id=linked_world["server_id"],
            )
