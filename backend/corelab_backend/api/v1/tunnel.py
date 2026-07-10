"""``/api/v1/admin/tunnel`` — Phase M v5 (post-review) read-only tunnel status.

The original M-5.4 version of this router had POST endpoints to
toggle ``lab.tunnel_mode``. Review removed those: enabling, disabling,
and upgrading the tunnel is a CLI / host-side action (touches
docker-compose state and can change how the admin console itself is
reachable), so the web UI now only displays the current state and
points the operator at the right shell command. See ``AdminDomain.vue``
for the tutorial surface.

This leaves only a single endpoint:

* GET /admin/tunnel/status — current tunnel_mode + whether a Named
  Tunnel token is stored. Used by the Domain page to decide which
  "what to do next" block to render.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import AuthenticatedUser, require_role
from ...db import get_session
from ...models import Lab

router = APIRouter(prefix="/admin/tunnel", tags=["admin-tunnel"])


class TunnelStatusResponse(BaseModel):
    tunnel_mode: Literal["none", "cloudflare_quick", "cloudflare_named"]
    tunnel_token_set: bool


async def _get_lab(session: AsyncSession, lab_id: int) -> Lab:
    lab = await session.get(Lab, lab_id)
    if lab is None:
        raise HTTPException(status_code=404, detail="lab not found")
    return lab


@router.get("/status", response_model=TunnelStatusResponse)
async def tunnel_status(
    session: AsyncSession = Depends(get_session),
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
) -> TunnelStatusResponse:
    lab = await _get_lab(session, current.lab_id)
    return TunnelStatusResponse(
        tunnel_mode=lab.tunnel_mode,
        tunnel_token_set=bool(lab.tunnel_token),
    )
