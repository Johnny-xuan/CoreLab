"""``/api/v1/account-links/*`` — challenge + verify (Phase 4 C2).

The CRUD / revoke / upgrade / admin_declared / reverse-lookup endpoints
land in Phase 4 C3. PAM password verify (``POST /try``) lands with
agent-side handlers in Phase 4 C5. This commit ships just the SSH
challenge/verify pair.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import (
    AuthenticatedUser,
    extract_request_context,
    get_current_user,
)
from ...db import get_session
from ...middleware.rate_limit import SshChallengeLockManager
from ...models import AuthorizedKeyEntry, PhysicalAccount, Server
from ...schemas.account_link import (
    AccountLinkRead,
    ChallengeIssued,
    ChallengeRequest,
    LinkRevokeRequest,
    PamVerifyEndpointRequest,
    PamVerifyEndpointResponse,
    UpgradeResponse,
    UpgradeViaChallengeRequest,
    VerifyRequest,
    VerifyResponse,
)
from ...services import (
    account_link_service,
    agent_rpc,
    audit_service,
    ssh_challenge,
)
from ...services.account_link_service import AccountLinkError

_ssh_lock = SshChallengeLockManager()

router = APIRouter(prefix="/account-links", tags=["account-links"])


@router.post("/challenge", response_model=ChallengeIssued)
async def create_challenge(
    payload: ChallengeRequest,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ChallengeIssued:
    # Phase 9 / FU-18 — docs/04 §5.5 "5min 3 次 lock 5min". The lock
    # uses the platform user id so a single attacker cannot use the
    # same account to grind the SSH signature space.
    if await _ssh_lock.is_locked(str(current.id)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "SSH_CHALLENGE_LOCKED"},
            headers={"Retry-After": "300"},
        )
    ip, ua = extract_request_context(request)

    server = await session.get(Server, payload.server_id)
    if server is None or server.lab_id != current.lab_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="server_not_found")

    try:
        pa = await account_link_service.resolve_physical_account(
            session, server_id=payload.server_id, linux_username=payload.linux_username
        )
    except account_link_service.PhysicalAccountNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    try:
        await account_link_service.assert_ssh_key_owned(
            session, ssh_public_key_id=payload.ssh_public_key_id, user_id=current.id
        )
    except account_link_service.SshKeyNotOwnedByUserError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    try:
        issued = await ssh_challenge.mint(
            user_id=current.id,
            server_id=payload.server_id,
            physical_account_id=pa.id,
            ssh_public_key_id=payload.ssh_public_key_id,
        )
    except ssh_challenge.RedisUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="redis_unavailable"
        ) from exc

    await audit_service.write(
        session,
        action="account_link.challenge.create",
        actor_user_id=current.id,
        lab_id=current.lab_id,
        target_type="physical_account",
        target_id=pa.id,
        target_lab_id=current.lab_id,
        target_server_id=payload.server_id,
        payload={
            "challenge_id": issued.challenge_id,
            "linux_username": payload.linux_username,
            "ssh_public_key_id": payload.ssh_public_key_id,
        },
        ip_address=ip,
        user_agent=ua,
    )

    return ChallengeIssued(
        challenge_id=issued.challenge_id,
        nonce=issued.nonce,
        expires_at=issued.expires_at,
        sign_command=issued.sign_command,
        signing_namespace=issued.signing_namespace,
    )


@router.post("/verify", response_model=VerifyResponse)
async def verify_challenge(
    payload: VerifyRequest,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> VerifyResponse:
    ip, ua = extract_request_context(request)

    # Phase 9 / FU-18 — lock guard at verify too (challenge mint and
    # verify share the same lock subject = user id).
    if await _ssh_lock.is_locked(str(current.id)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "SSH_CHALLENGE_LOCKED"},
            headers={"Retry-After": "300"},
        )

    try:
        ctx = await ssh_challenge.consume(payload.challenge_id, actor_user_id=current.id)
    except ssh_challenge.ChallengeExpiredOrUsedError as exc:
        await audit_service.write(
            session,
            action="account_link.verify_failed",
            actor_user_id=current.id,
            lab_id=current.lab_id,
            target_type="ssh_challenge",
            target_id=None,
            target_lab_id=current.lab_id,
            target_server_id=None,
            payload={"reason": "expired_or_used", "challenge_id": payload.challenge_id},
            ip_address=ip,
            user_agent=ua,
        )
        await _ssh_lock.record_failure(str(current.id))
        raise HTTPException(status.HTTP_410_GONE, detail="challenge_expired_or_used") from exc
    except ssh_challenge.ActorMismatchError as exc:
        await audit_service.write(
            session,
            action="account_link.verify_failed",
            actor_user_id=current.id,
            lab_id=current.lab_id,
            target_type="ssh_challenge",
            target_id=None,
            target_lab_id=current.lab_id,
            target_server_id=None,
            payload={"reason": "actor_mismatch", "challenge_id": payload.challenge_id},
            ip_address=ip,
            user_agent=ua,
        )
        await _ssh_lock.record_failure(str(current.id))
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="actor_mismatch") from exc
    except ssh_challenge.RedisUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="redis_unavailable"
        ) from exc

    # Ask the agent on ctx.server_id to verify the signature against the
    # server's real ``~/.ssh/authorized_keys`` (docs/04-security.md §5).
    # The RPC wiring lands in Phase 4 C4 (protocol) + C5 (agent handler);
    # until then the verify call surfaces 503 so the endpoint structure
    # is intact and the front-end gets a meaningful error.
    pa_row = await session.get(PhysicalAccount, ctx.physical_account_id)
    if pa_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="physical_account_gone")

    try:
        rpc_result = await agent_rpc.request_response(
            server_id=ctx.server_id,
            frame_type="backend.ssh.verify_sig",
            payload={
                "linux_username": pa_row.linux_username,
                "nonce": ctx.nonce,
                "namespace": ssh_challenge.SIGNING_NAMESPACE,
                "signature_armored": payload.signature_armored,
            },
        )
    except agent_rpc.RpcNotYetWiredError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="agent_rpc_not_implemented",
        ) from exc
    except agent_rpc.AgentOfflineError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except agent_rpc.AgentRpcTimeoutError as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc

    if not rpc_result.get("ok"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="signature_verification_failed")
    signer_fp = rpc_result.get("signer_fingerprint", "unknown")

    # Phase K K-fence — anti-forge: if the signed key was actually
    # placed there by CoreLab (情形 4 approval path), the trail must
    # say so. We audit-flag rather than silently rebrand, so an admin
    # can see "user piggy-backed on the platform-pushed key".
    plat_entry = await session.execute(
        select(AuthorizedKeyEntry).where(
            AuthorizedKeyEntry.physical_account_id == ctx.physical_account_id,
            AuthorizedKeyEntry.ssh_public_key_id == ctx.ssh_public_key_id,
            AuthorizedKeyEntry.is_active == 1,
        )
    )
    plat_match = plat_entry.scalar_one_or_none()
    if plat_match is not None:
        await audit_service.write(
            session,
            action="account_link.verify.platform_pushed_key",
            actor_user_id=current.id,
            lab_id=current.lab_id,
            target_type="account_link",
            target_id=None,
            target_lab_id=current.lab_id,
            target_server_id=ctx.server_id,
            payload={
                "challenge_id": ctx.challenge_id,
                "authorized_key_entry_id": plat_match.id,
                "note": "user verified via a key CoreLab itself pushed",
            },
            ip_address=ip,
            user_agent=ua,
        )

    try:
        link = await account_link_service.establish_via_ssh_challenge(
            session,
            user_id=ctx.user_id,
            physical_account_id=ctx.physical_account_id,
            challenge_id=ctx.challenge_id,
            signer_fingerprint=signer_fp,
            lab_id=current.lab_id,
            server_id=ctx.server_id,
            request_ip=ip,
            user_agent=ua,
        )
    except account_link_service.LinkAlreadyActiveError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return VerifyResponse(
        account_link=AccountLinkRead.model_validate(link),
        signer_fingerprint=signer_fp,
    )


@router.post("/try", response_model=PamVerifyEndpointResponse)
async def try_password(
    payload: PamVerifyEndpointRequest,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PamVerifyEndpointResponse:
    """PAM password verify path (docs/04-security.md §6).

    The plaintext password is forwarded to the agent over WSS, held in
    agent memory for the duration of ``pamela.authenticate``, then
    dropped. It never lands in audit_log, never lands on disk, and the
    structlog filter redacts ``password*`` fields end-to-end (invariant
    #4).
    """
    ip, ua = extract_request_context(request)

    from ...models import Server  # local import — avoid top-level cycle clutter

    server = await session.get(Server, payload.server_id)
    if server is None or server.lab_id != current.lab_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="server_not_found")

    try:
        pa = await account_link_service.resolve_physical_account(
            session, server_id=payload.server_id, linux_username=payload.linux_username
        )
    except account_link_service.PhysicalAccountNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    try:
        rpc_result = await agent_rpc.request_response(
            server_id=payload.server_id,
            frame_type="backend.pam.verify",
            payload={
                "linux_username": payload.linux_username,
                "password": payload.password,
            },
            timeout_seconds=15.0,
        )
    except agent_rpc.AgentOfflineError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except agent_rpc.AgentRpcTimeoutError as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except agent_rpc.RpcNotYetWiredError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="agent_rpc_not_implemented"
        ) from exc

    if not rpc_result.get("verify_ok"):
        await audit_service.write(
            session,
            action="account_link.pam_failed",
            actor_user_id=current.id,
            lab_id=current.lab_id,
            target_type="physical_account",
            target_id=pa.id,
            target_lab_id=current.lab_id,
            target_server_id=payload.server_id,
            payload={"linux_username": payload.linux_username, "reason": "pam_reject"},
            ip_address=ip,
            user_agent=ua,
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="pam_verify_failed")

    try:
        link = await account_link_service.establish_via_pam(
            session,
            user_id=current.id,
            physical_account_id=pa.id,
            lab_id=current.lab_id,
            server_id=payload.server_id,
            request_ip=ip,
            user_agent=ua,
        )
    except account_link_service.LinkAlreadyActiveError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return PamVerifyEndpointResponse(account_link=AccountLinkRead.model_validate(link))


@router.get("/{link_id}", response_model=AccountLinkRead)
async def get_link(
    link_id: int,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AccountLinkRead:
    try:
        link = await account_link_service.load_link_in_lab(
            session, link_id=link_id, lab_id=current.lab_id
        )
    except AccountLinkError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    # Visibility rule: a non-admin can only see their own links.
    if current.role != "lab_admin" and link.user_id != current.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not_owner")
    return AccountLinkRead.model_validate(link)


@router.post("/{link_id}/revoke", response_model=AccountLinkRead)
async def revoke_link(
    link_id: int,
    payload: LinkRevokeRequest,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AccountLinkRead:
    ip, ua = extract_request_context(request)
    try:
        link = await account_link_service.load_link_in_lab(
            session, link_id=link_id, lab_id=current.lab_id
        )
    except AccountLinkError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    is_self = link.user_id == current.id
    is_admin = current.role == "lab_admin"
    # Reason must match actor: 'self' for owner-revoke; lab_admin may
    # use 'admin_force' (also 'user_disabled' / 'pa_disabled' for the
    # cascade paths but those land with later phases).
    if payload.reason == "self" and not is_self:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not_owner")
    if payload.reason == "admin_force" and not is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not_admin")
    if payload.reason in ("user_disabled", "pa_disabled") and not is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not_admin")

    # Resolve server_id for audit context — link -> pa -> server.
    pa_id = link.physical_account_id
    from ...models import PhysicalAccount  # local import avoids API->models cycle clutter

    pa = await session.get(PhysicalAccount, pa_id)
    server_id = pa.server_id if pa is not None else 0

    try:
        revoked = await account_link_service.revoke_link(
            session,
            link=link,
            actor_user_id=current.id,
            reason=payload.reason,
            lab_id=current.lab_id,
            server_id=server_id,
            revoke_key=payload.revoke_key,
            request_ip=ip,
            user_agent=ua,
        )
    except AccountLinkError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return AccountLinkRead.model_validate(revoked)


@router.post("/{link_id}/upgrade-via-challenge", response_model=UpgradeResponse)
async def upgrade_via_challenge(
    link_id: int,
    payload: UpgradeViaChallengeRequest,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UpgradeResponse:
    """Upgrade an admin_declared link to a verified ssh_challenge link.

    Requires the user to first ``POST /challenge`` for the same
    (server, linux_username, ssh_key) and supply the resulting
    signature here. The old link flips inactive in the same
    transaction as the new one is written so reverse lookups never
    see two active rows for (user, pa).
    """
    ip, ua = extract_request_context(request)

    try:
        link = await account_link_service.load_link_in_lab(
            session, link_id=link_id, lab_id=current.lab_id
        )
    except AccountLinkError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if link.user_id != current.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not_owner")
    if link.source != "admin_declared":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"link {link.id} is source={link.source!r}; only admin_declared can upgrade",
        )

    try:
        ctx = await ssh_challenge.consume(payload.challenge_id, actor_user_id=current.id)
    except ssh_challenge.ChallengeExpiredOrUsedError as exc:
        raise HTTPException(status.HTTP_410_GONE, detail="challenge_expired_or_used") from exc
    except ssh_challenge.ActorMismatchError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="actor_mismatch") from exc
    except ssh_challenge.RedisUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="redis_unavailable"
        ) from exc

    if ctx.physical_account_id != link.physical_account_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="challenge_pa_mismatch",
        )

    pa_row = await session.get(PhysicalAccount, ctx.physical_account_id)
    if pa_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="physical_account_gone")

    try:
        rpc_result = await agent_rpc.request_response(
            server_id=ctx.server_id,
            frame_type="backend.ssh.verify_sig",
            payload={
                "linux_username": pa_row.linux_username,
                "nonce": ctx.nonce,
                "namespace": ssh_challenge.SIGNING_NAMESPACE,
                "signature_armored": payload.signature_armored,
            },
        )
    except agent_rpc.RpcNotYetWiredError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="agent_rpc_not_implemented"
        ) from exc
    except agent_rpc.AgentOfflineError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except agent_rpc.AgentRpcTimeoutError as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc

    if not rpc_result.get("ok"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="signature_verification_failed")
    signer_fp = rpc_result.get("signer_fingerprint", "unknown")

    try:
        new_link = await account_link_service.upgrade_admin_declared_to_ssh(
            session,
            user_id=current.id,
            physical_account_id=ctx.physical_account_id,
            challenge_id=ctx.challenge_id,
            signer_fingerprint=signer_fp,
            lab_id=current.lab_id,
            server_id=ctx.server_id,
            request_ip=ip,
            user_agent=ua,
        )
    except AccountLinkError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return UpgradeResponse(
        account_link=AccountLinkRead.model_validate(new_link),
        signer_fingerprint=signer_fp,
        upgraded_from_link_id=link.id,
    )
