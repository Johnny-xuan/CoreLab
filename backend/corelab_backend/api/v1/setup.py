"""``/api/v1/setup/*`` — wizard + activation flow."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import extract_request_context
from ...db import get_session
from ...schemas.setup import (
    ActivateSubmit,
    ActivateValidate,
    SetupInitRequest,
    SetupInitResponse,
    SetupStatus,
)
from ...schemas.user import UserRead
from ...services import auth_service, setup_service
from ...services.auth_service import (
    ActivationProfileConflictError,
    ActivationSshKeyError,
    InvalidActivationTokenError,
)
from ...services.setup_service import AlreadyInitializedError, SetupError

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("/status", response_model=SetupStatus)
async def status_endpoint(session: AsyncSession = Depends(get_session)) -> SetupStatus:
    return SetupStatus(initialized=await setup_service.is_initialized(session))


@router.get("/suggest-slug")
async def suggest_slug(
    name: str = Query(min_length=1, max_length=100),
) -> dict[str, str]:
    """Phase M M-2.2 — derive a safe slug from a free-form lab name.

    Stateless helper for the Setup wizard so the suggested slug shown
    to the user matches the fallback the server would apply if the
    request omitted ``lab_slug``. No auth required (pre-setup endpoint).
    """
    return {"slug": setup_service.derive_slug(name)}


@router.post("/init", response_model=SetupInitResponse, status_code=status.HTTP_201_CREATED)
async def init(
    payload: SetupInitRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SetupInitResponse:
    ip, ua = extract_request_context(request)
    try:
        lab, admin = await setup_service.initialize(session, payload, request_ip=ip, user_agent=ua)
    except AlreadyInitializedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="already_initialized"
        ) from exc
    except SetupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return SetupInitResponse(lab_id=lab.id, admin=UserRead.model_validate(admin))


@router.get("/activate/validate", response_model=ActivateValidate)
async def activate_validate(
    token: str = Query(min_length=8, max_length=128),
    session: AsyncSession = Depends(get_session),
) -> ActivateValidate:
    try:
        return await auth_service.validate_activation_token(session, token)
    except InvalidActivationTokenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/activate", response_model=UserRead)
async def activate(
    payload: ActivateSubmit,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    ip, ua = extract_request_context(request)
    try:
        user = await auth_service.activate(
            session,
            token=payload.token,
            new_password=payload.password,
            username=payload.username,
            email=payload.email,
            display_name=payload.display_name,
            ssh_key_label=payload.ssh_key_label,
            ssh_key_public_key=payload.ssh_key_public_key,
            request_ip=ip,
            user_agent=ua,
        )
    except InvalidActivationTokenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ActivationProfileConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ActivationSshKeyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return UserRead.model_validate(user)
