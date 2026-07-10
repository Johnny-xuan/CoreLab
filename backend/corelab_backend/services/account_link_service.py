"""AccountLink writer service — establish / revoke / upgrade + act-as gate.

All ``account_link`` rows go through this module so the invariants in
docs/04-security.md §5 + §6 + §9.8 stay in one place:

- #3 actor binding: the caller of ``establish_via_ssh_challenge`` /
  ``establish_via_pam`` must be ``user_id`` themselves; no admin
  override path here.
- #5 admin_declared cannot act-as: ``get_active_link_for_actas``
  refuses ``source='admin_declared'`` rows and raises
  ``AdminDeclaredCannotActAsError``.
- #8 upgrade flow: ``upgrade_via_challenge`` flips the old admin_declared
  row inactive (``revoke_reason='upgraded_to_verified'`` — extending
  the doc enum; see C2 commit note) and writes a new row with
  ``source='ssh_challenge'``.
- #10 every write writes ``audit_log`` (in this commit only the
  ssh_challenge write path lands; pam / admin_declare / upgrade /
  revoke arrive with C3).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AccountLink, PhysicalAccount, Server, SshPublicKey
from . import audit_service


class AccountLinkError(Exception):
    pass


class PhysicalAccountNotFoundError(AccountLinkError):
    pass


class SshKeyNotOwnedByUserError(AccountLinkError):
    """The ssh_public_key_id is not in the actor's profile (invariant #3)."""


class LinkAlreadyActiveError(AccountLinkError):
    pass


class AdminDeclaredCannotActAsError(AccountLinkError):
    """Refused — admin_declared links are reverse-lookup-only (invariant #5)."""


async def resolve_physical_account(
    session: AsyncSession,
    *,
    server_id: int,
    linux_username: str,
) -> PhysicalAccount:
    """Look up an active PA on a server by linux_username."""
    result = await session.execute(
        select(PhysicalAccount).where(
            PhysicalAccount.server_id == server_id,
            PhysicalAccount.linux_username == linux_username,
            PhysicalAccount.is_active == 1,
        )
    )
    pa = result.scalar_one_or_none()
    if pa is None:
        raise PhysicalAccountNotFoundError(
            f"no active PhysicalAccount for ({server_id}, {linux_username!r})"
        )
    return pa


async def assert_ssh_key_owned(
    session: AsyncSession,
    *,
    ssh_public_key_id: int,
    user_id: int,
) -> SshPublicKey:
    """Refuse if the ssh_public_key isn't actually in the actor's profile."""
    result = await session.execute(
        select(SshPublicKey).where(
            SshPublicKey.id == ssh_public_key_id,
            SshPublicKey.user_id == user_id,
            SshPublicKey.is_active == 1,
        )
    )
    key = result.scalar_one_or_none()
    if key is None:
        raise SshKeyNotOwnedByUserError(
            f"ssh_public_key {ssh_public_key_id} is not active in user {user_id}'s profile"
        )
    return key


async def _existing_active_link(
    session: AsyncSession, *, user_id: int, physical_account_id: int
) -> AccountLink | None:
    result = await session.execute(
        select(AccountLink).where(
            AccountLink.user_id == user_id,
            AccountLink.physical_account_id == physical_account_id,
            AccountLink.is_active == 1,
        )
    )
    return result.scalar_one_or_none()


async def establish_via_pam(
    session: AsyncSession,
    *,
    user_id: int,
    physical_account_id: int,
    lab_id: int,
    server_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> AccountLink:
    """Persist a verified-via-PAM link; audit it; refuse duplicates."""
    existing = await _existing_active_link(
        session, user_id=user_id, physical_account_id=physical_account_id
    )
    if existing is not None:
        raise LinkAlreadyActiveError(
            f"user {user_id} already linked to physical_account {physical_account_id}"
        )
    proof: dict[str, Any] = {
        "method": "pam",
        "verified_at": datetime.now(UTC).isoformat(),
    }
    link = AccountLink(
        user_id=user_id,
        physical_account_id=physical_account_id,
        source="password_pam",
        proof_evidence=proof,
        established_by_user_id=user_id,
        is_active=1,
    )
    session.add(link)
    await session.flush()

    await audit_service.write(
        session,
        action="account_link.established",
        actor_user_id=user_id,
        lab_id=lab_id,
        target_type="account_link",
        target_id=link.id,
        target_lab_id=lab_id,
        target_server_id=server_id,
        payload={
            "physical_account_id": physical_account_id,
            "source": "password_pam",
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return link


async def establish_via_ssh_challenge(
    session: AsyncSession,
    *,
    user_id: int,
    physical_account_id: int,
    challenge_id: str,
    signer_fingerprint: str,
    lab_id: int,
    server_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> AccountLink:
    """Persist a verified SSH-challenge link; audit it; refuse duplicates."""
    existing = await _existing_active_link(
        session, user_id=user_id, physical_account_id=physical_account_id
    )
    if existing is not None:
        raise LinkAlreadyActiveError(
            f"user {user_id} already linked to physical_account {physical_account_id}"
        )

    proof: dict[str, Any] = {
        "method": "ssh-sign",
        "challenge_id": challenge_id,
        "signer_fingerprint": signer_fingerprint,
        "verified_at": datetime.now(UTC).isoformat(),
    }
    link = AccountLink(
        user_id=user_id,
        physical_account_id=physical_account_id,
        source="ssh_challenge",
        proof_evidence=proof,
        established_by_user_id=user_id,
        is_active=1,
    )
    session.add(link)
    await session.flush()

    await audit_service.write(
        session,
        action="account_link.established",
        actor_user_id=user_id,
        lab_id=lab_id,
        target_type="account_link",
        target_id=link.id,
        target_lab_id=lab_id,
        target_server_id=server_id,
        payload={
            "physical_account_id": physical_account_id,
            "source": "ssh_challenge",
            "challenge_id": challenge_id,
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return link


async def get_active_link_for_actas(
    session: AsyncSession,
    *,
    user_id: int,
    physical_account_id: int,
) -> AccountLink:
    """Return the user's active link to a PA *iff* it can be used to act-as.

    Refuses ``source='admin_declared'`` per docs/04-security.md §9.8 +
    invariant #5. Callers that need *visibility only* (reverse lookups,
    notifications, stats) should hit ``account_link`` directly without
    going through this helper.
    """
    result = await session.execute(
        select(AccountLink).where(
            AccountLink.user_id == user_id,
            AccountLink.physical_account_id == physical_account_id,
            AccountLink.is_active == 1,
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        raise AccountLinkError(
            f"no active link for user {user_id} on physical_account {physical_account_id}"
        )
    if link.source == "admin_declared":
        raise AdminDeclaredCannotActAsError(
            f"link {link.id} is admin_declared; cannot be used for act-as operations"
        )
    return link


async def list_active_for_user(session: AsyncSession, *, user_id: int) -> Sequence[AccountLink]:
    """List a user's own active links (powers the workspace switcher)."""
    result = await session.execute(
        select(AccountLink)
        .where(AccountLink.user_id == user_id, AccountLink.is_active == 1)
        .order_by(AccountLink.id)
    )
    return result.scalars().all()


async def list_all_for_user(session: AsyncSession, *, user_id: int) -> Sequence[AccountLink]:
    """All links (active + revoked) — for the user's link history view."""
    result = await session.execute(
        select(AccountLink).where(AccountLink.user_id == user_id).order_by(AccountLink.id.desc())
    )
    return result.scalars().all()


async def load_link_in_lab(session: AsyncSession, *, link_id: int, lab_id: int) -> AccountLink:
    """Look up a link and verify the PA belongs to the caller's lab.

    Lab membership is enforced via ``physical_account.server.lab_id`` so
    cross-lab access (e.g. an admin from another lab snooping links by
    id) returns the same 404 as a non-existent row.
    """
    link = await session.get(AccountLink, link_id)
    if link is None:
        raise AccountLinkError(f"account_link {link_id} not found")
    pa = await session.get(PhysicalAccount, link.physical_account_id)
    if pa is None:
        raise AccountLinkError(f"physical_account {link.physical_account_id} not found")
    server = await session.get(Server, pa.server_id)
    if server is None or server.lab_id != lab_id:
        raise AccountLinkError(f"account_link {link_id} not in lab {lab_id}")
    return link


async def revoke_link(
    session: AsyncSession,
    *,
    link: AccountLink,
    actor_user_id: int,
    reason: str,
    lab_id: int,
    server_id: int,
    revoke_key: bool = True,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> AccountLink:
    """Soft-revoke an active link; mirrors the doc revoke_reason enum.

    When ``revoke_key`` is true (default), every authorized_key_entry
    CoreLab pushed for this (user, pa) pair is also revoked: agent gets
    a ``backend.authorized_key.revoke`` RPC and the row flips
    ``is_active=0``. Keeps Linux-side state in sync — otherwise the
    revoked link leaves a stranded pubkey on disk that still lets the
    user ssh in directly, bypassing the platform.
    """
    if link.is_active != 1:
        raise AccountLinkError(f"link {link.id} is already revoked")
    link.is_active = 0
    link.revoked_at = datetime.now(UTC)
    link.revoked_by_user_id = actor_user_id
    link.revoke_reason = reason
    await session.flush()

    key_revoke_outcome: list[dict[str, Any]] = []
    if revoke_key:
        from sqlalchemy import select as _sel

        from ..models import AuthorizedKeyEntry, PhysicalAccount, SshPublicKey
        from . import agent_rpc

        pa = await session.get(PhysicalAccount, link.physical_account_id)
        if pa is not None:
            entries_result = await session.execute(
                _sel(AuthorizedKeyEntry).where(
                    AuthorizedKeyEntry.physical_account_id == pa.id,
                    AuthorizedKeyEntry.pushed_for_user_id == link.user_id,
                    AuthorizedKeyEntry.is_active == 1,
                )
            )
            entries = entries_result.scalars().all()
            for entry in entries:
                key = await session.get(SshPublicKey, entry.ssh_public_key_id)
                if key is None:
                    continue
                outcome: dict[str, Any] = {"entry_id": entry.id, "key_id": key.id}
                try:
                    await agent_rpc.request_response(
                        server_id=pa.server_id,
                        frame_type="backend.authorized_key.revoke",
                        payload={
                            "linux_username": pa.linux_username,
                            "fingerprint": key.fingerprint_sha256,
                        },
                        timeout_seconds=10.0,
                    )
                    outcome["ok"] = True
                except (agent_rpc.AgentOfflineError, agent_rpc.AgentRpcTimeoutError) as exc:
                    outcome.update({"ok": False, "error": str(exc)})
                entry.is_active = 0
                entry.removed_at = datetime.now(UTC)
                entry.removed_by_user_id = actor_user_id
                key_revoke_outcome.append(outcome)
            if entries:
                await session.flush()

    await audit_service.write(
        session,
        action="account_link.revoked",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="account_link",
        target_id=link.id,
        target_lab_id=lab_id,
        target_server_id=server_id,
        payload={
            "reason": reason,
            "user_id": link.user_id,
            "physical_account_id": link.physical_account_id,
            "revoke_key": revoke_key,
            "key_revoke_outcome": key_revoke_outcome,
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return link


async def admin_declare_link(
    session: AsyncSession,
    *,
    physical_account_id: int,
    owner_user_id: int,
    reason: str,
    declared_by_user_id: int,
    lab_id: int,
    server_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> AccountLink:
    """Write a source='admin_declared' link — visibility-only per invariant #5."""
    existing = await _existing_active_link(
        session, user_id=owner_user_id, physical_account_id=physical_account_id
    )
    if existing is not None:
        raise LinkAlreadyActiveError(
            f"user {owner_user_id} already linked to physical_account {physical_account_id}"
        )

    proof: dict[str, Any] = {
        "method": "admin-declared",
        "declared_by": declared_by_user_id,
        "declared_at": datetime.now(UTC).isoformat(),
        "reason": reason,
    }
    link = AccountLink(
        user_id=owner_user_id,
        physical_account_id=physical_account_id,
        source="admin_declared",
        proof_evidence=proof,
        established_by_user_id=declared_by_user_id,
        is_active=1,
    )
    session.add(link)
    await session.flush()

    await audit_service.write(
        session,
        action="account_link.admin_declared",
        actor_user_id=declared_by_user_id,
        lab_id=lab_id,
        target_type="account_link",
        target_id=link.id,
        target_lab_id=lab_id,
        target_server_id=server_id,
        payload={
            "owner_user_id": owner_user_id,
            "physical_account_id": physical_account_id,
            "reason": reason,
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return link


async def upgrade_admin_declared_to_ssh(
    session: AsyncSession,
    *,
    user_id: int,
    physical_account_id: int,
    challenge_id: str,
    signer_fingerprint: str,
    lab_id: int,
    server_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> AccountLink:
    """Flip an existing admin_declared link inactive and write a verified one.

    Phase 9 / FU-21: uses the dedicated ``revoke_reason=
    'upgraded_to_verified'`` enum value (docs/04 §10 line 781 字面)
    instead of overloading ``'self'``. The new row's ``proof_evidence``
    still carries ``upgraded_from_link_id`` so the upgrade chain is
    fully reconstructable.
    """
    old = await _existing_active_link(
        session, user_id=user_id, physical_account_id=physical_account_id
    )
    if old is None:
        raise AccountLinkError(
            f"no existing link to upgrade for user {user_id} / pa {physical_account_id}"
        )
    if old.source != "admin_declared":
        raise AccountLinkError(
            f"link {old.id} source is {old.source!r}; only admin_declared can be upgraded"
        )

    old.is_active = 0
    old.revoked_at = datetime.now(UTC)
    old.revoked_by_user_id = user_id
    old.revoke_reason = "upgraded_to_verified"
    await session.flush()

    proof: dict[str, Any] = {
        "method": "ssh-sign",
        "challenge_id": challenge_id,
        "signer_fingerprint": signer_fingerprint,
        "verified_at": datetime.now(UTC).isoformat(),
        "upgraded_from_link_id": old.id,
    }
    new_link = AccountLink(
        user_id=user_id,
        physical_account_id=physical_account_id,
        source="ssh_challenge",
        proof_evidence=proof,
        established_by_user_id=user_id,
        is_active=1,
    )
    session.add(new_link)
    await session.flush()

    await audit_service.write(
        session,
        action="account_link.upgraded",
        actor_user_id=user_id,
        lab_id=lab_id,
        target_type="account_link",
        target_id=new_link.id,
        target_lab_id=lab_id,
        target_server_id=server_id,
        payload={
            "old_link_id": old.id,
            "new_link_id": new_link.id,
            "physical_account_id": physical_account_id,
            "challenge_id": challenge_id,
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return new_link


async def reverse_lookup_users(
    session: AsyncSession, *, server_id: int, linux_username: str
) -> list[AccountLink]:
    """Resolve a Linux account on a server -> every platform user linked to it.

    Returns all *active* links across all source enums (including
    admin_declared) so the agent's compliance monitor can name the
    accountable user(s) for any process it sees (invariant #7).
    """
    pa_result = await session.execute(
        select(PhysicalAccount).where(
            PhysicalAccount.server_id == server_id,
            PhysicalAccount.linux_username == linux_username,
            PhysicalAccount.is_active == 1,
        )
    )
    pa = pa_result.scalar_one_or_none()
    if pa is None:
        return []
    result = await session.execute(
        select(AccountLink)
        .where(AccountLink.physical_account_id == pa.id, AccountLink.is_active == 1)
        .order_by(AccountLink.id)
    )
    return list(result.scalars().all())
