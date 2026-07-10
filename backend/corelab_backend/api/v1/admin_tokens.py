"""``/api/v1/admin/enrollment-tokens`` — lab_admin lifecycle view.

Read-only listing of every enrollment token issued for the caller's
lab, with a derived ``status`` (unused / used / expired) so the UI can
filter without re-computing on the client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import AuthenticatedUser, require_role
from ...db import get_session
from ...schemas.server import EnrollmentTokenAdminItem
from ...services import server_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/enrollment-tokens", response_model=list[EnrollmentTokenAdminItem])
async def list_enrollment_tokens(
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
    session: AsyncSession = Depends(get_session),
) -> list[EnrollmentTokenAdminItem]:
    tokens = await server_service.list_enrollment_tokens(session, lab_id=current.lab_id)
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    items: list[EnrollmentTokenAdminItem] = []
    for t in tokens:
        # asyncmy returns DATETIME columns as naive — compare in the
        # same tz-naive frame to avoid TypeError.
        expires_naive = t.expires_at.replace(tzinfo=None) if t.expires_at.tzinfo else t.expires_at
        if t.used_at is not None:
            tstatus: Literal["unused", "used", "expired"] = "used"
        elif expires_naive <= now_naive:
            tstatus = "expired"
        else:
            tstatus = "unused"
        items.append(
            EnrollmentTokenAdminItem(
                id=t.id,
                lab_id=t.lab_id,
                expected_hostname_pattern=t.expected_hostname_pattern,
                expires_at=t.expires_at,
                used_at=t.used_at,
                used_by_server_id=t.used_by_server_id,
                created_at=t.created_at,
                created_by_user_id=t.created_by_user_id,
                status=tstatus,
            )
        )
    return items
