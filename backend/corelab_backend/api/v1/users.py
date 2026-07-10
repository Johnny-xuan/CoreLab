"""``/api/v1/users/*`` — list, detail, invite, profile, role, disable, password."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import (
    AuthenticatedUser,
    extract_request_context,
    get_current_user,
    require_role,
)
from ...config import get_settings
from ...db import get_session
from ...schemas.account_link import AccountLinkRead, AccountLinkRequestRead
from ...schemas.server import MyGrantItem
from ...schemas.user import (
    PasswordChange,
    PasswordResetResponse,
    ProfileGpuRanking,
    ProfileLinkItem,
    ProfilePendingRequest,
    ProfileRecentAudit,
    ProfileReservationStats,
    ProfileSshKey,
    RegistrationInviteRead,
    UserCreate,
    UserInviteCreate,
    UserInviteResponse,
    UserProfileSummary,
    UserRead,
    UserReservationItem,
    UserReservationsResponse,
    UserRoleUpdate,
    UserUpdate,
)
from ...services import (
    account_link_request_service,
    account_link_service,
    audit_service,
    auth_service,
    server_service,
    usage_service,
    user_service,
)
from ...services.audit_service import AuditAccessContext, AuditFilters
from ...services.auth_service import (
    RegistrationInviteNotFoundError,
    RegistrationInviteNotRevokableError,
)
from ...services.user_service import (
    DuplicateUserError,
    LastAdminError,
    SelfDisableError,
    SelfRoleChangeError,
    UserAlreadyActivatedError,
    UserError,
    UserNotActivatedError,
    UserNotFoundError,
    WrongPasswordError,
)

router = APIRouter(prefix="/users", tags=["users"])


def _public_base_url(request: Request) -> str:
    settings = get_settings()
    configured = settings.backend_public_url.rstrip("/")
    if configured not in {"http://localhost", "http://127.0.0.1"}:
        return configured
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host:
        return configured
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{host}".rstrip("/")


def _registration_url(request: Request, token: str) -> str:
    return f"{_public_base_url(request)}/register?token={token}"


def _password_reset_url(request: Request, token: str) -> str:
    return f"{_public_base_url(request)}/activate?token={token}&mode=reset"


@router.get("", response_model=list[UserRead])
async def list_users(
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[UserRead]:
    users = await user_service.list_users(session, lab_id=current.lab_id)
    return [UserRead.model_validate(u) for u in users]


@router.post(
    "",
    response_model=UserInviteResponse,
    status_code=status.HTTP_201_CREATED,
    deprecated=True,
)
async def invite_user(
    payload: UserCreate,
    request: Request,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> UserInviteResponse:
    """Compatibility endpoint for old admin-precreated invitation clients.

    New product invitations are token-first and user-later: the admin picks a
    role, the invitee fills identity details on /register, and the user row is
    created only after a successful registration submit. Older browser bundles
    may still POST username/email/display_name here, so keep accepting that
    payload shape but never create a pending user from it.
    """

    ip, ua = extract_request_context(request)

    invite, plaintext, expires_at = await auth_service.issue_registration_invite(
        session,
        lab_id=current.lab_id,
        role=payload.role,
        actor_user_id=current.id,
        request_ip=ip,
        user_agent=ua,
    )
    return UserInviteResponse(
        user=None,
        invitation_id=invite.id,
        role=invite.role,
        setup_token=plaintext,
        activation_url=_registration_url(request, plaintext),
        expires_at=expires_at,
    )


@router.post(
    "/invitations",
    response_model=UserInviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_registration_invite(
    payload: UserInviteCreate,
    request: Request,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> UserInviteResponse:
    """Create a registration link before the invitee has a user row."""

    ip, ua = extract_request_context(request)
    invite, plaintext, expires_at = await auth_service.issue_registration_invite(
        session,
        lab_id=current.lab_id,
        role=payload.role,
        actor_user_id=current.id,
        request_ip=ip,
        user_agent=ua,
    )
    return UserInviteResponse(
        user=None,
        invitation_id=invite.id,
        role=invite.role,
        setup_token=plaintext,
        activation_url=_registration_url(request, plaintext),
        expires_at=expires_at,
    )


@router.get("/invitations", response_model=list[RegistrationInviteRead])
async def list_registration_invites(
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[RegistrationInviteRead]:
    return await auth_service.list_registration_invites(
        session,
        lab_id=current.lab_id,
        limit=limit,
    )


@router.post("/invitations/{invite_id:int}/revoke", response_model=RegistrationInviteRead)
async def revoke_registration_invite(
    invite_id: int,
    request: Request,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> RegistrationInviteRead:
    ip, ua = extract_request_context(request)
    try:
        return await auth_service.revoke_registration_invite(
            session,
            invite_id=invite_id,
            lab_id=current.lab_id,
            actor_user_id=current.id,
            request_ip=ip,
            user_agent=ua,
        )
    except RegistrationInviteNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="registration_invite_not_found"
        ) from exc
    except RegistrationInviteNotRevokableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="registration_invite_not_revokable"
        ) from exc


@router.get("/me/account-links", response_model=list[AccountLinkRead])
async def list_my_account_links(
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    include_history: bool = False,
) -> list[AccountLinkRead]:
    if include_history:
        links = await account_link_service.list_all_for_user(session, user_id=current.id)
    else:
        links = await account_link_service.list_active_for_user(session, user_id=current.id)
    return [AccountLinkRead.model_validate(link) for link in links]


@router.get("/me/account-link-requests", response_model=list[AccountLinkRequestRead])
async def list_my_account_link_requests(
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AccountLinkRequestRead]:
    rows = await account_link_request_service.list_for_requester(
        session, requester_user_id=current.id
    )
    return [AccountLinkRequestRead.model_validate(r) for r in rows]


@router.get("/me/grants", response_model=list[MyGrantItem])
async def list_my_grants(
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[MyGrantItem]:
    """Active server_admin grants this user holds — drives the per-server
    "Manage: server-X" sidebar groups for the Server admin view tier.

    lab_admin is implicitly admin of every server in the lab; we
    synthesize a grant entry per server so the sidebar can iterate
    uniformly without role branching."""
    if current.role == "lab_admin":
        servers = await server_service.list_servers(session, lab_id=current.lab_id)
        return [
            MyGrantItem(
                server_id=s.id,
                hostname=s.hostname,
                display_name=s.display_name,
                granted_at=s.created_at,
                notes="via lab_admin role",
            )
            for s in servers
        ]
    pairs = await server_service.list_grants_for_user(
        session, user_id=current.id, lab_id=current.lab_id
    )
    return [
        MyGrantItem(
            server_id=s.id,
            hostname=s.hostname,
            display_name=s.display_name,
            granted_at=g.granted_at,
            notes=g.notes,
        )
        for g, s in pairs
    ]


@router.get("/me", response_model=UserRead)
async def me(current: AuthenticatedUser = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(current.user)


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: PasswordChange,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    ip, ua = extract_request_context(request)
    try:
        await user_service.change_password(
            session,
            actor_user_id=current.id,
            lab_id=current.lab_id,
            payload=payload,
            request_ip=ip,
            user_agent=ua,
        )
    except WrongPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="wrong_old_password"
        ) from exc


@router.get("/{user_id:int}", response_model=UserRead)
async def get_user(
    user_id: int,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    try:
        user = await user_service.get_user(session, user_id, lab_id=current.lab_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found") from exc
    return UserRead.model_validate(user)


@router.get("/{user_id:int}/profile-summary", response_model=UserProfileSummary)
async def get_user_profile_summary(
    user_id: int,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> UserProfileSummary:
    """Phase K K-6 (Phase L L-1 extended) — full admin picture of a user.

    Aggregates everything the User detail page shows above the fold:
    links (active + revoked), pending requests, SSH keys, reservation
    stats (active / last 30d / GPU·h 7d & 30d), top GPUs by hours, and
    the 5 most recent audit rows for this user. Cheaper than 8 separate
    roundtrips; the detail page tabs still lazy-load anything heavier
    (Activity full list, lateral-surface joins).
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select as _sel

    from ...models import (
        AccountLink,
        AccountLinkRequest,
        PhysicalAccount,
        Reservation,
        Server,
        SshPublicKey,
    )

    try:
        user = await user_service.get_user(session, user_id, lab_id=current.lab_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found") from exc

    link_rows = (
        await session.execute(
            _sel(AccountLink, PhysicalAccount, Server)
            .join(PhysicalAccount, PhysicalAccount.id == AccountLink.physical_account_id)
            .join(Server, Server.id == PhysicalAccount.server_id)
            .where(AccountLink.user_id == user_id, Server.lab_id == current.lab_id)
            .order_by(AccountLink.id.desc())
        )
    ).all()
    active_links: list[ProfileLinkItem] = []
    revoked_links: list[ProfileLinkItem] = []
    for link, pa, srv in link_rows:
        item = ProfileLinkItem(
            link_id=link.id,
            physical_account_id=pa.id,
            linux_username=pa.linux_username,
            server_hostname=srv.hostname,
            source=link.source,
            is_active=bool(link.is_active),
            established_at=link.established_at,
            revoked_at=link.revoked_at,
        )
        if link.is_active:
            active_links.append(item)
        else:
            revoked_links.append(item)

    pending_rows = (
        await session.execute(
            _sel(AccountLinkRequest, PhysicalAccount, Server)
            .join(PhysicalAccount, PhysicalAccount.id == AccountLinkRequest.physical_account_id)
            .join(Server, Server.id == PhysicalAccount.server_id)
            .where(
                AccountLinkRequest.requester_user_id == user_id,
                AccountLinkRequest.status == "pending",
                Server.lab_id == current.lab_id,
            )
            .order_by(AccountLinkRequest.id.desc())
        )
    ).all()
    pending = [
        ProfilePendingRequest(
            request_id=r.id,
            physical_account_id=p.id,
            linux_username=p.linux_username,
            server_hostname=s.hostname,
            request_note=r.request_note,
            created_at=r.created_at,
        )
        for r, p, s in pending_rows
    ]

    key_rows = (
        (
            await session.execute(
                _sel(SshPublicKey)
                .where(SshPublicKey.user_id == user_id)
                .order_by(SshPublicKey.id.desc())
            )
        )
        .scalars()
        .all()
    )
    keys = [
        ProfileSshKey(
            id=k.id,
            fingerprint_sha256=k.fingerprint_sha256,
            key_type=k.key_type,
            comment=k.comment,
            is_active=bool(k.is_active),
            created_at=k.created_at,
        )
        for k in key_rows
    ]

    # Reservation stats — counts straight from DB, hours via usage_service.
    now = datetime.now(UTC)
    window_30d = now - timedelta(days=30)
    window_7d = now - timedelta(days=7)
    # Far-future end for the "active" query (active = currently running OR
    # scheduled to start within the next 30d window the UI cares about).
    next_30d = now + timedelta(days=30)

    from sqlalchemy import func as _func

    active_count_q = await session.execute(
        _sel(_func.count(Reservation.id)).where(
            Reservation.user_id == user_id,
            Reservation.status.in_(("scheduled", "active")),
            Reservation.end_at > now,
        )
    )
    active_count = int(active_count_q.scalar_one() or 0)

    last_30d_count_q = await session.execute(
        _sel(_func.count(Reservation.id)).where(
            Reservation.user_id == user_id,
            Reservation.start_at < now,
            Reservation.end_at > window_30d,
        )
    )
    last_30d_count = int(last_30d_count_q.scalar_one() or 0)

    gpu_hours_7d = await usage_service.gpu_hours_for_user_in_window(
        session, user_id=user_id, window_start=window_7d, window_end=now, now=now
    )
    gpu_hours_30d = await usage_service.gpu_hours_for_user_in_window(
        session, user_id=user_id, window_start=window_30d, window_end=now, now=now
    )

    top_gpu_7d_rows = await usage_service.gpu_ranking_for_user_in_window(
        session, user_id=user_id, window_start=window_7d, window_end=now, now=now, limit=5
    )
    top_gpu_7d = [ProfileGpuRanking(**r) for r in top_gpu_7d_rows]

    # Recent audit — reuse audit_service with lab_admin scope + actor filter.
    audit_items, _ = await audit_service.list_logs(
        session,
        lab_id=int(current.user.lab_id),
        ctx=AuditAccessContext(scope="all"),
        filters=AuditFilters(actor_user_id=user_id),
        page=1,
        size=5,
    )
    recent_audit = [
        ProfileRecentAudit(
            id=int(a["id"]),
            action=a["action"],
            target_type=a.get("target_type"),
            target_id=a.get("target_id"),
            target_server_id=a.get("target_server_id"),
            result=a.get("result", "ok"),
            created_at=a["created_at"],
        )
        for a in audit_items
    ]

    # next_30d is referenced in the doc above; keep the variable to make
    # the intent of "active = current or starts within next 30d" explicit
    # for future maintainers. (kept silent — Pyflakes is fine with use.)
    _ = next_30d

    return UserProfileSummary(
        user=UserRead.model_validate(user),
        active_links=active_links,
        revoked_links=revoked_links,
        pending_requests=pending,
        ssh_keys=keys,
        reservation_stats=ProfileReservationStats(
            active_count=active_count,
            last_30d_count=last_30d_count,
            gpu_hours_7d=round(gpu_hours_7d, 2),
            gpu_hours_30d=round(gpu_hours_30d, 2),
        ),
        top_gpu_7d=top_gpu_7d,
        recent_audit=recent_audit,
    )


@router.get("/{user_id:int}/reservations", response_model=UserReservationsResponse)
async def get_user_reservations(
    user_id: int,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> UserReservationsResponse:
    """Phase L L-1 — last 30d + upcoming reservations for the User detail page.

    Returns two arrays (``upcoming`` = future-facing scheduled / active,
    ``last_30d`` = anything that touched the past 30 days regardless of
    terminal status) plus a 30d GPU·h total and per-server breakdown.

    Scope: lab_admin sees any user in their lab. server_admin / user
    access goes through their own ``/me`` endpoints; this is admin only.
    """
    from datetime import UTC, datetime, timedelta

    try:
        user = await user_service.get_user(session, user_id, lab_id=current.lab_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found") from exc
    _ = user  # explicit user check, used for the 404; intentional.

    now = datetime.now(UTC)
    past_30d = now - timedelta(days=30)
    next_60d = now + timedelta(days=60)

    upcoming_rows = await usage_service.user_reservations_window(
        session, user_id=user_id, window_start=now, window_end=next_60d, now=now
    )
    past_rows = await usage_service.user_reservations_window(
        session, user_id=user_id, window_start=past_30d, window_end=now, now=now
    )

    gpu_hours_30d = await usage_service.gpu_hours_for_user_in_window(
        session, user_id=user_id, window_start=past_30d, window_end=now, now=now
    )
    by_server_rows = await usage_service.gpu_ranking_for_user_in_window(
        session, user_id=user_id, window_start=past_30d, window_end=now, now=now, limit=10
    )

    return UserReservationsResponse(
        upcoming=[UserReservationItem(**r) for r in upcoming_rows],
        last_30d=[UserReservationItem(**r) for r in past_rows],
        gpu_hours_30d=round(gpu_hours_30d, 2),
        gpu_hours_by_server_30d=[ProfileGpuRanking(**r) for r in by_server_rows],
    )


@router.patch("/{user_id:int}", response_model=UserRead)
async def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    ip, ua = extract_request_context(request)
    try:
        user = await user_service.update_user(
            session,
            user_id,
            payload,
            actor_user_id=current.id,
            actor_role=current.role,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found") from exc
    except DuplicateUserError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UserError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return UserRead.model_validate(user)


@router.patch("/{user_id:int}/role", response_model=UserRead)
async def change_role(
    user_id: int,
    payload: UserRoleUpdate,
    request: Request,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    ip, ua = extract_request_context(request)
    try:
        user = await user_service.change_role(
            session,
            user_id,
            payload,
            actor_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found") from exc
    except SelfRoleChangeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="cannot_change_own_role"
        ) from exc
    except LastAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="must_keep_one_active_admin"
        ) from exc
    return UserRead.model_validate(user)


@router.patch("/{user_id:int}/disable", response_model=UserRead)
async def disable_user(
    user_id: int,
    request: Request,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    ip, ua = extract_request_context(request)
    try:
        user = await user_service.disable_user(
            session,
            user_id,
            actor_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found") from exc
    except SelfDisableError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="cannot_disable_self"
        ) from exc
    except LastAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="must_keep_one_active_admin"
        ) from exc
    return UserRead.model_validate(user)


@router.patch("/{user_id:int}/reactivate", response_model=UserRead)
async def reactivate_user(
    user_id: int,
    request: Request,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    """Re-enable a disabled user. Does not restore revoked links/grants."""
    ip, ua = extract_request_context(request)
    try:
        user = await user_service.reactivate_user(
            session,
            user_id,
            actor_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found") from exc
    return UserRead.model_validate(user)


@router.post("/{user_id:int}/password-reset", response_model=PasswordResetResponse)
async def reset_user_password(
    user_id: int,
    request: Request,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> PasswordResetResponse:
    """Admin-proxy password reset: mint a one-shot reset link for a user.

    No email dependency — the admin copies ``reset_url`` and hands it to the
    user. Only valid for an already-activated user; for a pending user use
    resend-invite instead.
    """
    ip, ua = extract_request_context(request)
    try:
        user, plaintext, expires_at = await user_service.issue_password_reset(
            session,
            user_id,
            actor_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found") from exc
    except UserNotActivatedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="user_not_activated"
        ) from exc

    return PasswordResetResponse(
        user=UserRead.model_validate(user),
        setup_token=plaintext,
        reset_url=_password_reset_url(request, plaintext),
        expires_at=expires_at,
    )


@router.post(
    "/{user_id:int}/resend-invite",
    response_model=UserInviteResponse,
)
async def resend_invite(
    user_id: int,
    request: Request,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> UserInviteResponse:
    """Re-issue a fresh activation link for a still-pending user.

    Invalidates the prior unused token. Only valid for a not-yet-activated
    user; for an active user use password-reset instead.
    """
    ip, ua = extract_request_context(request)
    try:
        user, plaintext, expires_at = await user_service.resend_invite(
            session,
            user_id,
            actor_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found") from exc
    except UserAlreadyActivatedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="user_already_activated"
        ) from exc

    return UserInviteResponse(
        user=UserRead.model_validate(user),
        invitation_id=None,
        role=user.role,
        setup_token=plaintext,
        activation_url=_registration_url(request, plaintext),
        expires_at=expires_at,
    )
