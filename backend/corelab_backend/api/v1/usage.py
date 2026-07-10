"""``/api/v1/usage/me`` — monthly GPU-hour summary (Phase 7 C4)."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import AuthenticatedUser, get_current_user
from ...db import get_session
from ...services import usage_service

router = APIRouter(prefix="/usage", tags=["usage"])

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@router.get("/me")
async def my_usage(
    month: str = Query(..., description="UTC month, YYYY-MM (e.g. 2026-06)"),
    current: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if not _MONTH_RE.match(month):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_MONTH", "expected": "YYYY-MM"},
        )
    return await usage_service.monthly_usage(session, user_id=current.user.id, month=month)
