"""``/api/v1/users/.../ssh-keys`` — self CRUD + admin read for others."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import (
    AuthenticatedUser,
    extract_request_context,
    get_current_user,
)
from ...db import get_session
from ...schemas.ssh_key import SshKeyCreate, SshKeyDeleteResponse, SshKeyRead
from ...services import ssh_key_service, user_service
from ...services.ssh_key_service import (
    DuplicateKeyError,
    KeyNotFoundError,
    SshKeyError,
)
from ...services.user_service import UserNotFoundError

router = APIRouter(tags=["ssh-keys"])


@router.get("/users/me/ssh-keys", response_model=list[SshKeyRead])
async def list_my_keys(
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SshKeyRead]:
    rows = await ssh_key_service.list_keys(session, owner_user_id=current.id)
    return [SshKeyRead.model_validate(r) for r in rows]


@router.post("/users/me/ssh-keys", response_model=SshKeyRead, status_code=status.HTTP_201_CREATED)
async def add_my_key(
    payload: SshKeyCreate,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SshKeyRead:
    ip, ua = extract_request_context(request)
    try:
        row = await ssh_key_service.add_key(
            session,
            payload,
            owner_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SshKeyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return SshKeyRead.model_validate(row)


@router.delete("/users/me/ssh-keys/{key_id}", response_model=SshKeyDeleteResponse)
async def delete_my_key(
    key_id: int,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SshKeyDeleteResponse:
    ip, ua = extract_request_context(request)
    try:
        row, result = await ssh_key_service.remove_key(
            session,
            key_id,
            owner_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except KeyNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ssh_key_not_found"
        ) from exc
    return SshKeyDeleteResponse(id=row.id, result=result)


@router.get("/users/{user_id}/ssh-keys", response_model=list[SshKeyRead])
async def list_user_keys(
    user_id: int,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SshKeyRead]:
    # Admin-read-for-others; non-admins may only list their own keys.
    if current.role != "lab_admin" and user_id != current.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    try:
        await user_service.get_user(session, user_id, lab_id=current.lab_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found") from exc
    rows = await ssh_key_service.list_keys(session, owner_user_id=user_id)
    return [SshKeyRead.model_validate(r) for r in rows]
