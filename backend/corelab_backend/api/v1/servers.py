"""``/api/v1/servers/*`` — CRUD + admins + capabilities + GPUs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import (
    AuthenticatedUser,
    assert_server_admin,
    extract_request_context,
    get_current_user,
    require_role,
)
from ...db import get_session, get_session_factory
from ...logging_setup import get_logger
from ...models import Lab, Server
from ...schemas.server import (
    CapabilityRead,
    CapabilityUpdate,
    GpuRead,
    RegenerateEnrollmentTokenResponse,
    ServerAdminGrantCreate,
    ServerAdminGrantRead,
    ServerCreate,
    ServerCreateResponse,
    ServerRead,
)
from ...services import (
    agent_hub,
    agent_url_broadcast,
    capability_sync_service,
    lab_url_service,
    link_cache_sync_service,
    policy_sync_service,
    server_service,
)
from ...services.server_service import (
    DangerousCapabilityWithoutNotesError,
    DuplicateHostnameError,
    ServerError,
    ServerNotFoundError,
)

router = APIRouter(prefix="/servers", tags=["servers"])
_log = get_logger("corelab.servers")


@router.get("", response_model=list[ServerRead])
async def list_servers(
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ServerRead]:
    servers = await server_service.list_servers(session, lab_id=current.lab_id)
    return [ServerRead.model_validate(s) for s in servers]


@router.post("", response_model=ServerCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_server(
    payload: ServerCreate,
    request: Request,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> ServerCreateResponse:
    ip, ua = extract_request_context(request)
    try:
        server, plaintext, expires_at, snippet = await server_service.create_server(
            session,
            hostname=payload.hostname,
            display_name=payload.display_name,
            max_reservation_hours=payload.max_reservation_hours,
            expected_hostname_pattern=payload.expected_hostname_pattern,
            lab_id=current.lab_id,
            actor_user_id=current.id,
            request_ip=ip,
            user_agent=ua,
        )
    except DuplicateHostnameError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return ServerCreateResponse(
        server=ServerRead.model_validate(server),
        enrollment_token=plaintext,
        install_snippet=snippet,
        expires_at=expires_at,
    )


@router.get("/{server_id}", response_model=ServerRead)
async def get_server(
    server_id: int,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ServerRead:
    try:
        server = await server_service.get_server(session, server_id, lab_id=current.lab_id)
    except ServerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="server_not_found"
        ) from exc
    return ServerRead.model_validate(server)


@router.post(
    "/{server_id}/regenerate-enrollment-token",
    response_model=RegenerateEnrollmentTokenResponse,
)
async def regenerate_enrollment_token(
    server_id: int,
    request: Request,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> RegenerateEnrollmentTokenResponse:
    ip, ua = extract_request_context(request)
    try:
        plaintext, expires_at, snippet, revoked = await server_service.regenerate_enrollment_token(
            session,
            server_id=server_id,
            lab_id=current.lab_id,
            actor_user_id=current.id,
            request_ip=ip,
            user_agent=ua,
        )
    except ServerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="server_not_found"
        ) from exc
    except ServerError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return RegenerateEnrollmentTokenResponse(
        enrollment_token=plaintext,
        install_snippet=snippet,
        expires_at=expires_at,
        revoked_token_ids=revoked,
    )


@router.delete("/{server_id}", response_model=ServerRead)
async def delete_server(
    server_id: int,
    request: Request,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> ServerRead:
    ip, ua = extract_request_context(request)
    try:
        server = await server_service.delete_server(
            session,
            server_id,
            lab_id=current.lab_id,
            actor_user_id=current.id,
            request_ip=ip,
            user_agent=ua,
        )
    except ServerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="server_not_found"
        ) from exc
    return ServerRead.model_validate(server)


@router.post("/{server_id}/approve", response_model=ServerRead)
async def approve_server(
    server_id: int,
    request: Request,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> ServerRead:
    ip, ua = extract_request_context(request)
    try:
        server = await server_service.approve_server(
            session,
            server_id,
            lab_id=current.lab_id,
            actor_user_id=current.id,
            is_connected=agent_hub.pool.is_online(server_id),
            request_ip=ip,
            user_agent=ua,
        )
    except ServerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="server_not_found"
        ) from exc
    except ServerError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    result = ServerRead.model_validate(server)
    await session.commit()
    await _push_agent_context_after_approval(server_id=server_id)
    return result


@router.get("/{server_id}/gpus", response_model=list[GpuRead])
async def list_gpus(
    server_id: int,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[GpuRead]:
    try:
        await server_service.get_server(session, server_id, lab_id=current.lab_id)
    except ServerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="server_not_found"
        ) from exc
    gpus = await server_service.list_gpus(session, server_id=server_id)
    return [GpuRead.model_validate(g) for g in gpus]


@router.get("/{server_id}/admins", response_model=list[ServerAdminGrantRead])
async def list_admins(
    server_id: int,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ServerAdminGrantRead]:
    try:
        await server_service.get_server(session, server_id, lab_id=current.lab_id)
    except ServerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="server_not_found"
        ) from exc
    grants = await server_service.list_admins(session, server_id=server_id)
    return [ServerAdminGrantRead.model_validate(g) for g in grants]


@router.post(
    "/{server_id}/admins",
    response_model=ServerAdminGrantRead,
    status_code=status.HTTP_201_CREATED,
)
async def grant_admin(
    server_id: int,
    payload: ServerAdminGrantCreate,
    request: Request,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> ServerAdminGrantRead:
    ip, ua = extract_request_context(request)
    try:
        await server_service.get_server(session, server_id, lab_id=current.lab_id)
    except ServerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="server_not_found"
        ) from exc
    try:
        grant = await server_service.grant_admin(
            session,
            server_id=server_id,
            user_id=payload.user_id,
            notes=payload.notes,
            actor_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except ServerError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ServerAdminGrantRead.model_validate(grant)


@router.delete("/{server_id}/admins/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_admin(
    server_id: int,
    user_id: int,
    request: Request,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> None:
    ip, ua = extract_request_context(request)
    try:
        await server_service.get_server(session, server_id, lab_id=current.lab_id)
    except ServerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="server_not_found"
        ) from exc
    grant = await server_service.revoke_admin(
        session,
        server_id=server_id,
        user_id=user_id,
        actor_user_id=current.id,
        lab_id=current.lab_id,
        request_ip=ip,
        user_agent=ua,
    )
    if grant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="grant_not_found")


@router.get("/{server_id}/capabilities", response_model=list[CapabilityRead])
async def list_capabilities(
    server_id: int,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CapabilityRead]:
    try:
        await server_service.get_server(session, server_id, lab_id=current.lab_id)
    except ServerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="server_not_found"
        ) from exc
    caps = await server_service.list_capabilities(session, server_id=server_id)
    return [CapabilityRead.model_validate(c) for c in caps]


@router.patch("/{server_id}/capabilities/{capability_key}", response_model=CapabilityRead)
async def update_capability(
    server_id: int,
    capability_key: str,
    payload: CapabilityUpdate,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CapabilityRead:
    await assert_server_admin(session, user=current, server_id=server_id)
    ip, ua = extract_request_context(request)
    try:
        await server_service.get_server(session, server_id, lab_id=current.lab_id)
    except ServerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="server_not_found"
        ) from exc
    try:
        cap = await server_service.update_capability(
            session,
            server_id=server_id,
            capability_key=capability_key,
            enabled=payload.enabled,
            notes=payload.notes,
            actor_user_id=current.id,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except DangerousCapabilityWithoutNotesError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except ServerError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    result = CapabilityRead.model_validate(cap)
    # Commit the flip, then push the new capability state to the live
    # agent so its gate updates without waiting for a reconnect.
    await session.commit()
    await capability_sync_service.push_to_server(session, server_id=server_id)
    return result


async def _push_agent_context_after_approval(*, server_id: int) -> None:
    if not agent_hub.pool.is_online(server_id):
        return

    factory = get_session_factory()
    urls: list[str] = []
    try:
        async with factory() as session:
            server = await session.get(Server, server_id)
            lab = await session.get(Lab, server.lab_id) if server is not None else None
            urls = lab_url_service.agent_urls_only(lab) if lab is not None else []
    except Exception as exc:
        _log.warning("server.approve_url_context_failed", server_id=server_id, error=str(exc))

    if urls:
        try:
            await agent_url_broadcast.push_update_urls(server_id=server_id, urls=urls)
        except Exception as exc:
            _log.warning("server.approve_url_push_failed", server_id=server_id, error=str(exc))

    for label, sender in (
        ("capability", capability_sync_service.send_on_connect),
        ("policy", policy_sync_service.send_on_connect),
        ("link_cache", link_cache_sync_service.send_on_connect),
    ):
        try:
            async with factory() as session:
                await sender(session, server_id=server_id)
        except Exception as exc:
            _log.warning(
                "server.approve_agent_context_push_failed",
                server_id=server_id,
                context=label,
                error=str(exc),
            )
