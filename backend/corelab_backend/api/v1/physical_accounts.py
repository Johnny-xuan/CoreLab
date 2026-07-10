"""PhysicalAccount endpoints + admin_declared owner.

Reads (list / detail) are open to any authenticated user in the lab —
transparency by default (philosophy #3). Writes (create / delete /
declare-owner) require lab_admin. The declare-owner path writes a
``source='admin_declared'`` account_link in the same transaction; per
invariant #5 this link is reverse-lookup-only and cannot be used for
act-as operations.
"""

from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import (
    AuthenticatedUser,
    assert_server_admin,
    extract_request_context,
    get_current_user,
)
from ...db import get_session
from ...models import Server, User
from ...schemas.account_link import AccountLinkRead
from ...schemas.physical_account import (
    AdminDeclareOwnerRequest,
    AuthorizedKeyInventoryEntry,
    AuthorizedKeyReadbackResponse,
    AuthorizedKeyRetryResponse,
    OnboardUserRequest,
    OnboardUserResponse,
    PhysicalAccountCreate,
    PhysicalAccountRead,
    ReverseLookupEntry,
    ReverseLookupResponse,
)
from ...services import (
    account_link_service,
    agent_rpc,
    audit_service,
    physical_account_service,
)
from ...services.account_link_service import LinkAlreadyActiveError
from ...services.physical_account_service import (
    AuthorizedKeyEntryNotFoundError,
    DuplicatePhysicalAccountError,
    InvalidSourceError,
    PhysicalAccountNotFoundError,
)

router = APIRouter(tags=["physical-accounts"])


def _assert_server_in_lab(server: Server | None, lab_id: int) -> Server:
    if server is None or server.lab_id != lab_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="server_not_found")
    return server


@router.get("/servers/{server_id}/physical-accounts", response_model=list[PhysicalAccountRead])
async def list_pas(
    server_id: int,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[PhysicalAccountRead]:
    server = await session.get(Server, server_id)
    _assert_server_in_lab(server, current.lab_id)
    pas = await physical_account_service.list_for_server(session, server_id=server_id)
    return [PhysicalAccountRead.model_validate(pa) for pa in pas]


@router.get(
    "/servers/{server_id}/authorized-key-entries",
    response_model=list[AuthorizedKeyInventoryEntry],
)
async def list_authorized_key_entries(
    server_id: int,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AuthorizedKeyInventoryEntry]:
    server = await session.get(Server, server_id)
    _assert_server_in_lab(server, current.lab_id)
    await assert_server_admin(session, user=current, server_id=server_id)
    rows = await physical_account_service.list_authorized_key_inventory(
        session, server_id=server_id, lab_id=current.lab_id
    )
    return [AuthorizedKeyInventoryEntry.model_validate(row) for row in rows]


@router.post(
    "/servers/{server_id}/physical-accounts",
    response_model=PhysicalAccountRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_pa(
    server_id: int,
    payload: PhysicalAccountCreate,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PhysicalAccountRead:
    await assert_server_admin(session, user=current, server_id=server_id)
    ip, ua = extract_request_context(request)
    try:
        pa = await physical_account_service.create(
            session,
            server_id=server_id,
            linux_username=payload.linux_username,
            source=payload.source,
            notes=payload.notes,
            actor_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except PhysicalAccountNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DuplicatePhysicalAccountError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidSourceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return PhysicalAccountRead.model_validate(pa)


@router.post(
    "/servers/{server_id}/onboard-user",
    response_model=OnboardUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def onboard_user(
    server_id: int,
    payload: OnboardUserRequest,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OnboardUserResponse:
    await assert_server_admin(session, user=current, server_id=server_id)
    """Phase K K-7 — one-stop "create the Linux account and grant access".

    Composes three side effects in a single transaction:
      1. agent ``backend.linux.useradd`` — create the Linux user
      2. write ``physical_account`` row
      3. agent ``backend.authorized_key.push`` — drop the chosen pubkey
         into ``~user/.ssh/authorized_keys``
      4. write ``authorized_key_entry`` + ``admin_declared`` account_link

    If any agent RPC fails we still flush the local rows we already
    wrote — recovery is "retry the failed step" instead of "redo
    everything". The audit payload captures both outcomes so the
    operator can tell which step bailed.
    """
    from ...models import AuthorizedKeyEntry, SshPublicKey

    ip, ua = extract_request_context(request)
    server = await session.get(Server, server_id)
    _assert_server_in_lab(server, current.lab_id)
    assert server is not None

    owner = await session.get(User, payload.owner_user_id)
    if owner is None or owner.lab_id != current.lab_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="owner_not_found")

    key = await session.get(SshPublicKey, payload.ssh_public_key_id)
    if key is None or key.user_id != owner.id or not key.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="ssh_key_not_owned_or_inactive")

    # 1. useradd RPC
    useradd_outcome: dict = {"attempted": True}
    try:
        useradd_outcome.update(
            await agent_rpc.request_response(
                server_id=server.id,
                frame_type="backend.linux.useradd",
                payload={"linux_username": payload.linux_username},
                timeout_seconds=15.0,
            )
        )
        useradd_outcome["ok"] = True
    except (
        agent_rpc.AgentOfflineError,
        agent_rpc.AgentRpcTimeoutError,
        agent_rpc.RpcNotYetWiredError,
    ) as exc:
        useradd_outcome.update({"ok": False, "error": str(exc)})

    # 2. PA row (one regardless of useradd outcome — admin retries if needed)
    try:
        pa = await physical_account_service.create(
            session,
            server_id=server.id,
            linux_username=payload.linux_username,
            source="admin_manual_register",
            notes=f"onboarded for user {owner.id} ({owner.username})",
            actor_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except DuplicatePhysicalAccountError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if useradd_outcome.get("ok"):
        pa.uid = useradd_outcome.get("uid")
        pa.gid = useradd_outcome.get("gid")
        pa.home_directory = useradd_outcome.get("home_directory")
        pa.default_shell = useradd_outcome.get("default_shell")
        await session.flush()

    # 3. push key RPC + authorized_key_entry
    push_outcome: dict = {"attempted": True}
    try:
        push_outcome.update(
            await agent_rpc.request_response(
                server_id=server.id,
                frame_type="backend.authorized_key.push",
                payload={
                    "linux_username": payload.linux_username,
                    "public_key": key.public_key,
                    "label": f"corelab:user={owner.id};onboard={pa.id}",
                },
                timeout_seconds=15.0,
            )
        )
        push_outcome["ok"] = True
    except (
        agent_rpc.AgentOfflineError,
        agent_rpc.AgentRpcTimeoutError,
        agent_rpc.RpcNotYetWiredError,
    ) as exc:
        push_outcome.update({"ok": False, "error": str(exc)})

    entry = AuthorizedKeyEntry(
        physical_account_id=pa.id,
        ssh_public_key_id=key.id,
        pushed_by_user_id=current.id,
        pushed_for_user_id=owner.id,
        is_active=1 if push_outcome.get("ok") else 0,
    )
    session.add(entry)
    await session.flush()

    # 4. admin_declared link so the user immediately sees this PA in
    # their account-links list. They can later upgrade to ssh_challenge
    # via /upgrade-via-challenge once they've actually used the key.
    try:
        link = await account_link_service.admin_declare_link(
            session,
            physical_account_id=pa.id,
            owner_user_id=owner.id,
            reason=payload.reason,
            declared_by_user_id=current.id,
            lab_id=current.lab_id,
            server_id=server.id,
            request_ip=ip,
            user_agent=ua,
        )
    except LinkAlreadyActiveError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await audit_service.write(
        session,
        action="physical_account.onboard_user",
        actor_user_id=current.id,
        lab_id=current.lab_id,
        target_type="physical_account",
        target_id=pa.id,
        target_lab_id=current.lab_id,
        target_server_id=server.id,
        payload={
            "linux_username": payload.linux_username,
            "owner_user_id": owner.id,
            "ssh_public_key_id": key.id,
            "reason": payload.reason,
            "useradd_outcome": useradd_outcome,
            "key_push_outcome": push_outcome,
        },
        ip_address=ip,
        user_agent=ua,
    )

    return OnboardUserResponse(
        physical_account_id=pa.id,
        account_link_id=link.id,
        authorized_key_entry_id=entry.id,
        useradd_outcome=useradd_outcome,
        key_push_outcome=push_outcome,
    )


@router.get("/physical-accounts/{pa_id}", response_model=PhysicalAccountRead)
async def get_pa(
    pa_id: int,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PhysicalAccountRead:
    try:
        pa = await physical_account_service.get(session, pa_id, lab_id=current.lab_id)
    except PhysicalAccountNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PhysicalAccountRead.model_validate(pa)


@router.delete("/physical-accounts/{pa_id}", response_model=PhysicalAccountRead)
async def delete_pa(
    pa_id: int,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PhysicalAccountRead:
    ip, ua = extract_request_context(request)
    try:
        pa = await physical_account_service.get(session, pa_id, lab_id=current.lab_id)
    except PhysicalAccountNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await assert_server_admin(session, user=current, server_id=pa.server_id)
    try:
        pa = await physical_account_service.delete(
            session,
            pa_id,
            actor_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except PhysicalAccountNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PhysicalAccountRead.model_validate(pa)


@router.post(
    "/physical-accounts/{pa_id}/authorized-key-entries/{entry_id}/retry-push",
    response_model=AuthorizedKeyRetryResponse,
)
async def retry_authorized_key_push(
    pa_id: int,
    entry_id: int,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AuthorizedKeyRetryResponse:
    ip, ua = extract_request_context(request)
    try:
        pa = await physical_account_service.get(session, pa_id, lab_id=current.lab_id)
    except PhysicalAccountNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await assert_server_admin(session, user=current, server_id=pa.server_id)
    try:
        outcome = await physical_account_service.retry_authorized_key_push(
            session,
            pa_id=pa_id,
            authorized_key_entry_id=entry_id,
            actor_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except AuthorizedKeyEntryNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return AuthorizedKeyRetryResponse(
        physical_account_id=pa.id,
        authorized_key_entry_id=entry_id,
        key_push_outcome=outcome,
    )


@router.post(
    "/physical-accounts/{pa_id}/authorized-key-readback",
    response_model=AuthorizedKeyReadbackResponse,
)
async def read_authorized_keys_from_host(
    pa_id: int,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AuthorizedKeyReadbackResponse:
    ip, ua = extract_request_context(request)
    try:
        pa = await physical_account_service.get(session, pa_id, lab_id=current.lab_id)
    except PhysicalAccountNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await assert_server_admin(session, user=current, server_id=pa.server_id)
    result = await physical_account_service.read_authorized_keys_from_host(
        session,
        pa_id=pa_id,
        actor_user_id=current.id,
        lab_id=current.lab_id,
        request_ip=ip,
        user_agent=ua,
    )
    return AuthorizedKeyReadbackResponse.model_validate(result)


@router.post(
    "/servers/{server_id}/physical-accounts/{pa_id}/declare-owner",
    response_model=AccountLinkRead,
    status_code=status.HTTP_201_CREATED,
)
async def declare_owner(
    server_id: int,
    pa_id: int,
    payload: AdminDeclareOwnerRequest,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AccountLinkRead:
    await assert_server_admin(session, user=current, server_id=server_id)
    ip, ua = extract_request_context(request)
    server = await session.get(Server, server_id)
    _assert_server_in_lab(server, current.lab_id)

    try:
        pa = await physical_account_service.get(session, pa_id, lab_id=current.lab_id)
    except PhysicalAccountNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if pa.server_id != server_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="pa_server_mismatch")

    owner = await session.get(User, payload.owner_user_id)
    if owner is None or owner.lab_id != current.lab_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="owner_user_not_in_lab")

    try:
        link = await account_link_service.admin_declare_link(
            session,
            physical_account_id=pa_id,
            owner_user_id=payload.owner_user_id,
            reason=payload.reason,
            declared_by_user_id=current.id,
            lab_id=current.lab_id,
            server_id=server_id,
            request_ip=ip,
            user_agent=ua,
        )
    except LinkAlreadyActiveError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return AccountLinkRead.model_validate(link)


@router.get(
    "/servers/{server_id}/physical-accounts/{pa_id}/reverse-lookup",
    response_model=ReverseLookupResponse,
)
async def reverse_lookup_via_pa(
    server_id: int,
    pa_id: int,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReverseLookupResponse:
    """Operator-facing reverse lookup (returns linked users for a PA).

    The agent uses a separate endpoint with agent_token auth; this one
    is for the SPA so admins can see who's claimed what.
    """
    try:
        pa = await physical_account_service.get(session, pa_id, lab_id=current.lab_id)
    except PhysicalAccountNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if pa.server_id != server_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="pa_server_mismatch")
    links = await account_link_service.reverse_lookup_users(
        session, server_id=server_id, linux_username=pa.linux_username
    )
    entries = [
        ReverseLookupEntry(
            user_id=link.user_id,
            link_id=link.id,
            source=cast(
                "Literal['ssh_challenge', 'password_pam', 'admin_prepared_then_ssh', 'admin_declared']",
                link.source,
            ),
            is_active=bool(link.is_active),
        )
        for link in links
    ]
    return ReverseLookupResponse(
        physical_account_id=pa.id, linked_users=entries, is_shared=len(entries) > 1
    )
