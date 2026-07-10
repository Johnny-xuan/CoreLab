"""``/api/v1/admin/public-urls`` — Phase M v5 multi-URL admin surface.

Powers the Public Access card on Lab Overview. lab_admin only.

Endpoints:

* GET    /admin/public-urls            — list URLs + reachability + tunnel_mode
* POST   /admin/public-urls            — add a custom_domain (or other) URL
* DELETE /admin/public-urls            — remove a URL by exact match
* POST   /admin/public-urls/probe-now  — re-run reachability probe on demand
* POST   /admin/public-urls/verify-domain — DNS A-record probe for Add domain wizard

Tunnel mode switch (M-5.4) lives in a separate router; this file is
URL-list bookkeeping only.
"""

from __future__ import annotations

import asyncio
import socket

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_dependencies import AuthenticatedUser, require_role
from ...db import get_session
from ...models import Lab, Server
from ...services import agent_hub, agent_url_broadcast, audit_service, lab_url_service
from ...services.lab_url_service import LabUrlError

router = APIRouter(prefix="/admin/public-urls", tags=["admin-public-urls"])


# ── schemas ────────────────────────────────────────────────────────────


class PublicUrlEntry(BaseModel):
    url: str
    kind: str
    source: str
    verified_at: str | None = None
    last_reachable_at: str | None = None
    primary: bool = False
    reachable: bool | None = None  # populated by /probe-now or recent scheduler tick


class PublicUrlsResponse(BaseModel):
    urls: list[PublicUrlEntry]
    tunnel_mode: str
    tunnel_token_set: bool = False


class AddPublicUrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    kind: str = Field(default="custom_domain")
    make_primary: bool = False


class DomainVerifyRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=253)


class DomainVerifyResponse(BaseModel):
    domain: str
    resolved: list[str]
    matches_expected: bool
    expected_any: list[str]


# ── helpers ────────────────────────────────────────────────────────────


async def _get_lab(session: AsyncSession, lab_id: int) -> Lab:
    lab = await session.get(Lab, lab_id)
    if lab is None:
        raise HTTPException(status_code=404, detail="lab not found")
    return lab


async def _broadcast_urls_to_agents(session: AsyncSession, lab: Lab) -> None:
    """Push the post-mutation URL list to every online agent in this lab.

    Best-effort — broadcast failures get swallowed by the underlying
    helper; agents pick up the new list on next reconnect regardless.
    """
    urls = lab_url_service.agent_urls_only(lab)
    if not urls:
        return
    result = await session.execute(
        select(Server.id).where(Server.lab_id == lab.id, Server.is_active == 1)
    )
    candidate_ids = [row for row in result.scalars().all() if agent_hub.pool.is_online(row)]
    if candidate_ids:
        await agent_url_broadcast.broadcast_update_urls(server_ids=candidate_ids, urls=urls)


def _serialize(lab: Lab, reachable: dict[str, bool] | None = None) -> PublicUrlsResponse:
    entries: list[PublicUrlEntry] = []
    for raw in lab_url_service.load_entries(lab):
        entries.append(
            PublicUrlEntry(
                url=raw.url,
                kind=raw.kind,
                source=raw.source,
                verified_at=raw.verified_at,
                last_reachable_at=raw.last_reachable_at,
                primary=raw.primary,
                reachable=reachable.get(raw.url) if reachable else None,
            )
        )
    return PublicUrlsResponse(
        urls=entries,
        tunnel_mode=lab.tunnel_mode,
        tunnel_token_set=lab.tunnel_token is not None and lab.tunnel_token != "",
    )


# ── endpoints ──────────────────────────────────────────────────────────


@router.get("", response_model=PublicUrlsResponse)
async def list_public_urls(
    session: AsyncSession = Depends(get_session),
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
) -> PublicUrlsResponse:
    lab = await _get_lab(session, current.lab_id)
    return _serialize(lab)


@router.post("", response_model=PublicUrlsResponse)
async def add_public_url(
    payload: AddPublicUrlRequest,
    session: AsyncSession = Depends(get_session),
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
) -> PublicUrlsResponse:
    lab = await _get_lab(session, current.lab_id)
    try:
        await lab_url_service.add_url(
            session,
            lab=lab,
            url=payload.url,
            kind=payload.kind,
            source="manual_admin",
            make_primary=payload.make_primary,
        )
    except LabUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_service.write(
        session,
        action="lab.public_url.add",
        actor_user_id=current.user.id,
        lab_id=lab.id,
        target_type="lab",
        target_id=lab.id,
        payload={"url": payload.url, "kind": payload.kind},
    )
    await session.commit()
    await _broadcast_urls_to_agents(session, lab)
    return _serialize(lab)


@router.delete("", response_model=PublicUrlsResponse)
async def remove_public_url(
    url: str = Query(min_length=1, max_length=500),
    session: AsyncSession = Depends(get_session),
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
) -> PublicUrlsResponse:
    lab = await _get_lab(session, current.lab_id)
    before_count = len(lab_url_service.load_entries(lab))
    await lab_url_service.remove_url(session, lab=lab, url=url)
    after_count = len(lab_url_service.load_entries(lab))
    if after_count == before_count:
        raise HTTPException(status_code=404, detail="url not in this lab's public_urls")
    await audit_service.write(
        session,
        action="lab.public_url.remove",
        actor_user_id=current.user.id,
        lab_id=lab.id,
        target_type="lab",
        target_id=lab.id,
        payload={"url": url},
    )
    await session.commit()
    await _broadcast_urls_to_agents(session, lab)
    return _serialize(lab)


@router.post("/probe-now", response_model=PublicUrlsResponse)
async def probe_now(
    session: AsyncSession = Depends(get_session),
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
) -> PublicUrlsResponse:
    lab = await _get_lab(session, current.lab_id)
    summary = await lab_url_service.probe_lab(session, lab=lab)
    await session.commit()
    return _serialize(lab, reachable=summary)


@router.post("/verify-domain", response_model=DomainVerifyResponse)
async def verify_domain(
    payload: DomainVerifyRequest,
    current: AuthenticatedUser = Depends(require_role("lab_admin")),
) -> DomainVerifyResponse:
    """DNS A-record probe used by the Add-domain wizard.

    No write side-effects — purely tells the admin "does this domain
    point here yet?" The actual add-to-public_urls step is a separate
    POST /admin/public-urls call.

    `matches_expected` is true if any of the resolved IPs match the
    backend's egress public IP discovered via standard system DNS for
    its own hostname. False is informational, not a hard block (NAT /
    Cloudflare proxy / etc. can legitimately make this false).
    """
    domain = payload.domain.strip().lower()
    # Strip protocol/path defensively.
    if "://" in domain:
        domain = domain.split("://", 1)[1]
    if "/" in domain:
        domain = domain.split("/", 1)[0]

    resolved: list[str] = []
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(domain, None, family=socket.AF_INET)
        resolved = sorted({info[4][0] for info in infos})
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"could not resolve domain: {exc}") from exc

    expected: list[str] = []
    try:
        hostname = socket.gethostname()
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(hostname, None, family=socket.AF_INET)
        expected = sorted({info[4][0] for info in infos})
    except OSError:
        expected = []

    matches = any(ip in expected for ip in resolved) if expected else False
    return DomainVerifyResponse(
        domain=domain,
        resolved=resolved,
        matches_expected=matches,
        expected_any=expected,
    )
