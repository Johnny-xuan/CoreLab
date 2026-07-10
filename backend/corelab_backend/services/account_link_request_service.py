"""AccountLinkRequest lifecycle — user-driven claim approval flow.

Workflow (docs/02-data-model.md §5.19 + planner C3 nit-3 clarification):

    user POSTs a request for an existing PA they want to act-as
        ↓
    admin reviews ``status='pending'`` and either approves or denies
        ↓ (approve only)
    backend asks the agent to push the user's *active* SSH public key
    into the PA's authorized_keys (RPC ``backend.authorized_key.push``,
    gated by the agent's ``ssh.push_authorized_key`` capability)
        ↓
    backend writes an ``authorized_key_entry`` row recording the push
        ↓
    backend notifies the requester (Phase 7 — for now status is the
    signal) so they can finish the link with a SSH challenge

Reject paths land the same ``status``-set audit + a ``decision_note``
explanation; no agent RPC fires.

The user-side ``withdraw`` is a no-side-effect cancel of a pending row.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    AccountLink,
    AccountLinkRequest,
    AuthorizedKeyEntry,
    PhysicalAccount,
    Server,
    SshPublicKey,
)
from . import agent_rpc, audit_service, notification_service


class AccountLinkRequestError(Exception):
    pass


class PhysicalAccountNotFoundError(AccountLinkRequestError):
    pass


class AlreadyLinkedError(AccountLinkRequestError):
    pass


class DuplicatePendingError(AccountLinkRequestError):
    pass


class RequestNotFoundError(AccountLinkRequestError):
    pass


class InvalidStatusTransitionError(AccountLinkRequestError):
    pass


class NoActiveSshKeyError(AccountLinkRequestError):
    """The requester has no active SSH key for the agent to push on approval."""


async def _latest_active_key_for_user(session: AsyncSession, *, user_id: int) -> SshPublicKey:
    key_result = await session.execute(
        select(SshPublicKey)
        .where(SshPublicKey.user_id == user_id, SshPublicKey.is_active == 1)
        .order_by(SshPublicKey.id.desc())
    )
    active_key = key_result.scalars().first()
    if active_key is None:
        raise NoActiveSshKeyError(f"user {user_id} has no active ssh_public_key; cannot push key")
    return active_key


async def _find_key_entry(
    session: AsyncSession,
    *,
    physical_account_id: int,
    ssh_public_key_id: int,
) -> AuthorizedKeyEntry | None:
    result = await session.execute(
        select(AuthorizedKeyEntry).where(
            AuthorizedKeyEntry.physical_account_id == physical_account_id,
            AuthorizedKeyEntry.ssh_public_key_id == ssh_public_key_id,
        )
    )
    return result.scalar_one_or_none()


async def create_request(
    session: AsyncSession,
    *,
    requester_user_id: int,
    physical_account_id: int,
    request_note: str | None,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> AccountLinkRequest:
    pa = await session.get(PhysicalAccount, physical_account_id)
    if pa is None or pa.is_active != 1:
        raise PhysicalAccountNotFoundError(
            f"physical_account {physical_account_id} not found or inactive"
        )
    server = await session.get(Server, pa.server_id)
    if server is None or server.lab_id != lab_id:
        raise PhysicalAccountNotFoundError(
            f"physical_account {physical_account_id} not in lab {lab_id}"
        )

    existing_link = await session.execute(
        select(AccountLink).where(
            AccountLink.user_id == requester_user_id,
            AccountLink.physical_account_id == physical_account_id,
            AccountLink.is_active == 1,
        )
    )
    if existing_link.scalar_one_or_none() is not None:
        raise AlreadyLinkedError(
            f"user {requester_user_id} already linked to physical_account {physical_account_id}"
        )

    existing_pending = await session.execute(
        select(AccountLinkRequest).where(
            AccountLinkRequest.requester_user_id == requester_user_id,
            AccountLinkRequest.physical_account_id == physical_account_id,
            AccountLinkRequest.status == "pending",
        )
    )
    if existing_pending.scalar_one_or_none() is not None:
        raise DuplicatePendingError(
            f"a pending request already exists for ({requester_user_id}, {physical_account_id})"
        )

    row = AccountLinkRequest(
        requester_user_id=requester_user_id,
        physical_account_id=physical_account_id,
        status="pending",
        request_note=request_note,
    )
    session.add(row)
    await session.flush()

    await audit_service.write(
        session,
        action="account_link_request.created",
        actor_user_id=requester_user_id,
        lab_id=lab_id,
        target_type="account_link_request",
        target_id=row.id,
        target_lab_id=lab_id,
        target_server_id=pa.server_id,
        payload={
            "physical_account_id": physical_account_id,
            "linux_username": pa.linux_username,
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return row


async def _load_request_in_lab(
    session: AsyncSession, *, request_id: int, lab_id: int
) -> tuple[AccountLinkRequest, PhysicalAccount, Server]:
    # Concurrency audit cluster A: lock the request row so the
    # approve/deny/withdraw state machine serializes. Two concurrent
    # approvals of the same pending request used to both pass the
    # ``status == 'pending'`` snapshot check and both run the side effects
    # (double key push + double audit/notification, decided_by clobbered),
    # because there is no unique key on the table and the agent-offline
    # branch inserts no authorized_key_entry to collide on. A locking read
    # makes the second caller block, then re-read ``approved`` and raise a
    # clean InvalidStatusTransitionError (409). ``populate_existing`` so an
    # already-cached row is refreshed to the latest committed status.
    row = (
        await session.execute(
            select(AccountLinkRequest)
            .where(AccountLinkRequest.id == request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        raise RequestNotFoundError(f"account_link_request {request_id} not found")
    pa = await session.get(PhysicalAccount, row.physical_account_id)
    if pa is None:
        raise RequestNotFoundError(f"physical_account {row.physical_account_id} not found")
    server = await session.get(Server, pa.server_id)
    if server is None or server.lab_id != lab_id:
        raise RequestNotFoundError(f"account_link_request {request_id} not in lab {lab_id}")
    return row, pa, server


async def approve_request(
    session: AsyncSession,
    *,
    request_id: int,
    decision_note: str | None,
    admin_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> AccountLinkRequest:
    row, pa, server = await _load_request_in_lab(session, request_id=request_id, lab_id=lab_id)
    if row.status != "pending":
        raise InvalidStatusTransitionError(
            f"request {request_id} status is {row.status!r}; only pending can be approved"
        )

    # The requester must own at least one active SSH key so the agent
    # has something to push. ``GET ssh-keys/me`` would have shown them
    # an empty list, but the front-end can call ``approve`` without
    # that check — surface a clean 422 here.
    active_key = await _latest_active_key_for_user(session, user_id=row.requester_user_id)

    # Best-effort RPC — record the failure in the audit payload but
    # still flip the request to approved so the operator-visible state
    # advances. The requester can re-trigger the push by clicking
    # "Retry push" on the request detail later (Phase 7 surface).
    push_outcome: dict[str, Any] = {"attempted": True}
    try:
        rpc_result = await agent_rpc.request_response(
            server_id=server.id,
            frame_type="backend.authorized_key.push",
            payload={
                "linux_username": pa.linux_username,
                "public_key": active_key.public_key,
                "label": f"corelab:user={row.requester_user_id};link_request={row.id}",
            },
            timeout_seconds=15.0,
        )
        installed_path = rpc_result.get("installed_path", "")
        fingerprint = rpc_result.get("fingerprint", "")
        push_outcome.update(
            {
                "ok": True,
                "installed_path": installed_path,
                "fingerprint": fingerprint,
                "mock_warning": rpc_result.get("mock_warning"),
            }
        )

        # Record the authorized_key_entry so revoke later is precise.
        entry = AuthorizedKeyEntry(
            physical_account_id=pa.id,
            ssh_public_key_id=active_key.id,
            pushed_by_user_id=admin_user_id,
            pushed_for_user_id=row.requester_user_id,
            is_active=1,
        )
        session.add(entry)
        await session.flush()
        push_outcome["authorized_key_entry_id"] = entry.id
    except (agent_rpc.AgentOfflineError, agent_rpc.AgentRpcTimeoutError) as exc:
        push_outcome.update({"ok": False, "error": str(exc)})

    row.status = "approved"
    row.decided_by_user_id = admin_user_id
    row.decided_at = datetime.now(UTC)
    row.decision_note = decision_note
    await session.flush()

    await audit_service.write(
        session,
        action="account_link_request.approved",
        actor_user_id=admin_user_id,
        lab_id=lab_id,
        target_type="account_link_request",
        target_id=row.id,
        target_lab_id=lab_id,
        target_server_id=server.id,
        payload={
            "physical_account_id": pa.id,
            "linux_username": pa.linux_username,
            "ssh_public_key_id": active_key.id,
            "push_outcome": push_outcome,
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )

    # Phase 7 FU-32 — notify the requester so they know the key has been
    # pushed and they can now self-claim via the SSH challenge flow.
    # Push-failed approvals still notify (severity warn) so the user
    # knows admin acted but agent was unreachable; the request page has
    # the retry-push button.
    push_ok = bool(push_outcome.get("ok"))
    severity: notification_service.Severity = "info" if push_ok else "warn"
    title = (
        f"Admin prepared account {pa.linux_username}@{server.hostname}; run SSH challenge to claim"
        if push_ok
        else f"Admin approved {pa.linux_username}@{server.hostname} but key push failed; retry"
    )
    await notification_service.create_notification(
        session,
        recipient_user_id=row.requester_user_id,
        type="link.prepared",
        severity=severity,
        title=title,
        payload={
            "account_link_request_id": row.id,
            "physical_account_id": pa.id,
            "server_id": server.id,
            "linux_username": pa.linux_username,
            "push_ok": push_ok,
        },
        cta_url="/account-link-requests",
    )
    return row


async def retry_push_for_request(
    session: AsyncSession,
    *,
    request_id: int,
    admin_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[AccountLinkRequest, dict[str, Any]]:
    """Retry key push for an approved request whose first push failed.

    Approval and preparation are deliberately separate: an approved
    request says an admin accepted the relationship, while a successful
    key push says the Linux account has actually been prepared for the
    requester to complete the SSH challenge.
    """
    row, pa, server = await _load_request_in_lab(session, request_id=request_id, lab_id=lab_id)
    if row.status != "approved":
        raise InvalidStatusTransitionError(
            f"request {request_id} status is {row.status!r}; only approved can retry key push"
        )

    active_key = await _latest_active_key_for_user(session, user_id=row.requester_user_id)
    existing_entry = await _find_key_entry(
        session,
        physical_account_id=pa.id,
        ssh_public_key_id=active_key.id,
    )
    if existing_entry is not None and existing_entry.is_active == 1:
        skipped_outcome: dict[str, Any] = {
            "ok": True,
            "attempted": False,
            "already_active": True,
            "authorized_key_entry_id": existing_entry.id,
            "ssh_public_key_id": active_key.id,
        }
        await audit_service.write(
            session,
            action="account_link_request.key_push_retry.skipped",
            actor_user_id=admin_user_id,
            lab_id=lab_id,
            target_type="account_link_request",
            target_id=row.id,
            target_lab_id=lab_id,
            target_server_id=server.id,
            payload={
                "physical_account_id": pa.id,
                "linux_username": pa.linux_username,
                "push_outcome": skipped_outcome,
            },
            ip_address=request_ip,
            user_agent=user_agent,
        )
        return row, skipped_outcome

    outcome: dict[str, Any] = {
        "attempted": True,
        "ssh_public_key_id": active_key.id,
    }
    try:
        rpc_result = await agent_rpc.request_response(
            server_id=server.id,
            frame_type="backend.authorized_key.push",
            payload={
                "linux_username": pa.linux_username,
                "public_key": active_key.public_key,
                "label": f"corelab:user={row.requester_user_id};link_request={row.id};retry=1",
            },
            timeout_seconds=15.0,
        )
        installed_path = rpc_result.get("installed_path", "")
        fingerprint = rpc_result.get("fingerprint", "")
        if existing_entry is None:
            existing_entry = AuthorizedKeyEntry(
                physical_account_id=pa.id,
                ssh_public_key_id=active_key.id,
                pushed_by_user_id=admin_user_id,
                pushed_for_user_id=row.requester_user_id,
                is_active=1,
            )
            session.add(existing_entry)
        else:
            existing_entry.pushed_by_user_id = admin_user_id
            existing_entry.pushed_for_user_id = row.requester_user_id
            existing_entry.pushed_at = datetime.now(UTC)
            existing_entry.is_active = 1
            existing_entry.removed_at = None
            existing_entry.removed_by_user_id = None
        await session.flush()
        outcome.update(
            {
                "ok": True,
                "installed_path": installed_path,
                "fingerprint": fingerprint,
                "mock_warning": rpc_result.get("mock_warning"),
                "authorized_key_entry_id": existing_entry.id,
            }
        )
    except (
        agent_rpc.AgentOfflineError,
        agent_rpc.AgentRpcTimeoutError,
        agent_rpc.RpcNotYetWiredError,
    ) as exc:
        outcome.update({"ok": False, "error": str(exc)})

    await audit_service.write(
        session,
        action="account_link_request.key_push_retried",
        actor_user_id=admin_user_id,
        lab_id=lab_id,
        target_type="account_link_request",
        target_id=row.id,
        target_lab_id=lab_id,
        target_server_id=server.id,
        payload={
            "physical_account_id": pa.id,
            "linux_username": pa.linux_username,
            "push_outcome": outcome,
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )
    if outcome.get("ok"):
        await notification_service.create_notification(
            session,
            recipient_user_id=row.requester_user_id,
            type="link.prepared",
            severity="info",
            title=f"Account {pa.linux_username}@{server.hostname} is prepared; run SSH challenge to claim",
            payload={
                "account_link_request_id": row.id,
                "physical_account_id": pa.id,
                "server_id": server.id,
                "linux_username": pa.linux_username,
                "push_ok": True,
                "retry": True,
            },
            cta_url="/account-link-requests",
            # The first approval may already have emitted a warn-level
            # link.prepared notification for the same request when the
            # agent was offline. A successful retry is a new state change,
            # so it must not be hidden by the request-level dedup window.
            dedup_window_seconds=0,
        )
    return row, outcome


async def deny_request(
    session: AsyncSession,
    *,
    request_id: int,
    decision_note: str | None,
    admin_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> AccountLinkRequest:
    row, pa, server = await _load_request_in_lab(session, request_id=request_id, lab_id=lab_id)
    if row.status != "pending":
        raise InvalidStatusTransitionError(
            f"request {request_id} status is {row.status!r}; only pending can be denied"
        )
    row.status = "denied"
    row.decided_by_user_id = admin_user_id
    row.decided_at = datetime.now(UTC)
    row.decision_note = decision_note
    await session.flush()

    await audit_service.write(
        session,
        action="account_link_request.denied",
        actor_user_id=admin_user_id,
        lab_id=lab_id,
        target_type="account_link_request",
        target_id=row.id,
        target_lab_id=lab_id,
        target_server_id=server.id,
        payload={"physical_account_id": pa.id, "decision_note": decision_note},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    # Mirror of the FU-32 approve notification — the requester must
    # learn the outcome either way; otherwise a denied request just
    # sits in "pending" from their point of view until they check.
    await notification_service.create_notification(
        session,
        recipient_user_id=row.requester_user_id,
        type="link.denied",
        severity="warn",
        title=f"Admin denied your request for {pa.linux_username}@{server.hostname}",
        payload={
            "account_link_request_id": row.id,
            "physical_account_id": pa.id,
            "server_id": server.id,
            "linux_username": pa.linux_username,
            "decision_note": decision_note,
        },
        cta_url="/account-link-requests",
    )
    return row


async def withdraw_request(
    session: AsyncSession,
    *,
    request_id: int,
    requester_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> AccountLinkRequest:
    row, pa, server = await _load_request_in_lab(session, request_id=request_id, lab_id=lab_id)
    if row.requester_user_id != requester_user_id:
        raise AccountLinkRequestError("not_requester")
    if row.status != "pending":
        raise InvalidStatusTransitionError(
            f"request {request_id} status is {row.status!r}; only pending can be withdrawn"
        )
    row.status = "withdrawn"
    row.decided_at = datetime.now(UTC)
    await session.flush()

    await audit_service.write(
        session,
        action="account_link_request.withdrawn",
        actor_user_id=requester_user_id,
        lab_id=lab_id,
        target_type="account_link_request",
        target_id=row.id,
        target_lab_id=lab_id,
        target_server_id=server.id,
        payload={"physical_account_id": pa.id},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return row


async def list_for_requester(
    session: AsyncSession, *, requester_user_id: int
) -> Sequence[AccountLinkRequest]:
    result = await session.execute(
        select(AccountLinkRequest)
        .where(AccountLinkRequest.requester_user_id == requester_user_id)
        .order_by(AccountLinkRequest.id.desc())
    )
    return result.scalars().all()


async def list_needing_key_push_in_lab(
    session: AsyncSession, *, lab_id: int
) -> Sequence[AccountLinkRequest]:
    """Approved requests whose current active key is not active on the PA."""
    result = await session.execute(
        select(AccountLinkRequest)
        .join(PhysicalAccount, AccountLinkRequest.physical_account_id == PhysicalAccount.id)
        .join(Server, PhysicalAccount.server_id == Server.id)
        .where(Server.lab_id == lab_id, AccountLinkRequest.status == "approved")
        .order_by(AccountLinkRequest.id.desc())
    )
    rows = result.scalars().all()
    needs_push: list[AccountLinkRequest] = []
    for row in rows:
        try:
            active_key = await _latest_active_key_for_user(session, user_id=row.requester_user_id)
        except NoActiveSshKeyError:
            continue
        entry = await _find_key_entry(
            session,
            physical_account_id=row.physical_account_id,
            ssh_public_key_id=active_key.id,
        )
        if entry is None or entry.is_active != 1:
            needs_push.append(row)
    return needs_push


async def build_request_context(
    session: AsyncSession, *, request_id: int, lab_id: int
) -> dict[str, Any]:
    """Aggregate the signals admin needs to make the approve/deny call —
    drives the review card in AccountLinkRequests.vue (Phase K)."""
    from sqlalchemy import func
    from sqlalchemy import select as _sel

    from ..models import (
        AccountLink,
        AuthorizedKeyEntry,
        SshPublicKey,
        User,
    )

    row, pa, server = await _load_request_in_lab(session, request_id=request_id, lab_id=lab_id)
    requester = await session.get(User, row.requester_user_id)
    assert requester is not None  # FK guarantees existence

    # First-time-with-this-PA?
    prior_link_count = (
        await session.execute(
            _sel(func.count())
            .select_from(AccountLink)
            .where(
                AccountLink.user_id == row.requester_user_id,
                AccountLink.physical_account_id == row.physical_account_id,
            )
        )
    ).scalar_one()
    is_first_time = prior_link_count == 0

    # Requester aggregate history.
    counts_result = await session.execute(
        _sel(AccountLinkRequest.status, func.count())
        .where(AccountLinkRequest.requester_user_id == row.requester_user_id)
        .group_by(AccountLinkRequest.status)
    )
    counts: dict[str, int] = {"approved": 0, "denied": 0, "withdrawn": 0, "pending": 0}
    for status_val, n in counts_result.all():
        counts[str(status_val)] = int(n)
    total = sum(counts.values())

    # Active SSH keys this requester owns.
    key_result = await session.execute(
        _sel(SshPublicKey)
        .where(SshPublicKey.user_id == row.requester_user_id, SshPublicKey.is_active == 1)
        .order_by(SshPublicKey.id.desc())
    )
    active_keys = key_result.scalars().all()

    # Lateral surface — every place CoreLab has already pushed any of
    # this requester's active keys. Joined to PA + Server so the UI can
    # show "host: <hostname>" instead of bare ids.
    lateral: list[dict[str, Any]] = []
    if active_keys:
        from ..models import PhysicalAccount

        entry_result = await session.execute(
            _sel(AuthorizedKeyEntry, SshPublicKey, PhysicalAccount, Server)
            .join(SshPublicKey, SshPublicKey.id == AuthorizedKeyEntry.ssh_public_key_id)
            .join(
                PhysicalAccount,
                PhysicalAccount.id == AuthorizedKeyEntry.physical_account_id,
            )
            .join(Server, Server.id == PhysicalAccount.server_id)
            .where(
                AuthorizedKeyEntry.pushed_for_user_id == row.requester_user_id,
                AuthorizedKeyEntry.is_active == 1,
                Server.lab_id == lab_id,
            )
        )
        for _e, k, p, s in entry_result.all():
            lateral.append(
                {
                    "fingerprint_sha256": k.fingerprint_sha256,
                    "label": k.comment,
                    "physical_account_id": p.id,
                    "linux_username": p.linux_username,
                    "server_hostname": s.hostname,
                    "pushed_at": _e.pushed_at,
                }
            )

    return {
        "request_id": row.id,
        "requester_user_id": row.requester_user_id,
        "requester_username": requester.username,
        "requester_display_name": requester.display_name,
        "physical_account_id": pa.id,
        "linux_username": pa.linux_username,
        "server_id": server.id,
        "server_hostname": server.hostname,
        "server_display_name": server.display_name,
        "is_first_time_for_this_pa": bool(is_first_time),
        "requester_stats": {
            "total": total,
            "approved": counts["approved"],
            "denied": counts["denied"],
            "withdrawn": counts["withdrawn"],
        },
        "requester_active_keys": [
            {
                "id": k.id,
                "fingerprint_sha256": k.fingerprint_sha256,
                "key_type": k.key_type,
                "comment": k.comment or "",
            }
            for k in active_keys
        ],
        "lateral_surface": lateral,
    }


async def list_pending_in_lab(
    session: AsyncSession, *, lab_id: int
) -> Sequence[AccountLinkRequest]:
    """List requests whose PA is in this lab + still ``pending``."""
    result = await session.execute(
        select(AccountLinkRequest)
        .join(PhysicalAccount, AccountLinkRequest.physical_account_id == PhysicalAccount.id)
        .join(Server, PhysicalAccount.server_id == Server.id)
        .where(Server.lab_id == lab_id, AccountLinkRequest.status == "pending")
        .order_by(AccountLinkRequest.id.desc())
    )
    return result.scalars().all()
