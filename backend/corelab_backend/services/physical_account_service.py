"""PhysicalAccount CRUD — server-scoped Linux account registry.

Soft-delete only (invariant: id never reused so link/audit history stays
intact). Lab membership is enforced by routing every read/write through
the parent ``server.lab_id``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..models import AuthorizedKeyEntry, PhysicalAccount, Server, SshPublicKey, User
from . import agent_rpc, audit_service

VALID_SOURCES = frozenset({"agent_created", "discovered_scan", "admin_manual_register"})


class PhysicalAccountError(Exception):
    pass


class PhysicalAccountNotFoundError(PhysicalAccountError):
    pass


class DuplicatePhysicalAccountError(PhysicalAccountError):
    pass


class InvalidSourceError(PhysicalAccountError):
    pass


class AuthorizedKeyEntryNotFoundError(PhysicalAccountError):
    pass


class AuthorizedKeyReadbackError(PhysicalAccountError):
    pass


def _authorized_key_entry_status(entry: AuthorizedKeyEntry) -> str:
    if entry.is_active == 1:
        return "active"
    if entry.removed_at is not None:
        return "removed"
    return "push_failed"


async def list_for_server(session: AsyncSession, *, server_id: int) -> Sequence[PhysicalAccount]:
    result = await session.execute(
        select(PhysicalAccount)
        .where(PhysicalAccount.server_id == server_id, PhysicalAccount.is_active == 1)
        .order_by(PhysicalAccount.id)
    )
    return result.scalars().all()


async def list_authorized_key_inventory(
    session: AsyncSession,
    *,
    server_id: int,
    lab_id: int,
) -> list[dict[str, Any]]:
    """Return CoreLab-managed authorized-key entries for one server.

    This is an inventory of rows CoreLab created. It deliberately does
    not claim to mirror arbitrary host-side edits to authorized_keys.
    """
    pushed_for = aliased(User)
    pushed_by = aliased(User)
    removed_by = aliased(User)
    result = await session.execute(
        select(AuthorizedKeyEntry, PhysicalAccount, SshPublicKey, pushed_for, pushed_by, removed_by)
        .join(PhysicalAccount, PhysicalAccount.id == AuthorizedKeyEntry.physical_account_id)
        .join(Server, Server.id == PhysicalAccount.server_id)
        .join(SshPublicKey, SshPublicKey.id == AuthorizedKeyEntry.ssh_public_key_id)
        .join(pushed_for, pushed_for.id == AuthorizedKeyEntry.pushed_for_user_id)
        .join(pushed_by, pushed_by.id == AuthorizedKeyEntry.pushed_by_user_id)
        .outerjoin(removed_by, removed_by.id == AuthorizedKeyEntry.removed_by_user_id)
        .where(Server.id == server_id, Server.lab_id == lab_id)
        .order_by(PhysicalAccount.linux_username, AuthorizedKeyEntry.id)
    )
    entries: list[dict[str, Any]] = []
    for entry, pa, key, for_user, by_user, removed_user in result.all():
        status = _authorized_key_entry_status(entry)
        entries.append(
            {
                "entry_id": entry.id,
                "physical_account_id": pa.id,
                "linux_username": pa.linux_username,
                "ssh_public_key_id": key.id,
                "fingerprint_sha256": key.fingerprint_sha256,
                "key_type": key.key_type,
                "key_comment": key.comment,
                "key_is_active": bool(key.is_active),
                "pushed_for_user_id": for_user.id,
                "pushed_for_username": for_user.username,
                "pushed_for_display_name": for_user.display_name,
                "pushed_by_user_id": by_user.id,
                "pushed_by_username": by_user.username,
                "pushed_by_display_name": by_user.display_name,
                "pushed_at": entry.pushed_at,
                "is_active": bool(entry.is_active),
                "removed_at": entry.removed_at,
                "removed_by_user_id": removed_user.id if removed_user is not None else None,
                "removed_by_username": removed_user.username if removed_user is not None else None,
                "removed_by_display_name": (
                    removed_user.display_name if removed_user is not None else None
                ),
                "status": status,
                "can_retry": (status == "push_failed" and key.is_active == 1 and pa.is_active == 1),
            }
        )
    return entries


async def read_authorized_keys_from_host(
    session: AsyncSession,
    *,
    pa_id: int,
    actor_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Ask the live agent to read one PA's authorized_keys fingerprints."""
    pa = await get(session, pa_id, lab_id=lab_id)
    pushed_for = aliased(User)
    result = await session.execute(
        select(AuthorizedKeyEntry, SshPublicKey, pushed_for)
        .join(SshPublicKey, SshPublicKey.id == AuthorizedKeyEntry.ssh_public_key_id)
        .join(pushed_for, pushed_for.id == AuthorizedKeyEntry.pushed_for_user_id)
        .where(
            AuthorizedKeyEntry.physical_account_id == pa.id,
            AuthorizedKeyEntry.is_active == 1,
            SshPublicKey.is_active == 1,
        )
        .order_by(AuthorizedKeyEntry.id)
    )
    managed_rows = result.all()
    host_keys: list[dict[str, Any]] = []
    unknown_host_keys: list[dict[str, Any]] = []
    managed_entries: list[dict[str, Any]] = []
    ok = False
    error: str | None = None
    authorized_keys_path: str | None = None
    line_count = 0
    invalid_line_count = 0
    mock_warning: str | None = None
    try:
        rpc_result = await agent_rpc.request_response(
            server_id=pa.server_id,
            frame_type="backend.authorized_key.read",
            payload={"linux_username": pa.linux_username},
            timeout_seconds=10.0,
        )
        ok = rpc_result.get("ok") is True
        error = rpc_result.get("error") if not ok else None
        authorized_keys_path = rpc_result.get("authorized_keys_path")
        line_count = int(rpc_result.get("line_count") or 0)
        invalid_line_count = int(rpc_result.get("invalid_line_count") or 0)
        mock_warning = rpc_result.get("mock_warning")
        for raw in rpc_result.get("keys") or []:
            if not isinstance(raw, dict):
                continue
            fingerprint = raw.get("fingerprint_sha256")
            line_number = raw.get("line_number")
            if not isinstance(fingerprint, str) or not isinstance(line_number, int):
                continue
            host_keys.append(
                {
                    "line_number": line_number,
                    "fingerprint_sha256": fingerprint,
                    "key_type": raw.get("key_type"),
                    "comment": raw.get("comment"),
                }
            )
    except (
        agent_rpc.AgentOfflineError,
        agent_rpc.AgentRpcTimeoutError,
        agent_rpc.RpcNotYetWiredError,
        agent_rpc.UnexpectedResponseTypeError,
    ) as exc:
        error = str(exc)

    host_fingerprints = {entry["fingerprint_sha256"] for entry in host_keys}
    managed_fingerprints: set[str] = set()
    for entry, key, user in managed_rows:
        managed_fingerprints.add(key.fingerprint_sha256)
        managed_entries.append(
            {
                "entry_id": entry.id,
                "ssh_public_key_id": key.id,
                "fingerprint_sha256": key.fingerprint_sha256,
                "key_type": key.key_type,
                "key_comment": key.comment,
                "pushed_for_user_id": user.id,
                "pushed_for_username": user.username,
                "pushed_for_display_name": user.display_name,
                "pushed_at": entry.pushed_at,
                "present_on_host": key.fingerprint_sha256 in host_fingerprints,
            }
        )
    unknown_host_keys = [
        entry for entry in host_keys if entry["fingerprint_sha256"] not in managed_fingerprints
    ]
    response = {
        "physical_account_id": pa.id,
        "server_id": pa.server_id,
        "linux_username": pa.linux_username,
        "ok": ok,
        "error": error,
        "authorized_keys_path": authorized_keys_path,
        "line_count": line_count,
        "invalid_line_count": invalid_line_count,
        "host_keys": host_keys,
        "managed_entries": managed_entries,
        "unknown_host_keys": unknown_host_keys,
        "mock_warning": mock_warning,
    }
    await audit_service.write(
        session,
        action="physical_account.authorized_key_readback",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="physical_account",
        target_id=pa.id,
        target_lab_id=lab_id,
        target_server_id=pa.server_id,
        payload={
            "linux_username": pa.linux_username,
            "ok": ok,
            "error": error,
            "line_count": line_count,
            "invalid_line_count": invalid_line_count,
            "managed_entry_count": len(managed_entries),
            "missing_managed_count": sum(
                1 for entry in managed_entries if not entry["present_on_host"]
            ),
            "unknown_host_key_count": len(unknown_host_keys),
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return response


async def get(session: AsyncSession, pa_id: int, *, lab_id: int) -> PhysicalAccount:
    pa = await session.get(PhysicalAccount, pa_id)
    if pa is None:
        raise PhysicalAccountNotFoundError(f"physical_account {pa_id} not found")
    server = await session.get(Server, pa.server_id)
    if server is None or server.lab_id != lab_id:
        raise PhysicalAccountNotFoundError(f"physical_account {pa_id} not in lab {lab_id}")
    return pa


async def create(
    session: AsyncSession,
    *,
    server_id: int,
    linux_username: str,
    source: str,
    notes: str | None,
    actor_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> PhysicalAccount:
    if source not in VALID_SOURCES:
        raise InvalidSourceError(
            f"invalid source {source!r}; must be one of {sorted(VALID_SOURCES)}"
        )

    server = await session.get(Server, server_id)
    if server is None or server.lab_id != lab_id:
        raise PhysicalAccountNotFoundError(f"server {server_id} not in lab {lab_id}")

    # Active uniqueness on (server, linux_username) is enforced at the
    # DB layer via uq_pa_server_user_active, but we want a clean 409
    # rather than an IntegrityError trace.
    existing = await session.execute(
        select(PhysicalAccount).where(
            PhysicalAccount.server_id == server_id,
            PhysicalAccount.linux_username == linux_username,
            PhysicalAccount.is_active == 1,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicatePhysicalAccountError(
            f"physical_account {linux_username!r} already active on server {server_id}"
        )

    pa = PhysicalAccount(
        server_id=server_id,
        linux_username=linux_username,
        source=source,
        notes=notes,
        is_active=1,
        created_by_user_id=actor_user_id,
    )
    session.add(pa)
    await session.flush()

    await audit_service.write(
        session,
        action="physical_account.create",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="physical_account",
        target_id=pa.id,
        target_lab_id=lab_id,
        target_server_id=server_id,
        payload={"linux_username": linux_username, "source": source},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return pa


async def delete(
    session: AsyncSession,
    pa_id: int,
    *,
    actor_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> PhysicalAccount:
    """Soft-delete (is_active=0) so FK history on links + reservations holds."""
    pa = await get(session, pa_id, lab_id=lab_id)
    if pa.is_active == 0:
        return pa
    pa.is_active = 0
    await session.flush()

    await audit_service.write(
        session,
        action="physical_account.delete",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="physical_account",
        target_id=pa.id,
        target_lab_id=lab_id,
        target_server_id=pa.server_id,
        payload={"linux_username": pa.linux_username},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return pa


async def retry_authorized_key_push(
    session: AsyncSession,
    *,
    pa_id: int,
    authorized_key_entry_id: int,
    actor_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> dict:
    """Retry a failed key push recorded during admin onboarding."""
    pa = await get(session, pa_id, lab_id=lab_id)
    entry = await session.get(AuthorizedKeyEntry, authorized_key_entry_id)
    if entry is None or entry.physical_account_id != pa.id:
        raise AuthorizedKeyEntryNotFoundError(
            f"authorized_key_entry {authorized_key_entry_id} not found for PA {pa_id}"
        )
    key = await session.get(SshPublicKey, entry.ssh_public_key_id)
    if key is None or key.is_active != 1:
        raise AuthorizedKeyEntryNotFoundError(
            f"ssh_public_key {entry.ssh_public_key_id} not active"
        )

    outcome: dict[str, Any]
    if entry.is_active == 1:
        outcome = {
            "ok": True,
            "attempted": False,
            "already_active": True,
            "authorized_key_entry_id": entry.id,
            "ssh_public_key_id": key.id,
        }
    else:
        outcome = {
            "attempted": True,
            "authorized_key_entry_id": entry.id,
            "ssh_public_key_id": key.id,
        }
        try:
            rpc_result = await agent_rpc.request_response(
                server_id=pa.server_id,
                frame_type="backend.authorized_key.push",
                payload={
                    "linux_username": pa.linux_username,
                    "public_key": key.public_key,
                    "label": f"corelab:user={entry.pushed_for_user_id};onboard={pa.id};retry=1",
                },
                timeout_seconds=15.0,
            )
            entry.pushed_by_user_id = actor_user_id
            entry.pushed_at = datetime.now(UTC)
            entry.is_active = 1
            entry.removed_at = None
            entry.removed_by_user_id = None
            await session.flush()
            outcome.update(
                {
                    "ok": True,
                    "installed_path": rpc_result.get("installed_path", ""),
                    "fingerprint": rpc_result.get("fingerprint", ""),
                    "mock_warning": rpc_result.get("mock_warning"),
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
        action="physical_account.authorized_key_retry",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="physical_account",
        target_id=pa.id,
        target_lab_id=lab_id,
        target_server_id=pa.server_id,
        payload={
            "linux_username": pa.linux_username,
            "authorized_key_entry_id": entry.id,
            "key_push_outcome": outcome,
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return outcome


async def sync_discovered(
    session: AsyncSession,
    *,
    server_id: int,
    lab_id: int,
    entries: Sequence[dict],
) -> dict[str, int]:
    """Upsert an agent account-scan snapshot (source=discovered_scan).

    Each entry is a dict with linux_username + optional uid/gid/
    home_directory/default_shell (the AccountScanEntry shape).

    Rules:
    - unknown username        → new active row, source=discovered_scan
    - active row exists       → sync uid/gid/home/shell from the scan
      (/etc/passwd is the source of truth for those four fields) and
      stamp ``last_seen_at``
    - only inactive rows      → skip; an admin soft-deleted it and a
      scan must not resurrect it
    - usernames missing from the scan are left alone (no auto-delete;
      their ``last_seen_at`` simply stops advancing, which the UI
      surfaces as "上次发现 N 天前" for a human to judge)

    Returns counters for the caller's log line. Audit: one summary row
    per scan that actually created accounts (actor=None → system).
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    existing = (
        (
            await session.execute(
                select(PhysicalAccount).where(PhysicalAccount.server_id == server_id)
            )
        )
        .scalars()
        .all()
    )
    active = {pa.linux_username: pa for pa in existing if pa.is_active == 1}
    inactive_names = {pa.linux_username for pa in existing if pa.is_active != 1}

    created: list[str] = []
    backfilled = 0
    skipped_inactive = 0
    for entry in entries:
        name = entry.get("linux_username")
        if not name:
            continue
        pa = active.get(name)
        if pa is not None:
            touched = False
            for attr in ("uid", "gid", "home_directory", "default_shell"):
                value = entry.get(attr)
                if value is not None and getattr(pa, attr) != value:
                    setattr(pa, attr, value)
                    touched = True
            pa.last_seen_at = now
            if touched:
                backfilled += 1
            continue
        if name in inactive_names:
            skipped_inactive += 1
            continue
        pa = PhysicalAccount(
            server_id=server_id,
            linux_username=name,
            uid=entry.get("uid"),
            gid=entry.get("gid"),
            home_directory=entry.get("home_directory"),
            default_shell=entry.get("default_shell"),
            source="discovered_scan",
            is_active=1,
            created_by_user_id=None,
            last_seen_at=now,
        )
        session.add(pa)
        active[name] = pa
        created.append(name)

    await session.flush()
    if created:
        await audit_service.write(
            session,
            action="physical_account.discovered",
            actor_user_id=None,
            lab_id=lab_id,
            target_type="server",
            target_id=server_id,
            target_lab_id=lab_id,
            target_server_id=server_id,
            payload={
                "created": created,
                "backfilled": backfilled,
                "skipped_inactive": skipped_inactive,
                "scanned": len(entries),
            },
        )
    return {
        "created": len(created),
        "backfilled": backfilled,
        "skipped_inactive": skipped_inactive,
        "scanned": len(entries),
    }


async def get_with_server(session: AsyncSession, pa_id: int) -> tuple[PhysicalAccount, Server]:
    """Helper used by routes that need both the PA and its server (lab check downstream)."""
    pa = await session.get(PhysicalAccount, pa_id)
    if pa is None:
        raise PhysicalAccountNotFoundError(f"physical_account {pa_id} not found")
    server = await session.get(Server, pa.server_id)
    if server is None:
        raise PhysicalAccountNotFoundError(
            f"server {pa.server_id} for physical_account {pa_id} not found"
        )
    return pa, server


__all__ = [
    "VALID_SOURCES",
    "DuplicatePhysicalAccountError",
    "InvalidSourceError",
    "PhysicalAccountError",
    "PhysicalAccountNotFoundError",
    "create",
    "delete",
    "get",
    "get_with_server",
    "list_for_server",
]
