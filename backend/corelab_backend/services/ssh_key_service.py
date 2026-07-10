"""SSH public key CRUD (self-service for the owning user)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import SshPublicKey
from ..schemas.ssh_key import SshKeyCreate
from . import audit_service
from .ssh_key_helpers import InvalidPublicKeyError, fingerprint_sha256, parse_public_key


class SshKeyError(Exception):
    pass


class DuplicateKeyError(SshKeyError):
    pass


class KeyNotFoundError(SshKeyError):
    pass


async def list_keys(
    session: AsyncSession, *, owner_user_id: int, active_only: bool = True
) -> Sequence[SshPublicKey]:
    stmt = select(SshPublicKey).where(SshPublicKey.user_id == owner_user_id)
    if active_only:
        stmt = stmt.where(SshPublicKey.is_active == 1)
    stmt = stmt.order_by(SshPublicKey.id)
    return (await session.execute(stmt)).scalars().all()


async def add_key(
    session: AsyncSession,
    payload: SshKeyCreate,
    *,
    owner_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> SshPublicKey:
    try:
        key_type, blob, comment = parse_public_key(payload.public_key)
    except InvalidPublicKeyError as exc:
        raise SshKeyError(str(exc)) from exc
    fp = fingerprint_sha256(blob)

    existing = await session.execute(
        select(SshPublicKey).where(
            SshPublicKey.user_id == owner_user_id,
            SshPublicKey.fingerprint_sha256 == fp,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicateKeyError(f"key with fingerprint {fp} already exists")

    row = SshPublicKey(
        user_id=owner_user_id,
        public_key=payload.public_key.strip(),
        fingerprint_sha256=fp,
        key_type=key_type,
        comment=payload.label or comment,
        is_active=1,
    )
    session.add(row)
    await session.flush()

    await audit_service.write(
        session,
        action="ssh_key.add",
        actor_user_id=owner_user_id,
        lab_id=lab_id,
        target_type="ssh_public_key",
        target_id=row.id,
        target_lab_id=lab_id,
        payload={"fingerprint_sha256": fp, "key_type": key_type},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return row


async def remove_key(
    session: AsyncSession,
    key_id: int,
    *,
    owner_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[SshPublicKey, Literal["deleted", "already_inactive"]]:
    row = await session.get(SshPublicKey, key_id)
    if row is None or row.user_id != owner_user_id:
        raise KeyNotFoundError(f"ssh key {key_id} not found")
    if not row.is_active:
        return row, "already_inactive"
    row.is_active = 0
    row.disabled_at = datetime.now(UTC)
    await audit_service.write(
        session,
        action="ssh_key.delete",
        actor_user_id=owner_user_id,
        lab_id=lab_id,
        target_type="ssh_public_key",
        target_id=row.id,
        target_lab_id=lab_id,
        payload={"fingerprint_sha256": row.fingerprint_sha256},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return row, "deleted"
