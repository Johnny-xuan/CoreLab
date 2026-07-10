"""``/api/v1/auth/*`` — login, logout, /me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import (
    AuthenticatedUser,
    extract_request_context,
    get_current_user,
)
from ...db import get_session
from ...schemas.auth import LoginRequest, LoginResponse, LogoutResponse
from ...schemas.user import UserRead
from ...services import auth_service
from ...services.auth_service import (
    AccountDisabledError,
    AccountLockedError,
    InvalidCredentialsError,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    ip, ua = extract_request_context(request)
    try:
        return await auth_service.authenticate(session, payload, request_ip=ip, user_agent=ua)
    except AccountLockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "ACCOUNT_LOCKED", "reason": "too_many_failed_attempts"},
            headers={"Retry-After": "300"},
        ) from exc
    except AccountDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="account_disabled"
        ) from exc
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials"
        ) from exc


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LogoutResponse:
    ip, ua = extract_request_context(request)
    await auth_service.record_logout(
        session,
        actor_user_id=current.id,
        lab_id=current.lab_id,
        request_ip=ip,
        user_agent=ua,
    )
    return LogoutResponse()


@router.get("/me", response_model=UserRead)
async def me(current: AuthenticatedUser = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(current.user)
