"""User CRUD + role / password / disable helpers.

Routers translate ``UserError`` subclasses into specific HTTP responses;
service code stays HTTP-agnostic so it stays unit-testable.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import User
from ..schemas.user import PasswordChange, UserCreate, UserRoleUpdate, UserUpdate
from ..security import hash_password, verify_password
from . import audit_service


class UserError(Exception):
    pass


class UserNotFoundError(UserError):
    pass


class DuplicateUserError(UserError):
    pass


class LastAdminError(UserError):
    """Refused: would leave the lab without an active lab_admin."""


class SelfRoleChangeError(UserError):
    """Refused: a user cannot change their own role."""


class SelfDisableError(UserError):
    """Refused: a user cannot disable themselves."""


class WrongPasswordError(UserError):
    pass


class UserNotActivatedError(UserError):
    """Refused: target user hasn't activated yet — resend the invite instead."""


class UserAlreadyActivatedError(UserError):
    """Refused: target user already activated — use password reset instead."""


async def list_users(session: AsyncSession, *, lab_id: int) -> Sequence[User]:
    result = await session.execute(select(User).where(User.lab_id == lab_id).order_by(User.id))
    return result.scalars().all()


async def get_user(session: AsyncSession, user_id: int, *, lab_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None or user.lab_id != lab_id:
        raise UserNotFoundError(f"user {user_id} not found")
    return user


async def create_user(
    session: AsyncSession,
    payload: UserCreate,
    *,
    lab_id: int,
    actor_user_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> User:
    user = User(
        lab_id=lab_id,
        username=payload.username,
        email=payload.email,
        display_name=payload.display_name,
        role=payload.role,
        password_hash=None,
        is_active=1,
        created_by_user_id=actor_user_id,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise DuplicateUserError("username or email already taken in this lab") from exc

    await audit_service.write(
        session,
        action="user.create",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="user",
        target_id=user.id,
        target_lab_id=lab_id,
        payload={"username": user.username, "email": user.email, "role": user.role},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return user


async def update_user(
    session: AsyncSession,
    user_id: int,
    payload: UserUpdate,
    *,
    actor_user_id: int,
    actor_role: str,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> User:
    user = await get_user(session, user_id, lab_id=lab_id)
    if actor_user_id != user.id and actor_role != "lab_admin":
        raise UserError("forbidden: only the user or a lab_admin may edit this profile")

    before = {"display_name": user.display_name, "email": user.email}
    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.email is not None:
        user.email = payload.email
    try:
        await session.flush()
    except IntegrityError as exc:
        raise DuplicateUserError("email already taken in this lab") from exc

    await audit_service.write(
        session,
        action="user.update",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="user",
        target_id=user.id,
        target_lab_id=lab_id,
        payload={
            "before": before,
            "after": {"display_name": user.display_name, "email": user.email},
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return user


async def change_role(
    session: AsyncSession,
    user_id: int,
    payload: UserRoleUpdate,
    *,
    actor_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> User:
    if actor_user_id == user_id:
        raise SelfRoleChangeError("you cannot change your own role")
    user = await get_user(session, user_id, lab_id=lab_id)
    if user.role == "lab_admin" and payload.role != "lab_admin":
        await _ensure_at_least_one_active_admin(session, exclude_user_id=user.id, lab_id=lab_id)
    old_role = user.role
    user.role = payload.role
    await session.flush()
    await audit_service.write(
        session,
        action="user.role_change",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="user",
        target_id=user.id,
        target_lab_id=lab_id,
        payload={"before": old_role, "after": user.role},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return user


async def disable_user(
    session: AsyncSession,
    user_id: int,
    *,
    actor_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> User:
    if actor_user_id == user_id:
        raise SelfDisableError("you cannot disable your own account")
    user = await get_user(session, user_id, lab_id=lab_id)
    if user.is_active and user.role == "lab_admin":
        await _ensure_at_least_one_active_admin(session, exclude_user_id=user.id, lab_id=lab_id)
    user.is_active = 0
    await session.flush()
    await audit_service.write(
        session,
        action="user.disable",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="user",
        target_id=user.id,
        target_lab_id=lab_id,
        ip_address=request_ip,
        user_agent=user_agent,
    )
    # Cascade: a disabled user must lose Linux-side reach. Soft-revoke every
    # active account_link (which also tears down pushed authorized_keys via
    # the agent) and every server_admin_grant. Reactivate does NOT auto-
    # restore these — re-granting is an explicit admin action (more secure).
    await _cascade_revoke_access(
        session,
        user_id=user_id,
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        request_ip=request_ip,
        user_agent=user_agent,
    )
    return user


async def reactivate_user(
    session: AsyncSession,
    user_id: int,
    *,
    actor_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> User:
    """Re-enable a previously-disabled user. Idempotent.

    Deliberately does NOT restore account_link / server_admin_grant that
    ``disable_user`` cascaded off — the admin must re-grant access explicitly
    so reactivation never silently re-opens Linux-side reach.
    """
    user = await get_user(session, user_id, lab_id=lab_id)
    if user.is_active:
        return user
    user.is_active = 1
    await session.flush()
    await audit_service.write(
        session,
        action="user.reactivate",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="user",
        target_id=user.id,
        target_lab_id=lab_id,
        payload={"access_restored": False},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return user


async def change_password(
    session: AsyncSession,
    *,
    actor_user_id: int,
    lab_id: int,
    payload: PasswordChange,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    user = await get_user(session, actor_user_id, lab_id=lab_id)
    if user.password_hash is None or not verify_password(payload.old_password, user.password_hash):
        await audit_service.write(
            session,
            action="user.password_change",
            actor_user_id=actor_user_id,
            lab_id=lab_id,
            target_type="user",
            target_id=actor_user_id,
            target_lab_id=lab_id,
            result="denied",
            error_message="wrong_old_password",
            ip_address=request_ip,
            user_agent=user_agent,
        )
        raise WrongPasswordError("old password does not match")
    user.password_hash = hash_password(payload.new_password)
    await audit_service.write(
        session,
        action="user.password_change",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="user",
        target_id=actor_user_id,
        target_lab_id=lab_id,
        ip_address=request_ip,
        user_agent=user_agent,
    )


async def issue_password_reset(
    session: AsyncSession,
    user_id: int,
    *,
    actor_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[User, str, datetime]:
    """Admin-proxy password reset: mint a one-shot reset token for a user.

    Only valid for an already-activated user (has a password). Reuses the
    setup_token framework with ``purpose='password_reset'``; the user
    completes it through the same activation flow in reset mode.
    """
    from . import auth_service

    user = await get_user(session, user_id, lab_id=lab_id)
    if user.password_hash is None:
        raise UserNotActivatedError(
            "user has not activated yet; resend the invite instead of resetting"
        )
    plaintext, expires_at = await auth_service.issue_setup_token(
        session,
        user_id=user.id,
        purpose="password_reset",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        request_ip=request_ip,
        user_agent=user_agent,
    )
    return user, plaintext, expires_at


async def resend_invite(
    session: AsyncSession,
    user_id: int,
    *,
    actor_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[User, str, datetime]:
    """Re-issue an activation link for a still-pending user.

    Invalidates any prior unused token (handled by ``issue_setup_token``)
    and mints a fresh one. Only valid for a not-yet-activated user.
    """
    from . import auth_service

    user = await get_user(session, user_id, lab_id=lab_id)
    if user.password_hash is not None:
        raise UserAlreadyActivatedError(
            "user already activated; use password reset instead of resending an invite"
        )
    plaintext, expires_at = await auth_service.issue_setup_token(
        session,
        user_id=user.id,
        purpose="activation",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        request_ip=request_ip,
        user_agent=user_agent,
    )
    return user, plaintext, expires_at


async def _ensure_at_least_one_active_admin(
    session: AsyncSession, *, exclude_user_id: int, lab_id: int
) -> None:
    # Concurrency audit cluster A (#20): a plain COUNT here is a
    # non-locking REPEATABLE READ snapshot, so two admins disabling /
    # downgrading the lab's last two admins concurrently could each count
    # the *other* as still active and both proceed (write-skew). Lock the
    # whole active-admin row set FOR UPDATE, ordered by id for a
    # deterministic lock order (so the two requests contend on the same
    # rows in the same sequence and serialize instead of deadlocking).
    # The second request then blocks, re-reads the committed state, and
    # correctly sees no admin remaining -> clean LastAdminError, instead
    # of relying on the incidental audit_log FK lock to abort it with a
    # 500. The target row is part of the locked set (it is an active
    # admin), so it is locked too.
    result = await session.execute(
        select(User.id)
        .where(
            User.lab_id == lab_id,
            User.role == "lab_admin",
            User.is_active == 1,
        )
        .order_by(User.id)
        .with_for_update()
    )
    remaining = [uid for uid in result.scalars().all() if uid != exclude_user_id]
    if len(remaining) < 1:
        raise LastAdminError("at least one active lab_admin must remain")


async def _cascade_revoke_access(
    session: AsyncSession,
    *,
    user_id: int,
    actor_user_id: int,
    lab_id: int,
    request_ip: str | None,
    user_agent: str | None,
) -> None:
    """Revoke a disabled user's active account_links + server_admin_grants.

    Reuses the canonical revoke paths so each write keeps its own audit row
    and the account_link revoke also tears down pushed authorized_keys
    (agent RPC; tolerant of an offline agent in mock/dev).
    """
    from ..models import AccountLink, PhysicalAccount, ServerAdminGrant
    from . import account_link_service, server_service

    link_rows = (
        await session.execute(
            select(AccountLink, PhysicalAccount.server_id)
            .join(PhysicalAccount, PhysicalAccount.id == AccountLink.physical_account_id)
            .where(AccountLink.user_id == user_id, AccountLink.is_active == 1)
        )
    ).all()
    for link, server_id in link_rows:
        await account_link_service.revoke_link(
            session,
            link=link,
            actor_user_id=actor_user_id,
            reason="user_disabled",
            lab_id=lab_id,
            server_id=server_id,
            revoke_key=True,
            request_ip=request_ip,
            user_agent=user_agent,
        )

    grant_rows = (
        (
            await session.execute(
                select(ServerAdminGrant).where(
                    ServerAdminGrant.user_id == user_id,
                    ServerAdminGrant.is_active == 1,
                )
            )
        )
        .scalars()
        .all()
    )
    for grant in grant_rows:
        await server_service.revoke_admin(
            session,
            server_id=grant.server_id,
            user_id=user_id,
            actor_user_id=actor_user_id,
            lab_id=lab_id,
            request_ip=request_ip,
            user_agent=user_agent,
        )
