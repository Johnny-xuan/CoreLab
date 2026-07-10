"""Phase 8 C2 — push reverse-lookup cache (account_link + active
reservations) to the agent (P8-10).

The agent's ``compliance_monitor`` does all reverse-lookup against a
local cache (Phase 8 Worker Catch #1 architectural fix). This service
composes the cache payload from the database and pushes it through
``backend.account_link_cache.sync``.

Two modes:
* ``push_full_to_server`` — snapshot every linux user on the server
  that has at least one active link + its current active reservations
  (called on first connect + TTL-driven refresh)
* ``push_incremental_to_server`` — push just the deltas for a single
  linux user (called after account_link or reservation mutations)
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logging_setup import get_logger
from ..models import AccountLink, PhysicalAccount, Reservation, User
from . import agent_hub, agent_rpc

_log = get_logger("corelab.link_cache_sync")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    # MySQL returns tz-naive — stamp UTC to match the protocol contract
    # (Phase 7 lesson #1).
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _compute_etag(entries: list[dict[str, Any]]) -> str:
    canonical = "|".join(
        f"{e['linux_username']}={','.join(map(str, sorted(e['user_ids'])))}" for e in entries
    )
    return "links-" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


async def _entries_for_linux_users(
    session: AsyncSession,
    *,
    server_id: int,
    linux_usernames: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Build the cache entries for a list of linux usernames on the
    server (or for *all* linked accounts on the server if
    ``linux_usernames`` is None)."""
    pa_q = select(PhysicalAccount).where(
        PhysicalAccount.server_id == server_id,
        PhysicalAccount.is_active == 1,
    )
    if linux_usernames is not None:
        pa_q = pa_q.where(PhysicalAccount.linux_username.in_(list(linux_usernames)))
    pas = (await session.execute(pa_q)).scalars().all()

    out: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for pa in pas:
        # All active links pointing at this PA.
        link_q = await session.execute(
            select(AccountLink, User.username)
            .join(User, AccountLink.user_id == User.id)
            .where(
                AccountLink.physical_account_id == pa.id,
                AccountLink.is_active == 1,
            )
        )
        rows = link_q.all()
        if not rows:
            continue
        user_ids = sorted({int(link.user_id) for link, _ in rows})

        # Active or scheduled reservations for these users on this server,
        # overlapping "now or in the future" (we cache scheduled + active
        # so the monitor knows about an imminent upcoming session too).
        active_reservations: dict[str, list[dict[str, Any]]] = {}
        for link, _ in rows:
            res_q = await session.execute(
                select(Reservation).where(
                    Reservation.account_link_id == link.id,
                    Reservation.status.in_(("scheduled", "active")),
                    Reservation.end_at >= now,
                )
            )
            for r in res_q.scalars():
                key = str(int(link.user_id))
                active_reservations.setdefault(key, []).append(
                    {
                        "reservation_id": r.id,
                        "gpu_id": r.gpu_id,
                        "start_at": _iso(r.start_at),
                        "end_at": _iso(r.end_at),
                        "status": r.status,
                        "gpu_memory_mb": r.gpu_memory_mb,
                        "gpu_compute_share_pct": r.gpu_compute_share_pct,
                        "source": link.source,
                    }
                )

        out.append(
            {
                "linux_username": pa.linux_username,
                "user_ids": user_ids,
                "active_reservations": active_reservations,
            }
        )
    return out


async def compose_full_payload(session: AsyncSession, *, server_id: int) -> dict[str, Any]:
    entries = await _entries_for_linux_users(session, server_id=server_id)
    return {
        "server_id": server_id,
        "mode": "full",
        "entries": entries,
        "removed_linux_usernames": [],
        "etag": _compute_etag(entries),
    }


async def compose_incremental_payload(
    session: AsyncSession,
    *,
    server_id: int,
    linux_usernames: list[str],
    removed_linux_usernames: list[str] | None = None,
) -> dict[str, Any]:
    entries = await _entries_for_linux_users(
        session, server_id=server_id, linux_usernames=linux_usernames
    )
    return {
        "server_id": server_id,
        "mode": "incremental",
        "entries": entries,
        "removed_linux_usernames": removed_linux_usernames or [],
        "etag": _compute_etag(entries),
    }


async def _push(*, server_id: int, payload: dict[str, Any], timeout_seconds: float) -> bool:
    try:
        result = await agent_rpc.request_response(
            server_id=server_id,
            frame_type="backend.account_link_cache.sync",
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
    except agent_rpc.AgentOfflineError:
        _log.info("link_cache_sync.agent_offline", server_id=server_id)
        return False
    except agent_rpc.AgentRpcTimeoutError as exc:
        _log.warning("link_cache_sync.timeout", server_id=server_id, error=str(exc))
        return False
    if not result.get("ok"):
        _log.warning(
            "link_cache_sync.rejected",
            server_id=server_id,
            error=result.get("error"),
        )
        return False
    return True


async def push_full_to_server(
    session: AsyncSession,
    *,
    server_id: int,
    timeout_seconds: float = 10.0,
) -> bool:
    payload = await compose_full_payload(session, server_id=server_id)
    return await _push(server_id=server_id, payload=payload, timeout_seconds=timeout_seconds)


async def push_incremental_to_server(
    session: AsyncSession,
    *,
    server_id: int,
    linux_usernames: list[str],
    removed_linux_usernames: list[str] | None = None,
    timeout_seconds: float = 5.0,
) -> bool:
    payload = await compose_incremental_payload(
        session,
        server_id=server_id,
        linux_usernames=linux_usernames,
        removed_linux_usernames=removed_linux_usernames,
    )
    return await _push(server_id=server_id, payload=payload, timeout_seconds=timeout_seconds)


async def send_on_connect(session: AsyncSession, *, server_id: int) -> bool:
    """Push full reverse-lookup cache during agent connect without waiting for ack."""
    payload = await compose_full_payload(session, server_id=server_id)
    ws = agent_hub.pool.get(server_id)
    if ws is None:
        return False
    frame_id = str(uuid4())
    agent_rpc.expect_optional_response(
        correlation_id=frame_id,
        frame_type="backend.account_link_cache.sync",
    )
    frame = json.dumps(
        {
            "type": "backend.account_link_cache.sync",
            "id": frame_id,
            "ts": datetime.now(UTC).isoformat(),
            "payload": payload,
            "correlation_id": None,
        }
    )
    with contextlib.suppress(Exception):
        await ws.send_text(frame)
        _log.info("link_cache_sync.sent_on_connect", server_id=server_id)
        return True
    return False
