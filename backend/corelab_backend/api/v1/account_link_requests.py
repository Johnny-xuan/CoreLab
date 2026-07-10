"""``/api/v1/account-link-requests/*`` + ``/users/me/account-link-requests``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import (
    AuthenticatedUser,
    assert_server_admin,
    extract_request_context,
    get_current_user,
    list_granted_server_ids,
)
from ...db import get_session
from ...schemas.account_link import (
    AccountLinkRequestCreate,
    AccountLinkRequestDecision,
    AccountLinkRequestRead,
    AccountLinkRequestRetryPushResponse,
    RequestContext,
)
from ...services import account_link_request_service
from ...services.account_link_request_service import (
    AccountLinkRequestError,
    AlreadyLinkedError,
    DuplicatePendingError,
    InvalidStatusTransitionError,
    NoActiveSshKeyError,
    PhysicalAccountNotFoundError,
    RequestNotFoundError,
)

router = APIRouter(prefix="/account-link-requests", tags=["account-link-requests"])


async def _filter_rows_for_current_admin(
    session: AsyncSession,
    *,
    current: AuthenticatedUser,
    rows: list,
) -> list:
    if current.role == "lab_admin":
        return rows
    granted = set(await list_granted_server_ids(session, user=current))
    if not granted:
        return []
    from sqlalchemy import select as _sel

    from ...models import PhysicalAccount

    pa_ids = [r.physical_account_id for r in rows]
    if not pa_ids:
        return []
    pa_rows = (
        (await session.execute(_sel(PhysicalAccount).where(PhysicalAccount.id.in_(pa_ids))))
        .scalars()
        .all()
    )
    pa_server = {p.id: p.server_id for p in pa_rows}
    return [r for r in rows if pa_server.get(r.physical_account_id) in granted]


@router.post("", response_model=AccountLinkRequestRead, status_code=status.HTTP_201_CREATED)
async def create_request(
    payload: AccountLinkRequestCreate,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AccountLinkRequestRead:
    ip, ua = extract_request_context(request)
    try:
        row = await account_link_request_service.create_request(
            session,
            requester_user_id=current.id,
            physical_account_id=payload.physical_account_id,
            request_note=payload.request_note,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except PhysicalAccountNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AlreadyLinkedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DuplicatePendingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return AccountLinkRequestRead.model_validate(row)


@router.get("", response_model=list[AccountLinkRequestRead])
async def list_pending(
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AccountLinkRequestRead]:
    """Phase K — server admin sees pending across servers they manage;
    lab_admin sees all pending in the lab. Non-admins get an empty list
    (their own requests are reachable via /users/me/account-link-requests).
    """
    rows = await account_link_request_service.list_pending_in_lab(session, lab_id=current.lab_id)
    rows = await _filter_rows_for_current_admin(session, current=current, rows=list(rows))
    return [AccountLinkRequestRead.model_validate(r) for r in rows]


@router.get("/needs-push", response_model=list[AccountLinkRequestRead])
async def list_needs_push(
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AccountLinkRequestRead]:
    rows = await account_link_request_service.list_needing_key_push_in_lab(
        session, lab_id=current.lab_id
    )
    rows = await _filter_rows_for_current_admin(session, current=current, rows=list(rows))
    return [AccountLinkRequestRead.model_validate(r) for r in rows]


@router.get("/{request_id}/context", response_model=RequestContext)
async def request_context(
    request_id: int,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RequestContext:
    """Signal bundle for the admin review card (Phase K K-5).
    Server admin can read context for requests on their granted servers."""
    try:
        ctx = await account_link_request_service.build_request_context(
            session, request_id=request_id, lab_id=current.lab_id
        )
    except RequestNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await assert_server_admin(session, user=current, server_id=ctx["server_id"])
    return RequestContext.model_validate(ctx)


@router.post("/{request_id}/approve", response_model=AccountLinkRequestRead)
async def approve(
    request_id: int,
    payload: AccountLinkRequestDecision,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AccountLinkRequestRead:
    ip, ua = extract_request_context(request)
    # Look up server first so the server-admin gate can run.
    try:
        ctx = await account_link_request_service.build_request_context(
            session, request_id=request_id, lab_id=current.lab_id
        )
    except RequestNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await assert_server_admin(session, user=current, server_id=ctx["server_id"])
    try:
        row = await account_link_request_service.approve_request(
            session,
            request_id=request_id,
            decision_note=payload.decision_note,
            admin_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except RequestNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidStatusTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except NoActiveSshKeyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return AccountLinkRequestRead.model_validate(row)


@router.post("/{request_id}/retry-push", response_model=AccountLinkRequestRetryPushResponse)
async def retry_push(
    request_id: int,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AccountLinkRequestRetryPushResponse:
    ip, ua = extract_request_context(request)
    try:
        ctx = await account_link_request_service.build_request_context(
            session, request_id=request_id, lab_id=current.lab_id
        )
    except RequestNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await assert_server_admin(session, user=current, server_id=ctx["server_id"])
    try:
        row, outcome = await account_link_request_service.retry_push_for_request(
            session,
            request_id=request_id,
            admin_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except RequestNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidStatusTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except NoActiveSshKeyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return AccountLinkRequestRetryPushResponse(
        request=AccountLinkRequestRead.model_validate(row),
        key_push_outcome=outcome,
    )


@router.post("/{request_id}/deny", response_model=AccountLinkRequestRead)
async def deny(
    request_id: int,
    payload: AccountLinkRequestDecision,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AccountLinkRequestRead:
    ip, ua = extract_request_context(request)
    try:
        ctx = await account_link_request_service.build_request_context(
            session, request_id=request_id, lab_id=current.lab_id
        )
    except RequestNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await assert_server_admin(session, user=current, server_id=ctx["server_id"])
    try:
        row = await account_link_request_service.deny_request(
            session,
            request_id=request_id,
            decision_note=payload.decision_note,
            admin_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except RequestNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidStatusTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return AccountLinkRequestRead.model_validate(row)


@router.post("/{request_id}/withdraw", response_model=AccountLinkRequestRead)
async def withdraw(
    request_id: int,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AccountLinkRequestRead:
    ip, ua = extract_request_context(request)
    try:
        row = await account_link_request_service.withdraw_request(
            session,
            request_id=request_id,
            requester_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except RequestNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidStatusTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AccountLinkRequestError as exc:
        # not_requester (403) lands here.
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return AccountLinkRequestRead.model_validate(row)
