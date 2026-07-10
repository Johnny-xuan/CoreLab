"""Setup wizard — initial lab + first lab_admin creation.

``initialize`` is the single mutating entry point and must enforce the
single-row lab invariant (Phase 2 invariant #3) and the "only one chance"
rule (Phase 2 invariant #5).

Phase M M-2.2 — ``derive_slug`` produces a safe ``lab_slug`` from any
``lab_name`` so users typing a non-ASCII (e.g. Chinese) lab name don't
get stuck. ``initialize`` falls back to ``derive_slug(lab_name)`` when
the request omits ``lab_slug``.
"""

from __future__ import annotations

import contextlib
import json
import re
import secrets

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import get_redis_client
from ..config import get_settings
from ..logging_setup import get_logger
from ..models import Lab, User
from ..schemas.setup import SetupInitRequest
from ..security import hash_password
from . import audit_service, lab_url_service
from .lab_url_service import UrlEntry

_log = get_logger("corelab.setup_service")
SEED_URLS_REDIS_KEY = "corelab:install:seed_urls"


def derive_slug(lab_name: str) -> str:
    """Best-effort ASCII slug from ``lab_name``.

    Lowercases, replaces runs of non-ASCII-alnum with a single hyphen,
    trims edge hyphens, then enforces the LabSlugPattern constraints
    (must start with a letter, 2-50 chars). Falls back to
    ``lab-{8 hex}`` when the result is empty or otherwise unusable.
    """
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", lab_name.lower()).strip("-")
    if cleaned and not cleaned[0].isalpha():
        cleaned = "lab-" + cleaned
    cleaned = cleaned[:50]
    if len(cleaned) < 2:
        return f"lab-{secrets.token_hex(4)}"
    return cleaned


class SetupError(Exception):
    """Raised when setup invariants are violated; routers translate to HTTP."""


class AlreadyInitializedError(SetupError):
    """Lab table is non-empty; /setup/init is forbidden."""


async def is_initialized(session: AsyncSession) -> bool:
    row = await session.execute(select(func.count()).select_from(Lab))
    return bool(row.scalar_one())


async def _load_seed_urls() -> list[UrlEntry]:
    """Pop the URL list install.sh wrote into redis (best-effort).

    install.sh runs before the first lab exists, so it stages the
    URLs it discovered (LAN IP, public IP, Cloudflare Tunnel URL) into
    a redis key under ``corelab:install:seed_urls``. We fold those into
    ``lab.public_urls`` at setup time so the admin sees them in the
    Public Access card without having to type them by hand. Missing /
    invalid seed is non-fatal — the fallback is the BACKEND_PUBLIC_URL
    env var as a single LAN entry.
    """
    client = get_redis_client()
    if client is None:
        return []
    try:
        raw = await client.get(SEED_URLS_REDIS_KEY)
    except Exception as exc:
        _log.warning("setup.seed_urls.redis_unavailable", error=str(exc))
        return []
    if raw is None:
        return []
    try:
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _log.warning("setup.seed_urls.parse_error", error=str(exc))
        return []
    if not isinstance(data, list):
        _log.warning("setup.seed_urls.not_a_list", got=type(data).__name__)
        return []
    entries: list[UrlEntry] = []
    for raw_entry in data:
        if not isinstance(raw_entry, dict) or "url" not in raw_entry:
            continue
        try:
            entries.append(UrlEntry.from_dict(raw_entry))
        except (KeyError, ValueError) as exc:
            _log.warning("setup.seed_urls.bad_entry", error=str(exc))
    # Pop the key now so a re-init never re-applies stale seed data.
    with contextlib.suppress(Exception):
        await client.delete(SEED_URLS_REDIS_KEY)
    # First entry becomes the UI "primary" if nothing else flagged.
    if entries and not any(e.primary for e in entries):
        entries[0].primary = True
    return entries


def _fallback_seed_url() -> list[UrlEntry]:
    """When redis has nothing, use BACKEND_PUBLIC_URL env as the sole entry.

    Categorises the URL by what it looks like: 127.0.0.1/localhost → lan,
    anything else → public_ip. The Public Access card lets the admin
    re-classify or remove afterwards.
    """
    settings = get_settings()
    url = settings.backend_public_url
    if not url:
        return []
    lowered = url.lower()
    kind = "lan" if ("localhost" in lowered or "127.0.0.1" in lowered) else "public_ip"
    return [UrlEntry(url=url, kind=kind, source="install_sh_probe", primary=True)]


async def initialize(
    session: AsyncSession,
    payload: SetupInitRequest,
    *,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[Lab, User]:
    if await is_initialized(session):
        raise AlreadyInitializedError("CoreLab is already initialized")

    slug = payload.lab_slug or derive_slug(payload.lab_name)
    lab = Lab(name=payload.lab_name, slug=slug, description=None)
    session.add(lab)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise SetupError("lab_slug already taken") from exc

    # Phase M v5 — fold the URLs install.sh discovered into public_urls.
    seed_entries = await _load_seed_urls()
    if not seed_entries:
        seed_entries = _fallback_seed_url()
    if seed_entries:
        await lab_url_service.replace_all(session, lab=lab, entries=seed_entries)

    admin = User(
        lab_id=lab.id,
        username=payload.admin_username,
        email=payload.admin_email,
        password_hash=hash_password(payload.admin_password),
        display_name=payload.admin_display_name,
        role="lab_admin",
        is_active=1,
    )
    session.add(admin)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise SetupError("admin_username or admin_email already taken") from exc

    await audit_service.write(
        session,
        action="setup.init",
        actor_user_id=admin.id,
        lab_id=lab.id,
        target_type="lab",
        target_id=lab.id,
        payload={
            "lab_slug": lab.slug,
            "admin_username": admin.username,
            "admin_email": admin.email,
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return lab, admin
