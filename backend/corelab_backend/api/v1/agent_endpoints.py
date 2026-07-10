"""Internal agent-facing HTTP endpoints (agent_token auth).

Used by the per-server agent process for queries that don't fit a WSS
push (e.g. on-demand reverse lookups when the compliance monitor sees
an unfamiliar process owner). Auth is the agent_token in
``Authorization: Bearer ...`` headers — *not* a user JWT — so the agent
doesn't need to impersonate any platform user.
"""

from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...models import Server
from ...schemas.physical_account import ReverseLookupEntry, ReverseLookupResponse
from ...services import account_link_service
from ...services.telemetry_service import AgentAuthError, authenticate_agent

router = APIRouter(prefix="/agent", tags=["agent"])
_bearer = HTTPBearer(auto_error=False)


async def _authenticate_agent_request(
    server_id: int,
    creds: HTTPAuthorizationCredentials | None,
    session: AsyncSession,
) -> Server:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="agent_token_required")
    try:
        server = await authenticate_agent(
            session, server_id=server_id, plaintext_token=creds.credentials
        )
    except AgentAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    if server.approved_at is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="server_pending_approval")
    return server


@router.get("/reverse-lookup", response_model=ReverseLookupResponse)
async def reverse_lookup(
    server_id: int,
    linux_username: str,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> ReverseLookupResponse:
    await _authenticate_agent_request(server_id, creds, session)
    links = await account_link_service.reverse_lookup_users(
        session, server_id=server_id, linux_username=linux_username
    )
    if not links:
        return ReverseLookupResponse(physical_account_id=None, linked_users=[], is_shared=False)
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
        physical_account_id=links[0].physical_account_id,
        linked_users=entries,
        is_shared=len(entries) > 1,
    )
