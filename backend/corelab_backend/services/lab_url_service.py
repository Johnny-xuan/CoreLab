"""lab_url_service — manages ``lab.public_urls`` JSON column.

Phase M v5 (rev2) — CoreLab is multi-URL native: every lab carries a
list of URLs by which the backend is reachable (LAN IP, public IP,
custom domain, Cloudflare Tunnel). install.sh seeds the initial list,
admins edit it from the Public Access card, the URL probe scheduler
keeps the reachability state fresh, and agents try each URL in turn.

All mutations go through this service so the JSON column never gets
mis-shaped writes (the rest of the codebase reads it as a list of
dicts and depends on the canonical shape).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..logging_setup import get_logger
from ..models import Lab

_log = get_logger("corelab.lab_url_service")

URL_KIND = Literal[
    "lan",
    "public_ip",
    "custom_domain",
    "cloudflare_quick",
    "cloudflare_named",
]
URL_SOURCE = Literal["install_sh_probe", "manual_admin", "tunnel_runtime"]
VALID_KINDS = {
    "lan",
    "public_ip",
    "custom_domain",
    "cloudflare_quick",
    "cloudflare_named",
}
VALID_SOURCES = {"install_sh_probe", "manual_admin", "tunnel_runtime"}
AGENT_URL_STALE_SECONDS = 10 * 60
EPHEMERAL_AGENT_URL_KINDS = {"cloudflare_quick"}


@dataclass
class UrlEntry:
    """In-memory representation of a public_urls entry.

    Serialise via ``to_dict()`` before writing the JSON column. Loaded
    via ``from_dict()`` which tolerates legacy / missing fields so we
    don't break on rows written by an older install.
    """

    url: str
    kind: str
    source: str
    verified_at: str | None = None
    last_reachable_at: str | None = None
    primary: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "kind": self.kind,
            "source": self.source,
            "verified_at": self.verified_at,
            "last_reachable_at": self.last_reachable_at,
            "primary": self.primary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UrlEntry:
        return cls(
            url=str(data["url"]),
            kind=str(data.get("kind", "lan")),
            source=str(data.get("source", "manual_admin")),
            verified_at=data.get("verified_at"),
            last_reachable_at=data.get("last_reachable_at"),
            primary=bool(data.get("primary", False)),
        )


class LabUrlError(Exception):
    """Validation / state error raised on bad add_url input."""


# ── reads ──────────────────────────────────────────────────────────────


def load_entries(lab: Lab) -> list[UrlEntry]:
    """Parse ``lab.public_urls`` into typed entries."""
    raw = lab.public_urls or []
    return [UrlEntry.from_dict(e) for e in raw if isinstance(e, dict) and "url" in e]


def urls_only(lab: Lab) -> list[str]:
    """Plain list of URLs — used when handing to the agent."""
    return [e.url for e in load_entries(lab)]


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_recent(value: str | None, *, now: datetime, stale_after: timedelta) -> bool:
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        return False
    return parsed >= now - stale_after


def _is_agent_url_candidate(
    entry: UrlEntry,
    *,
    now: datetime,
    stale_after: timedelta,
) -> bool:
    """Return whether a public URL should be pushed into agent config.

    LAN/custom/named URLs are stable configuration and may be reachable
    from the agent even when the backend container cannot probe them
    directly. Cloudflare Quick Tunnel URLs are different: they are
    short-lived and old entries create noisy reconnect fallback loops.
    Keep a quick tunnel only when it was freshly discovered by runtime
    tooling or has recent reachability evidence.
    """
    if entry.kind not in EPHEMERAL_AGENT_URL_KINDS:
        return True
    if entry.source == "tunnel_runtime" and not entry.verified_at and not entry.last_reachable_at:
        return True
    return _is_recent(entry.last_reachable_at, now=now, stale_after=stale_after) or _is_recent(
        entry.verified_at,
        now=now,
        stale_after=stale_after,
    )


def agent_urls_only(
    lab: Lab,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = AGENT_URL_STALE_SECONDS,
) -> list[str]:
    """URL list safe to persist into agent ``backend_urls``.

    This intentionally differs from ``urls_only``. ``public_urls`` is
    an admin-facing ledger of known access URLs, including historical
    tunnel URLs. Agent config is an operational reconnect list, so stale
    short-lived tunnel URLs should not be pushed forever. If filtering
    would remove every URL, fall back to the ledger to avoid stranding a
    remote agent on an overly strict heuristic.
    """
    entries = load_entries(lab)
    if not entries:
        return []
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    stale_after = timedelta(seconds=stale_after_seconds)
    filtered = [
        e.url for e in entries if _is_agent_url_candidate(e, now=now_utc, stale_after=stale_after)
    ]
    return filtered or [e.url for e in entries]


# ── mutations ──────────────────────────────────────────────────────────


def _persist(lab: Lab, entries: list[UrlEntry]) -> None:
    lab.public_urls = [e.to_dict() for e in entries]


async def replace_all(session: AsyncSession, *, lab: Lab, entries: list[UrlEntry]) -> Lab:
    """Wholesale replace ``lab.public_urls`` (used by setup_service for seed)."""
    for e in entries:
        if e.kind not in VALID_KINDS:
            raise LabUrlError(f"invalid kind: {e.kind}")
        if e.source not in VALID_SOURCES:
            raise LabUrlError(f"invalid source: {e.source}")
    _persist(lab, entries)
    await session.flush()
    return lab


async def add_url(
    session: AsyncSession,
    *,
    lab: Lab,
    url: str,
    kind: str,
    source: str,
    make_primary: bool = False,
) -> Lab:
    if kind not in VALID_KINDS:
        raise LabUrlError(f"invalid kind: {kind}")
    if source not in VALID_SOURCES:
        raise LabUrlError(f"invalid source: {source}")
    if not url:
        raise LabUrlError("url must not be empty")
    entries = load_entries(lab)
    for e in entries:
        if e.url == url:
            # Already present — touch fields only.
            e.kind = kind
            e.source = source
            if make_primary:
                for other in entries:
                    other.primary = False
                e.primary = True
            _persist(lab, entries)
            await session.flush()
            return lab
    new = UrlEntry(url=url, kind=kind, source=source, primary=make_primary)
    if make_primary:
        for other in entries:
            other.primary = False
    entries.append(new)
    _persist(lab, entries)
    await session.flush()
    return lab


async def remove_url(session: AsyncSession, *, lab: Lab, url: str) -> Lab:
    entries = [e for e in load_entries(lab) if e.url != url]
    _persist(lab, entries)
    await session.flush()
    return lab


async def mark_reachable(session: AsyncSession, *, lab: Lab, url: str, reachable: bool) -> Lab:
    """Update probe-state on a single URL. No-op if URL not in list."""
    entries = load_entries(lab)
    now = datetime.now(UTC).isoformat()
    touched = False
    for e in entries:
        if e.url == url:
            if reachable:
                e.last_reachable_at = now
                if e.verified_at is None:
                    e.verified_at = now
            else:
                # Don't clear last_reachable_at — keep the last-known
                # good time so admins can see how long it's been down.
                pass
            touched = True
            break
    if touched:
        _persist(lab, entries)
        await session.flush()
    return lab


# ── probe ──────────────────────────────────────────────────────────────


async def probe_one(url: str, *, probe_timeout: float = 5.0) -> bool:
    """HEAD/GET ``/healthz`` on a URL and return True iff 2xx.

    Uses an unverified HTTPS context (Cloudflare Tunnel terminates TLS
    upstream; LE-signed custom domains will succeed naturally). We tolerate
    redirects since some setups front the backend with a path-stripping
    proxy.
    """
    healthz = url.rstrip("/") + "/healthz"
    try:
        async with httpx.AsyncClient(
            timeout=probe_timeout, follow_redirects=True, verify=False
        ) as client:
            resp = await client.get(healthz)
            return 200 <= resp.status_code < 300
    except (httpx.HTTPError, OSError):
        return False


async def probe_lab(session: AsyncSession, *, lab: Lab) -> dict[str, bool]:
    """Probe every URL in this lab and update reachability state."""
    entries = load_entries(lab)
    if not entries:
        return {}
    results = await asyncio.gather(*(probe_one(e.url) for e in entries), return_exceptions=False)
    now = datetime.now(UTC).isoformat()
    summary: dict[str, bool] = {}
    for e, ok in zip(entries, results, strict=True):
        summary[e.url] = ok
        if ok:
            e.last_reachable_at = now
            if e.verified_at is None:
                e.verified_at = now
    _persist(lab, entries)
    await session.flush()
    return summary
