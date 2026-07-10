"""Login / fetch-me / activation flow.

Token issuance lives here; FastAPI dependencies in
:mod:`corelab_backend.auth_dependencies` consume the issued tokens.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..config import get_settings
from ..middleware.rate_limit import LoginLockManager
from ..models import RegistrationInvite, SetupToken, SshPublicKey, User
from ..schemas.auth import LoginRequest, LoginResponse
from ..schemas.setup import ActivateValidate
from ..schemas.ssh_key import SshKeyCreate
from ..schemas.user import RegistrationInviteRead, RegistrationInviteUserRef, UserRead
from ..security import (
    generate_setup_token,
    hash_password,
    hash_setup_token,
    issue_access_token,
    verify_password,
)
from . import audit_service, ssh_key_service
from .ssh_key_helpers import InvalidPublicKeyError, fingerprint_sha256, parse_public_key

_login_lock = LoginLockManager()


class AuthError(Exception):
    """Authentication failures and bad credentials surface as this type."""


class InvalidCredentialsError(AuthError):
    pass


class AccountDisabledError(AuthError):
    pass


class AccountLockedError(AuthError):
    """Raised when /auth/login is locked due to repeated failures."""


class InvalidActivationTokenError(AuthError):
    pass


class ActivationProfileConflictError(AuthError):
    pass


class ActivationSshKeyError(AuthError):
    pass


class RegistrationInviteNotFoundError(AuthError):
    pass


class RegistrationInviteNotRevokableError(AuthError):
    pass


def _to_user_read(user: User) -> UserRead:
    return UserRead.model_validate(user)


async def authenticate(
    session: AsyncSession,
    payload: LoginRequest,
    *,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> LoginResponse:
    # Phase 9 / FU-18 — failure-count lock (docs/04 §4.2). Lock key
    # uses ``username`` + ``ip`` so a single attacker IP cannot lock
    # an unrelated user just by guessing their username.
    lock_subject = f"{payload.username}:{request_ip or 'noip'}"
    if await _login_lock.is_locked(lock_subject):
        raise AccountLockedError("too many failed attempts; try again later")

    result = await session.execute(
        select(User).where((User.username == payload.username) | (User.email == payload.username))
    )
    user = result.scalar_one_or_none()
    if user is None or user.password_hash is None:
        await audit_service.write(
            session,
            action="auth.login",
            actor_user_id=None,
            target_type="user",
            target_id=None,
            payload={"username_attempt": payload.username},
            ip_address=request_ip,
            user_agent=user_agent,
            result="denied",
            error_message="invalid_credentials",
        )
        await _login_lock.record_failure(lock_subject)
        raise InvalidCredentialsError("invalid username or password")
    if not verify_password(payload.password, user.password_hash):
        await audit_service.write(
            session,
            action="auth.login",
            actor_user_id=user.id,
            lab_id=user.lab_id,
            target_type="user",
            target_id=user.id,
            ip_address=request_ip,
            user_agent=user_agent,
            result="denied",
            error_message="invalid_credentials",
        )
        await _login_lock.record_failure(lock_subject)
        raise InvalidCredentialsError("invalid username or password")
    if not user.is_active:
        await audit_service.write(
            session,
            action="auth.login",
            actor_user_id=user.id,
            lab_id=user.lab_id,
            target_type="user",
            target_id=user.id,
            ip_address=request_ip,
            user_agent=user_agent,
            result="denied",
            error_message="account_disabled",
        )
        raise AccountDisabledError("account disabled")

    token, expires_at = issue_access_token(user_id=user.id, lab_id=user.lab_id, role=user.role)
    user.last_login_at = datetime.now(UTC)
    await audit_service.write(
        session,
        action="auth.login",
        actor_user_id=user.id,
        lab_id=user.lab_id,
        target_type="user",
        target_id=user.id,
        ip_address=request_ip,
        user_agent=user_agent,
        result="ok",
    )
    # Phase 9 / FU-18 — successful login resets the failure counter so
    # the next mistyped password does not insta-trip the lock.
    await _login_lock.record_success(lock_subject)
    return LoginResponse(
        access_token=token,
        expires_at=expires_at,
        user=_to_user_read(user),
    )


async def record_logout(
    session: AsyncSession,
    *,
    actor_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Stateless logout: write an audit row. Token revocation lands Phase 3+."""
    await audit_service.write(
        session,
        action="auth.logout",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="user",
        target_id=actor_user_id,
        ip_address=request_ip,
        user_agent=user_agent,
    )


async def issue_setup_token(
    session: AsyncSession,
    *,
    user_id: int,
    purpose: str,
    actor_user_id: int | None,
    lab_id: int | None = None,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, datetime]:
    """Mint a fresh setup token for ``user_id``. Returns ``(plaintext, expires_at)``.

    Invalidates any previously-issued unused token for the same user.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    # Invalidate prior unused tokens — single live token per user at a time.
    prior = await session.execute(
        select(SetupToken).where(SetupToken.user_id == user_id, SetupToken.used_at.is_(None))
    )
    for old in prior.scalars().all():
        old.used_at = now

    plaintext, hashed = generate_setup_token()
    expires_at = now + timedelta(hours=settings.setup_token_ttl_hours)
    row = SetupToken(
        user_id=user_id,
        token_hash=hashed,
        purpose=purpose,
        expires_at=expires_at,
        created_by_user_id=actor_user_id,
    )
    session.add(row)
    await session.flush()
    await audit_service.write(
        session,
        action="setup_token.invite" if purpose == "activation" else "setup_token.password_reset",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="user",
        target_id=user_id,
        target_lab_id=lab_id,
        payload={"purpose": purpose, "expires_at": expires_at.isoformat()},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return plaintext, expires_at


async def issue_registration_invite(
    session: AsyncSession,
    *,
    lab_id: int,
    role: str,
    actor_user_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[RegistrationInvite, str, datetime]:
    """Mint an invitation token that is not tied to a user row yet."""

    settings = get_settings()
    now = datetime.now(UTC)
    plaintext, hashed = generate_setup_token()
    expires_at = now + timedelta(hours=settings.setup_token_ttl_hours)
    row = RegistrationInvite(
        lab_id=lab_id,
        token_hash=hashed,
        role=role,
        expires_at=expires_at,
        created_by_user_id=actor_user_id,
    )
    session.add(row)
    await session.flush()
    await audit_service.write(
        session,
        action="registration_invite.create",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="registration_invite",
        target_id=row.id,
        target_lab_id=lab_id,
        payload={"role": role, "expires_at": expires_at.isoformat()},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return row, plaintext, expires_at


def _invite_status(
    invite: RegistrationInvite, now: datetime
) -> Literal["active", "used", "expired", "revoked"]:
    if invite.used_at is not None:
        return "used" if invite.used_by_user_id is not None else "revoked"
    if invite.expires_at.replace(tzinfo=UTC) <= now:
        return "expired"
    return "active"


def _user_ref(user: User | None) -> RegistrationInviteUserRef | None:
    if user is None:
        return None
    return RegistrationInviteUserRef(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
    )


def _invite_read(
    invite: RegistrationInvite,
    *,
    creator: User | None,
    used_by: User | None,
    now: datetime,
) -> RegistrationInviteRead:
    invite_status = _invite_status(invite, now)
    return RegistrationInviteRead(
        id=invite.id,
        role=cast(Literal["user", "lab_admin"], invite.role),
        status=invite_status,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        used_at=invite.used_at,
        created_by=_user_ref(creator),
        used_by=_user_ref(used_by),
        can_revoke=invite_status == "active",
    )


async def list_registration_invites(
    session: AsyncSession,
    *,
    lab_id: int,
    limit: int = 50,
) -> list[RegistrationInviteRead]:
    creator = aliased(User)
    used_by = aliased(User)
    stmt = (
        select(RegistrationInvite, creator, used_by)
        .outerjoin(creator, creator.id == RegistrationInvite.created_by_user_id)
        .outerjoin(used_by, used_by.id == RegistrationInvite.used_by_user_id)
        .where(RegistrationInvite.lab_id == lab_id)
        .order_by(desc(RegistrationInvite.id))
        .limit(limit)
    )
    now = datetime.now(UTC)
    rows = (await session.execute(stmt)).all()
    return [
        _invite_read(invite, creator=creator_user, used_by=used_by_user, now=now)
        for invite, creator_user, used_by_user in rows
    ]


async def get_registration_invite(
    session: AsyncSession,
    *,
    invite_id: int,
    lab_id: int,
) -> RegistrationInviteRead:
    creator = aliased(User)
    used_by = aliased(User)
    stmt = (
        select(RegistrationInvite, creator, used_by)
        .outerjoin(creator, creator.id == RegistrationInvite.created_by_user_id)
        .outerjoin(used_by, used_by.id == RegistrationInvite.used_by_user_id)
        .where(RegistrationInvite.id == invite_id, RegistrationInvite.lab_id == lab_id)
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        raise RegistrationInviteNotFoundError("registration_invite_not_found")
    invite, creator_user, used_by_user = row
    return _invite_read(
        invite,
        creator=creator_user,
        used_by=used_by_user,
        now=datetime.now(UTC),
    )


async def revoke_registration_invite(
    session: AsyncSession,
    *,
    invite_id: int,
    lab_id: int,
    actor_user_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> RegistrationInviteRead:
    stmt = (
        select(RegistrationInvite)
        .where(RegistrationInvite.id == invite_id, RegistrationInvite.lab_id == lab_id)
        .with_for_update()
    )
    invite = (await session.execute(stmt)).scalar_one_or_none()
    if invite is None:
        raise RegistrationInviteNotFoundError("registration_invite_not_found")

    now = datetime.now(UTC)
    if _invite_status(invite, now) != "active":
        raise RegistrationInviteNotRevokableError("registration_invite_not_revokable")

    invite.used_at = now
    invite.used_by_user_id = None
    await session.flush()
    await audit_service.write(
        session,
        action="registration_invite.revoke",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="registration_invite",
        target_id=invite.id,
        target_lab_id=lab_id,
        payload={"role": invite.role, "expires_at": invite.expires_at.isoformat()},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return await get_registration_invite(session, invite_id=invite_id, lab_id=lab_id)


async def lookup_active_token(session: AsyncSession, token_plaintext: str) -> SetupToken:
    hashed = hash_setup_token(token_plaintext)
    result = await session.execute(select(SetupToken).where(SetupToken.token_hash == hashed))
    row = result.scalar_one_or_none()
    if row is None:
        raise InvalidActivationTokenError("token not found")
    if row.used_at is not None:
        raise InvalidActivationTokenError("token already used")
    if row.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
        raise InvalidActivationTokenError("token expired")
    return row


async def lookup_active_registration_invite(
    session: AsyncSession, token_plaintext: str
) -> RegistrationInvite:
    hashed = hash_setup_token(token_plaintext)
    result = await session.execute(
        select(RegistrationInvite).where(RegistrationInvite.token_hash == hashed)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise InvalidActivationTokenError("token not found")
    if row.used_at is not None:
        raise InvalidActivationTokenError("token already used")
    if row.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
        raise InvalidActivationTokenError("token expired")
    return row


async def validate_activation_token(session: AsyncSession, token: str) -> ActivateValidate:
    try:
        row = await lookup_active_token(session, token)
    except InvalidActivationTokenError as setup_exc:
        if str(setup_exc) != "token not found":
            raise
        invite = await lookup_active_registration_invite(session, token)
        return ActivateValidate(
            user_id=None,
            username=None,
            email=None,
            display_name=None,
            purpose="registration",
            role=invite.role,
        )

    user = await session.get(User, row.user_id)
    if user is None:
        raise InvalidActivationTokenError("user no longer exists")
    return ActivateValidate(
        user_id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        purpose=row.purpose,
        role=user.role,
    )


async def activate(
    session: AsyncSession,
    *,
    token: str,
    new_password: str,
    username: str | None = None,
    email: str | None = None,
    display_name: str | None = None,
    ssh_key_label: str | None = None,
    ssh_key_public_key: str | None = None,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> User:
    try:
        row = await lookup_active_token(session, token)
    except InvalidActivationTokenError as setup_exc:
        if str(setup_exc) != "token not found":
            raise
        invite = await lookup_active_registration_invite(session, token)
        return await _complete_registration_invite(
            session,
            invite=invite,
            new_password=new_password,
            username=username,
            email=email,
            display_name=display_name,
            ssh_key_label=ssh_key_label,
            ssh_key_public_key=ssh_key_public_key,
            request_ip=request_ip,
            user_agent=user_agent,
        )

    user = await session.get(User, row.user_id)
    if user is None:
        raise InvalidActivationTokenError("user no longer exists")

    if row.purpose == "activation":
        if user.password_hash is not None:
            raise InvalidActivationTokenError("user already activated")
        await _validate_registration_profile(
            session,
            user=user,
            username=username,
            email=email,
        )
        if ssh_key_public_key is not None:
            await _validate_registration_ssh_key(
                session,
                user_id=user.id,
                public_key=ssh_key_public_key,
            )
    elif row.purpose == "password_reset":
        if username is not None or email is not None or display_name is not None:
            raise InvalidActivationTokenError("password reset cannot update profile")
        if ssh_key_public_key is not None or ssh_key_label is not None:
            raise InvalidActivationTokenError("password reset cannot add ssh key")
    else:
        raise InvalidActivationTokenError("unsupported setup token purpose")

    if row.purpose == "activation":
        if username is not None:
            user.username = username
        if email is not None:
            user.email = email
        if display_name is not None:
            user.display_name = display_name

    user.password_hash = hash_password(new_password)
    row.used_at = datetime.now(UTC)
    await session.flush()

    if row.purpose == "activation" and ssh_key_public_key is not None:
        await ssh_key_service.add_key(
            session,
            SshKeyCreate(public_key=ssh_key_public_key, label=ssh_key_label),
            owner_user_id=user.id,
            lab_id=user.lab_id,
            request_ip=request_ip,
            user_agent=user_agent,
        )

    await audit_service.write(
        session,
        action="setup_token.activate",
        actor_user_id=user.id,
        lab_id=user.lab_id,
        target_type="user",
        target_id=user.id,
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return user


async def _complete_registration_invite(
    session: AsyncSession,
    *,
    invite: RegistrationInvite,
    new_password: str,
    username: str | None,
    email: str | None,
    display_name: str | None,
    ssh_key_label: str | None,
    ssh_key_public_key: str | None,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> User:
    if username is None or email is None or display_name is None:
        raise InvalidActivationTokenError("registration requires username, email and display_name")

    await _validate_new_registration_profile(
        session,
        lab_id=invite.lab_id,
        username=username,
        email=email,
    )
    if ssh_key_public_key is not None:
        await _validate_registration_ssh_key(
            session,
            user_id=None,
            public_key=ssh_key_public_key,
        )

    user = User(
        lab_id=invite.lab_id,
        username=username,
        email=email,
        display_name=display_name,
        role=invite.role,
        password_hash=hash_password(new_password),
        is_active=1,
        created_by_user_id=invite.created_by_user_id,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ActivationProfileConflictError("username_or_email_taken") from exc

    invite.used_at = datetime.now(UTC)
    invite.used_by_user_id = user.id
    await session.flush()

    if ssh_key_public_key is not None:
        await ssh_key_service.add_key(
            session,
            SshKeyCreate(public_key=ssh_key_public_key, label=ssh_key_label),
            owner_user_id=user.id,
            lab_id=user.lab_id,
            request_ip=request_ip,
            user_agent=user_agent,
        )

    await audit_service.write(
        session,
        action="user.register",
        actor_user_id=user.id,
        lab_id=user.lab_id,
        target_type="user",
        target_id=user.id,
        target_lab_id=user.lab_id,
        payload={"registration_invite_id": invite.id, "role": user.role},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    await audit_service.write(
        session,
        action="registration_invite.consume",
        actor_user_id=user.id,
        lab_id=user.lab_id,
        target_type="registration_invite",
        target_id=invite.id,
        target_lab_id=user.lab_id,
        payload={"user_id": user.id},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return user


async def _validate_registration_profile(
    session: AsyncSession,
    *,
    user: User,
    username: str | None,
    email: str | None,
) -> None:
    if username is not None and username != user.username:
        existing_username = await session.execute(
            select(User.id).where(
                User.lab_id == user.lab_id,
                User.username == username,
                User.id != user.id,
            )
        )
        if existing_username.scalar_one_or_none() is not None:
            raise ActivationProfileConflictError("username_taken")

    if email is not None and email != user.email:
        existing_email = await session.execute(
            select(User.id).where(
                User.lab_id == user.lab_id,
                User.email == email,
                User.id != user.id,
            )
        )
        if existing_email.scalar_one_or_none() is not None:
            raise ActivationProfileConflictError("email_taken")


async def _validate_new_registration_profile(
    session: AsyncSession,
    *,
    lab_id: int,
    username: str,
    email: str,
) -> None:
    existing_username = await session.execute(
        select(User.id).where(User.lab_id == lab_id, User.username == username)
    )
    if existing_username.scalar_one_or_none() is not None:
        raise ActivationProfileConflictError("username_taken")

    existing_email = await session.execute(
        select(User.id).where(User.lab_id == lab_id, User.email == email)
    )
    if existing_email.scalar_one_or_none() is not None:
        raise ActivationProfileConflictError("email_taken")


async def _validate_registration_ssh_key(
    session: AsyncSession,
    *,
    user_id: int | None,
    public_key: str,
) -> None:
    try:
        _key_type, blob, _comment = parse_public_key(public_key)
    except InvalidPublicKeyError as exc:
        raise ActivationSshKeyError(str(exc)) from exc

    fp = fingerprint_sha256(blob)
    if user_id is not None:
        existing = await session.execute(
            select(SshPublicKey.id).where(
                SshPublicKey.user_id == user_id,
                SshPublicKey.fingerprint_sha256 == fp,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ActivationSshKeyError(f"key with fingerprint {fp} already exists")


def random_invite_password() -> str:
    """Random one-shot password used to seed user creation before activation."""
    return secrets.token_urlsafe(24)
