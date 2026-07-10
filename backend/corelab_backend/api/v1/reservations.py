"""``/api/v1/reservations/*`` + ``/reservation-groups/{group_id}`` + per-PA shims.

Phase 5 C3 — the REST surface that wraps ``reservation_service``. The
service layer raises typed exceptions; this module is responsible for
mapping them to HTTP status codes per docs/05-api-design.md §3.12:

- 409 RESERVATION_OVERLAP / MEMORY_EXCEEDED / MIX_EXCLUSIVE_SHARED /
  COMPUTE_EXCEEDED (Q3)
- 422 RESERVATION_TOO_LONG / INVALID_TIME / LINK_NOT_VERIFIED /
  NO_ACTIVE_LINK / SCRIPT_TOO_LARGE
- 403 cancel-not-permitted (caller is neither owner nor an admin
  with server jurisdiction)
- 404 reservation-not-found

The two route shims in §2.17 — ``GET /me/accounts/{pa_id}/reservations``
and ``POST /me/accounts/{pa_id}/reservations`` — live in this file so
the audit trail / RBAC / docstring stay one click away.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import AuthenticatedUser, extract_request_context, get_current_user
from ...db import get_session
from ...models import AccountLink, Reservation, ServerAdminGrant
from ...schemas.reservation import (
    CancelRequest,
    ConflictRead,
    ModifyRequest,
    PreviewRequest,
    PreviewResponse,
    RecommendCandidate,
    RecommendRequest,
    RecommendResponse,
    ReservationCreateRequest,
    ReservationCreateResponse,
    ReservationRead,
    ReservationScriptLogRead,
    TimeLimitCheck,
)
from ...services import reservation_service, schedule_recommender, script_lifecycle_service

router = APIRouter(tags=["reservations"])


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _conflict_detail(code: str, **extra: object) -> dict[str, object]:
    """Build the body of a 409 detail dict (kept stable for the frontend)."""
    body: dict[str, object] = {"code": code}
    body.update(extra)
    return body


async def _user_can_admin_server(
    session: AsyncSession, *, user: AuthenticatedUser, server_id: int
) -> bool:
    """True if user is lab_admin in the right lab, or has an active server_admin_grant."""
    if user.role == "lab_admin":
        return True
    result = await session.execute(
        select(ServerAdminGrant).where(
            ServerAdminGrant.user_id == user.id,
            ServerAdminGrant.server_id == server_id,
            ServerAdminGrant.is_active == 1,
        )
    )
    return result.scalar_one_or_none() is not None


def _map_service_error(exc: reservation_service.ReservationError) -> HTTPException:
    """Translate typed reservation-service errors to FastAPI HTTPException."""
    if isinstance(exc, reservation_service.ReservationOverlapError):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail=_conflict_detail(
                "RESERVATION_OVERLAP",
                conflicting_reservation_ids=exc.conflicting_reservation_ids,
            ),
        )
    if isinstance(exc, reservation_service.ReservationMixExclusiveSharedError):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail=_conflict_detail(
                "RESERVATION_MIX_EXCLUSIVE_SHARED",
                conflicting_reservation_ids=exc.conflicting_reservation_ids,
            ),
        )
    if isinstance(exc, reservation_service.ReservationMemoryExceededError):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail=_conflict_detail(
                "RESERVATION_MEMORY_EXCEEDED",
                conflicting_reservation_ids=exc.conflicting_reservation_ids,
                memory={
                    "used_mb": exc.used_mb,
                    "would_use_mb": exc.would_use_mb,
                    "total_mb": exc.total_mb,
                    "exceeds_by_mb": exc.exceeds_by_mb,
                },
            ),
        )
    if isinstance(exc, reservation_service.ReservationComputeExceededError):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail=_conflict_detail(
                "RESERVATION_COMPUTE_EXCEEDED",
                conflicting_reservation_ids=exc.conflicting_reservation_ids,
                compute={
                    "used_pct": exc.used_pct,
                    "would_use_pct": exc.would_use_pct,
                    "exceeds_by_pct": exc.exceeds_by_pct,
                },
            ),
        )
    if isinstance(exc, reservation_service.ReservationTooLongError):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_conflict_detail(
                "RESERVATION_TOO_LONG",
                max_hours=exc.max_hours,
                requested_hours=exc.requested_hours,
            ),
        )
    if isinstance(exc, reservation_service.InvalidTimeError):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_conflict_detail("INVALID_TIME", message=str(exc)),
        )
    if isinstance(exc, reservation_service.LinkNotVerifiedError):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_conflict_detail("LINK_NOT_VERIFIED", message=str(exc)),
        )
    if isinstance(exc, reservation_service.NoActiveLinkError):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_conflict_detail("NO_ACTIVE_LINK", message=str(exc)),
        )
    if isinstance(exc, reservation_service.ScriptTooLargeError):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_conflict_detail("SCRIPT_TOO_LARGE"),
        )
    if isinstance(exc, reservation_service.GroupCancelRunningScriptError):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail=_conflict_detail(
                "GROUP_CANCEL_RUNNING_SCRIPT_UNSUPPORTED",
                message=str(exc),
                running_reservation_ids=exc.running_reservation_ids,
            ),
        )
    if isinstance(exc, reservation_service.CancelNotPermittedError):
        return HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=_conflict_detail("CANCEL_NOT_PERMITTED", message=str(exc)),
        )
    if isinstance(exc, reservation_service.ReservationNotFoundError):
        return HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=_conflict_detail("RESERVATION_NOT_FOUND", message=str(exc)),
        )
    return HTTPException(
        status.HTTP_400_BAD_REQUEST,
        detail=_conflict_detail("RESERVATION_ERROR", message=str(exc)),
    )


def _draft_from(item: object) -> reservation_service.ItemDraft:
    """Build an ItemDraft from a pydantic input row; supports both schemas."""
    return reservation_service.ItemDraft(
        server_id=item.server_id,  # type: ignore[attr-defined]
        gpu_id=item.gpu_id,  # type: ignore[attr-defined]
        start_at=item.start_at,  # type: ignore[attr-defined]
        end_at=item.end_at,  # type: ignore[attr-defined]
        account_link_id=getattr(item, "account_link_id", 0),
        gpu_memory_mb=item.gpu_memory_mb,  # type: ignore[attr-defined]
        gpu_compute_share_pct=item.gpu_compute_share_pct,  # type: ignore[attr-defined]
    )


@router.post(
    "/reservations",
    response_model=ReservationCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_reservations(
    payload: ReservationCreateRequest,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReservationCreateResponse:
    ip, ua = extract_request_context(request)
    drafts = [_draft_from(it) for it in payload.items]
    try:
        group_id, rows = await reservation_service.create_reservation_batch(
            session,
            items=drafts,
            user_id=current.id,
            lab_id=current.lab_id,
            script=payload.script,
            script_scheduled_start_at=payload.script_scheduled_start_at,
            script_max_runtime_seconds=payload.script_max_runtime_seconds,
            share_script=payload.share_script,
            now=_utc_now(),
            request_ip=ip,
            user_agent=ua,
        )
    except reservation_service.ReservationError as exc:
        raise _map_service_error(exc) from exc

    return ReservationCreateResponse(
        group_id=group_id,
        reservations=[ReservationRead.model_validate(r) for r in rows],
    )


@router.post("/reservations/recommend", response_model=RecommendResponse)
async def recommend_reservation(
    payload: RecommendRequest,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RecommendResponse:
    """Phase J — return the earliest matching reservation window(s).

    Stateless: no row is created, no slot is reserved. The user picks a
    candidate and POSTs ``/reservations`` (or the PA shim) to confirm.
    If somebody else booked the window in the meantime, that POST will
    409 and the UI re-asks for recommendations.
    """
    try:
        candidates = await schedule_recommender.recommend(
            session,
            lab_id=current.lab_id,
            gpu_count=payload.gpu_count,
            time_limit_seconds=payload.time_limit_seconds,
            after=payload.after,
            top_k=payload.top_k,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return RecommendResponse(
        candidates=[
            RecommendCandidate(
                server_id=c.server_id,
                gpu_ids=c.gpu_ids,
                start_at=c.start_at,
                end_at=c.end_at,
            )
            for c in candidates
        ]
    )


@router.post("/reservations/preview-conflicts", response_model=PreviewResponse)
async def preview_conflicts(
    payload: PreviewRequest,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PreviewResponse:
    drafts = [
        reservation_service.ItemDraft(
            server_id=it.server_id,
            gpu_id=it.gpu_id,
            start_at=it.start_at,
            end_at=it.end_at,
            account_link_id=payload.account_link_id,
            gpu_memory_mb=it.gpu_memory_mb,
            gpu_compute_share_pct=it.gpu_compute_share_pct,
        )
        for it in payload.items
    ]
    result = await reservation_service.preview_conflicts(
        session,
        items=drafts,
        user_id=current.id,
        now=_utc_now(),
    )
    return PreviewResponse(
        conflicts=[
            ConflictRead(
                input_index=c.input_index,
                type=c.type,
                conflicting_reservation_ids=c.conflicting_reservation_ids,
                memory=c.memory,
                compute=c.compute,
                time=c.time,
            )
            for c in result.conflicts
        ],
        time_limit_checks=[TimeLimitCheck(**tlc) for tlc in result.time_limit_checks],
    )


@router.get("/reservations", response_model=list[ReservationRead])
async def list_reservations(
    server_id: int | None = Query(default=None),
    gpu_id: int | None = Query(default=None),
    user_id: int | None = Query(default=None),
    starts_after: datetime | None = Query(default=None),
    ends_before: datetime | None = Query(default=None),
    status_in: list[str] | None = Query(default=None),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ReservationRead]:
    del current  # any-logged-in (lab-scope is server-bound at the query side)
    rows = await reservation_service.list_reservations(
        session,
        server_id=server_id,
        gpu_id=gpu_id,
        user_id=user_id,
        starts_after=starts_after,
        ends_before=ends_before,
        statuses=status_in,
    )
    return [ReservationRead.model_validate(r) for r in rows]


@router.get("/reservations/{reservation_id}", response_model=ReservationRead)
async def get_reservation(
    reservation_id: int,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReservationRead:
    row = await session.get(Reservation, reservation_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="reservation_not_found")
    del current
    return ReservationRead.model_validate(row)


async def _ensure_can_read_script_log(
    session: AsyncSession, *, row: Reservation, user: AuthenticatedUser
) -> None:
    if row.user_id == user.id:
        return
    if await _user_can_admin_server(session, user=user, server_id=row.server_id):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail=_conflict_detail("SCRIPT_LOG_FORBIDDEN"),
    )


@router.get("/reservations/{reservation_id}/script-log", response_model=ReservationScriptLogRead)
async def get_reservation_script_log(
    reservation_id: int,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReservationScriptLogRead:
    row = await session.get(Reservation, reservation_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="reservation_not_found")
    await _ensure_can_read_script_log(session, row=row, user=current)
    return ReservationScriptLogRead(
        reservation_id=row.id,
        text=row.script_log_tail_text or "",
        truncated=bool(row.script_log_tail_truncated),
        log_path=row.script_log_path,
        output_size_bytes=row.script_output_size_bytes,
        script_status=row.script_status,
        script_started_at=row.script_started_at,
        script_finished_at=row.script_finished_at,
    )


@router.patch("/reservations/{reservation_id}", response_model=ReservationRead)
async def modify_reservation(
    reservation_id: int,
    payload: ModifyRequest,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReservationRead:
    ip, ua = extract_request_context(request)
    try:
        row = await reservation_service.modify_reservation(
            session,
            reservation_id=reservation_id,
            actor_user_id=current.id,
            new_start_at=payload.start_at,
            new_end_at=payload.end_at,
            new_script=payload.script,
            new_script_scheduled_start_at=payload.script_scheduled_start_at,
            new_script_max_runtime_seconds=payload.script_max_runtime_seconds,
            lab_id=current.lab_id,
            now=_utc_now(),
            request_ip=ip,
            user_agent=ua,
        )
    except reservation_service.ReservationError as exc:
        raise _map_service_error(exc) from exc
    return ReservationRead.model_validate(row)


@router.delete("/reservations/{reservation_id}", response_model=ReservationRead)
async def cancel_reservation(
    reservation_id: int,
    payload: CancelRequest,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReservationRead:
    ip, ua = extract_request_context(request)
    row = await session.get(Reservation, reservation_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="reservation_not_found")
    actor_can_admin = await _user_can_admin_server(session, user=current, server_id=row.server_id)

    # Phase 6 SP-5 — if the script is still running we must SIGTERM it
    # on the agent before transitioning the reservation. The lifecycle
    # path keeps things atomic: RPC + wait for script.finished{killed} +
    # then cancel_reservation. Agent unreachable or lifecycle timeout
    # leaves the row active (P6-14) and surfaces 5xx so the user knows
    # to retry rather than assume cancel succeeded.
    if (
        row.status == reservation_service.STATUS_ACTIVE
        and row.script_status == reservation_service.SCRIPT_RUNNING
    ):
        agent_reason = (
            "admin_cancel" if (actor_can_admin and row.user_id != current.id) else "user_cancel"
        )
        try:
            row = await script_lifecycle_service.cancel_active_with_running_script(
                session,
                reservation=row,
                actor_user_id=current.id,
                actor_can_admin=actor_can_admin,
                reason=payload.reason,
                lab_id=current.lab_id,
                cancel_reason_for_agent=agent_reason,
                request_ip=ip,
                user_agent=ua,
            )
        except script_lifecycle_service.AgentUnreachableDuringCancelError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "AGENT_UNREACHABLE_DURING_CANCEL",
                    "message": (
                        "Could not reach the agent to stop the running script; "
                        "reservation is still active. Try again once the agent is back online."
                    ),
                    "error": str(exc),
                },
            ) from exc
        except script_lifecycle_service.LifecycleEventTimeoutError as exc:
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                detail={
                    "code": "SCRIPT_LIFECYCLE_TIMEOUT",
                    "message": (
                        "Agent acknowledged the cancel but did not confirm the "
                        "script terminated in time; reservation is still active."
                    ),
                    "error": str(exc),
                },
            ) from exc
        except reservation_service.ReservationError as exc:
            raise _map_service_error(exc) from exc
        return ReservationRead.model_validate(row)

    try:
        row = await reservation_service.cancel_reservation(
            session,
            reservation_id=reservation_id,
            actor_user_id=current.id,
            actor_can_admin=actor_can_admin,
            reason=payload.reason,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except reservation_service.ReservationError as exc:
        raise _map_service_error(exc) from exc
    return ReservationRead.model_validate(row)


@router.delete("/reservation-groups/{group_id}", response_model=list[ReservationRead])
async def cancel_group(
    group_id: str,
    payload: CancelRequest,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ReservationRead]:
    ip, ua = extract_request_context(request)
    # Look at one row to figure out admin jurisdiction (group is single-server).
    sample = (
        await session.execute(select(Reservation).where(Reservation.group_id == group_id).limit(1))
    ).scalar_one_or_none()
    actor_can_admin = False
    if sample is not None:
        actor_can_admin = await _user_can_admin_server(
            session, user=current, server_id=sample.server_id
        )
    try:
        rows = await reservation_service.cancel_group(
            session,
            group_id=group_id,
            actor_user_id=current.id,
            actor_can_admin=actor_can_admin,
            reason=payload.reason,
            lab_id=current.lab_id,
            request_ip=ip,
            user_agent=ua,
        )
    except reservation_service.ReservationError as exc:
        raise _map_service_error(exc) from exc
    return [ReservationRead.model_validate(r) for r in rows]


@router.get("/users/me/reservations", response_model=list[ReservationRead])
async def list_my_reservations(
    status_in: list[str] | None = Query(default=None),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ReservationRead]:
    rows = await reservation_service.list_reservations(
        session, user_id=current.id, statuses=status_in
    )
    return [ReservationRead.model_validate(r) for r in rows]


# §2.17 per-PA shims — auto-inject account_link_id from the route context.


async def _resolve_link_owned_by(session: AsyncSession, pa_id: int, user_id: int) -> AccountLink:
    result = await session.execute(
        select(AccountLink).where(
            AccountLink.physical_account_id == pa_id,
            AccountLink.user_id == user_id,
            AccountLink.is_active == 1,
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no_active_link_for_pa")
    return link


@router.get("/me/accounts/{pa_id}/reservations", response_model=list[ReservationRead])
async def list_reservations_for_pa(
    pa_id: int,
    status_in: list[str] | None = Query(default=None),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ReservationRead]:
    link = await _resolve_link_owned_by(session, pa_id, current.id)
    rows = await reservation_service.list_for_link(
        session, account_link_id=link.id, statuses=status_in
    )
    return [ReservationRead.model_validate(r) for r in rows]


@router.post(
    "/me/accounts/{pa_id}/reservations",
    response_model=ReservationCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_reservations_for_pa(
    pa_id: int,
    payload: ReservationCreateRequest,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReservationCreateResponse:
    """Per-PA shim — every item's ``account_link_id`` is forced to this PA's
    active link, regardless of what was supplied (matches §2.17 contract)."""
    link = await _resolve_link_owned_by(session, pa_id, current.id)
    ip, ua = extract_request_context(request)
    drafts = [
        reservation_service.ItemDraft(
            server_id=it.server_id,
            gpu_id=it.gpu_id,
            start_at=it.start_at,
            end_at=it.end_at,
            account_link_id=link.id,
            gpu_memory_mb=it.gpu_memory_mb,
            gpu_compute_share_pct=it.gpu_compute_share_pct,
        )
        for it in payload.items
    ]
    try:
        group_id, rows = await reservation_service.create_reservation_batch(
            session,
            items=drafts,
            user_id=current.id,
            lab_id=current.lab_id,
            script=payload.script,
            script_scheduled_start_at=payload.script_scheduled_start_at,
            script_max_runtime_seconds=payload.script_max_runtime_seconds,
            share_script=payload.share_script,
            now=_utc_now(),
            request_ip=ip,
            user_agent=ua,
        )
    except reservation_service.ReservationError as exc:
        raise _map_service_error(exc) from exc
    return ReservationCreateResponse(
        group_id=group_id,
        reservations=[ReservationRead.model_validate(r) for r in rows],
    )
