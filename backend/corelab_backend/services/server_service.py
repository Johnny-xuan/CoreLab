"""Server + EnrollmentToken + ServerAdminGrant + AgentCapability service.

Combines the CRUD-y server operations because they all share the same
business-rule envelope (lab_admin gate, audit writes, transactional
side effects). Each public function owns its own audit_log row.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import AgentCapability, EnrollmentToken, Gpu, Lab, Server, ServerAdminGrant
from . import audit_service, lab_url_service

DANGEROUS_CAPABILITIES: frozenset[str] = frozenset(
    {"gpu.kill_process", "linux.useradd", "linux.userdel"}
)

# 10 seeded capability keys (docs/02-data-model.md §5.17). New servers
# get a row per key on creation; later capability writes are PATCHes,
# not POSTs, so seeding here keeps the (server_id, capability_key) set
# stable.
CAPABILITY_SEED: tuple[tuple[str, bool, bool], ...] = (
    # (key, default_enabled, is_dangerous)
    ("gpu.read_telemetry", True, False),
    ("gpu.kill_process", False, True),
    ("linux.useradd", False, True),
    ("linux.userdel", False, True),
    ("linux.usermod_lock", True, False),
    ("ssh.push_authorized_key", True, False),
    ("ssh.remove_authorized_key", True, False),
    ("ssh.verify_signature", True, False),
    ("pam.authenticate", True, False),
    ("script.execute_as_user", True, False),
    ("linux.scan_users", True, False),
)


class ServerError(Exception):
    pass


class ServerNotFoundError(ServerError):
    pass


class DuplicateHostnameError(ServerError):
    pass


class DangerousCapabilityWithoutNotesError(ServerError):
    """Refused: dangerous capabilities cannot be enabled without notes >= 10 chars."""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _generate_token() -> tuple[str, str]:
    plaintext = secrets.token_urlsafe(32)
    return plaintext, _sha256(plaintext)


def _install_snippet(*, backend_http_url: str, server_id: int, plaintext_token: str) -> str:
    """Phase M M-1.3 — one-line ``curl | bash`` installer snippet.

    The script downloaded from ``/api/v1/install/agent.sh`` handles
    everything: precheck, tarball fetch, venv build, systemd unit, start.
    Caller injects the enrollment token + server id via env so the
    snippet stays a single command that copy-pastes cleanly into a root
    shell on the target GPU host.

    A two-step fallback (curl-to-file then inspect) is included below
    the one-liner for operators who don't want to pipe network input
    straight into bash.
    """
    base = backend_http_url.rstrip("/")
    return f"""# Phase M install snippet — copy & paste on the target GPU host as root.
#
# One-shot:
curl -fsSL {base}/api/v1/install/agent.sh | \\
  CORELAB_BACKEND_URLS='{base}' \\
  CORELAB_SERVER_ID={server_id} \\
  CORELAB_ENROLLMENT_TOKEN='{plaintext_token}' \\
  sudo -E bash

# Fallback (inspect before running):
#   curl -fsSL {base}/api/v1/install/agent.sh -o /tmp/install-agent.sh
#   less /tmp/install-agent.sh
#   sudo CORELAB_BACKEND_URLS='{base}' \\
#        CORELAB_SERVER_ID={server_id} \\
#        CORELAB_ENROLLMENT_TOKEN='{plaintext_token}' \\
#        bash /tmp/install-agent.sh
#
# No-sudo (e.g. shared lab account)? Append --user-mode to the bash line.
# No GPU? Append --mock.
"""


async def _build_install_snippet(
    session: AsyncSession,
    *,
    lab_id: int,
    server_id: int,
    plaintext_token: str,
) -> str:
    """Phase M v5 M-6 — gather the lab's URL list + format the snippet.

    Pulls every URL from ``lab.public_urls`` (LAN, public IP, tunnel,
    custom domain), then renders the multi-URL ``curl | bash`` snippet.
    Falls back to ``settings.backend_public_url`` if the lab is so
    fresh it has no URLs yet (shouldn't happen post-setup, but the
    fallback keeps create_server from blowing up).
    """
    lab = await session.get(Lab, lab_id)
    urls: list[str] = []
    if lab is not None:
        urls = lab_url_service.urls_only(lab)
    if not urls:
        urls = [get_settings().backend_public_url]
    return _install_snippet_multi(
        backend_http_urls=urls,
        server_id=server_id,
        plaintext_token=plaintext_token,
    )


def _install_snippet_multi(
    *,
    backend_http_urls: list[str],
    server_id: int,
    plaintext_token: str,
) -> str:
    """Phase M v5 M-6 — multi-URL variant. First URL hosts /install/agent.sh."""
    if not backend_http_urls:
        backend_http_urls = ["http://localhost"]
    primary = backend_http_urls[0].rstrip("/")
    urls_csv = ",".join(u.rstrip("/") for u in backend_http_urls)
    return f"""# Phase M v5 install snippet — multi-URL agent (LAN + tunnel + domain).
#
# One-shot (target GPU host as root):
curl -fsSL {primary}/api/v1/install/agent.sh | \\
  CORELAB_BACKEND_URLS='{urls_csv}' \\
  CORELAB_SERVER_ID={server_id} \\
  CORELAB_ENROLLMENT_TOKEN='{plaintext_token}' \\
  sudo -E bash

# Inspect-first fallback:
#   curl -fsSL {primary}/api/v1/install/agent.sh -o /tmp/install-agent.sh
#   less /tmp/install-agent.sh
#   sudo CORELAB_BACKEND_URLS='{urls_csv}' \\
#        CORELAB_SERVER_ID={server_id} \\
#        CORELAB_ENROLLMENT_TOKEN='{plaintext_token}' \\
#        bash /tmp/install-agent.sh
#
# Agent will try every URL in turn. Tunnel / public-IP / LAN — whichever
# is reachable from this GPU host wins. The agent absorbs subsequent
# URL changes via the backend.config.update_urls frame on reconnect.
#
# No sudo (lab account)? Append --user-mode.
# No GPU? Append --mock.
"""


async def list_servers(session: AsyncSession, *, lab_id: int) -> Sequence[Server]:
    result = await session.execute(
        select(Server).where(Server.lab_id == lab_id, Server.is_active == 1).order_by(Server.id)
    )
    return result.scalars().all()


async def get_server(session: AsyncSession, server_id: int, *, lab_id: int) -> Server:
    server = await session.get(Server, server_id)
    if server is None or server.lab_id != lab_id:
        raise ServerNotFoundError(f"server {server_id} not found")
    return server


async def create_server(
    session: AsyncSession,
    *,
    hostname: str,
    display_name: str | None,
    max_reservation_hours: int | None,
    expected_hostname_pattern: str | None,
    lab_id: int,
    actor_user_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[Server, str, datetime, str]:
    """Returns (server, plaintext_enrollment_token, expires_at, install_snippet)."""
    existing = await session.execute(
        select(Server).where(Server.lab_id == lab_id, Server.hostname == hostname)
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicateHostnameError(f"hostname {hostname!r} already taken in this lab")

    server = Server(
        lab_id=lab_id,
        hostname=hostname,
        display_name=display_name,
        max_reservation_hours=max_reservation_hours,
        status="pending",
        created_by_user_id=actor_user_id,
    )
    session.add(server)
    await session.flush()

    # Seed capability rows for the new server.
    for key, default_enabled, is_dangerous in CAPABILITY_SEED:
        session.add(
            AgentCapability(
                server_id=server.id,
                capability_key=key,
                is_enabled=1 if default_enabled else 0,
                is_dangerous=1 if is_dangerous else 0,
                notes=None,
                updated_by_user_id=actor_user_id,
            )
        )

    plaintext, hashed = _generate_token()
    expires_at = datetime.now(UTC) + timedelta(days=7)
    session.add(
        EnrollmentToken(
            lab_id=lab_id,
            token_hash=hashed,
            # Default the hint to this server's hostname so regenerate
            # can scope its revoke step precisely (vs nuking every
            # in-flight token in the lab).
            expected_hostname_pattern=expected_hostname_pattern or hostname,
            expires_at=expires_at,
            created_by_user_id=actor_user_id,
        )
    )
    await session.flush()

    snippet = await _build_install_snippet(
        session,
        lab_id=lab_id,
        server_id=server.id,
        plaintext_token=plaintext,
    )

    await audit_service.write(
        session,
        action="server.create",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="server",
        target_id=server.id,
        target_lab_id=lab_id,
        target_server_id=server.id,
        payload={"hostname": server.hostname, "display_name": server.display_name},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    await audit_service.write(
        session,
        action="server.enrollment_token.generate",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="server",
        target_id=server.id,
        target_lab_id=lab_id,
        target_server_id=server.id,
        payload={"expires_at": expires_at.isoformat()},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return server, plaintext, expires_at, snippet


async def delete_server(
    session: AsyncSession,
    server_id: int,
    *,
    lab_id: int,
    actor_user_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> Server:
    server = await get_server(session, server_id, lab_id=lab_id)
    server.is_active = 0
    server.status = "maintenance"
    await session.flush()
    await audit_service.write(
        session,
        action="server.delete",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="server",
        target_id=server.id,
        target_lab_id=lab_id,
        target_server_id=server.id,
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return server


async def approve_server(
    session: AsyncSession,
    server_id: int,
    *,
    lab_id: int,
    actor_user_id: int,
    is_connected: bool,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> Server:
    server = await get_server(session, server_id, lab_id=lab_id)
    if not server.is_active:
        raise ServerError(f"server {server_id} is soft-deleted")
    if server.agent_token_hash is None:
        raise ServerError("server_has_not_phoned_home")
    newly_approved = server.approved_at is None
    if newly_approved:
        server.approved_at = datetime.now(UTC)
        server.approved_by_user_id = actor_user_id
        await audit_service.write(
            session,
            action="server.approve",
            actor_user_id=actor_user_id,
            lab_id=lab_id,
            target_type="server",
            target_id=server.id,
            target_lab_id=lab_id,
            target_server_id=server.id,
            payload={
                "hostname": server.hostname,
                "agent_version": server.agent_version,
                "last_heartbeat_at": (
                    server.last_heartbeat_at.isoformat()
                    if server.last_heartbeat_at is not None
                    else None
                ),
                "connected_at_approval": is_connected,
            },
            ip_address=request_ip,
            user_agent=user_agent,
        )
    if newly_approved or server.status != "maintenance":
        server.status = "online" if is_connected else "offline"
    await session.flush()
    return server


async def list_gpus(session: AsyncSession, *, server_id: int) -> Sequence[Gpu]:
    result = await session.execute(
        select(Gpu).where(Gpu.server_id == server_id).order_by(Gpu.gpu_index)
    )
    return result.scalars().all()


async def list_admins(session: AsyncSession, *, server_id: int) -> Sequence[ServerAdminGrant]:
    result = await session.execute(
        select(ServerAdminGrant)
        .where(ServerAdminGrant.server_id == server_id, ServerAdminGrant.is_active == 1)
        .order_by(ServerAdminGrant.id)
    )
    return result.scalars().all()


async def list_grants_for_user(
    session: AsyncSession, *, user_id: int, lab_id: int
) -> list[tuple[ServerAdminGrant, Server]]:
    """Active grants this user holds — sidebar uses this to render the
    per-server management group. Joins Server so caller can show hostname /
    display_name without a second roundtrip."""
    result = await session.execute(
        select(ServerAdminGrant, Server)
        .join(Server, Server.id == ServerAdminGrant.server_id)
        .where(
            ServerAdminGrant.user_id == user_id,
            ServerAdminGrant.is_active == 1,
            Server.lab_id == lab_id,
        )
        .order_by(Server.hostname)
    )
    return [(row[0], row[1]) for row in result.all()]


async def grant_admin(
    session: AsyncSession,
    *,
    server_id: int,
    user_id: int,
    notes: str | None,
    actor_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> ServerAdminGrant:
    grant = ServerAdminGrant(
        user_id=user_id,
        server_id=server_id,
        granted_by_user_id=actor_user_id,
        notes=notes,
        is_active=1,
    )
    session.add(grant)
    await session.flush()
    await audit_service.write(
        session,
        action="server.admin_grant.create",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="server_admin_grant",
        target_id=grant.id,
        target_lab_id=lab_id,
        target_server_id=server_id,
        payload={"user_id": user_id, "notes": notes},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return grant


async def revoke_admin(
    session: AsyncSession,
    *,
    server_id: int,
    user_id: int,
    actor_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> ServerAdminGrant | None:
    result = await session.execute(
        select(ServerAdminGrant).where(
            ServerAdminGrant.server_id == server_id,
            ServerAdminGrant.user_id == user_id,
            ServerAdminGrant.is_active == 1,
        )
    )
    grant = result.scalar_one_or_none()
    if grant is None:
        return None
    grant.is_active = 0
    grant.revoked_by_user_id = actor_user_id
    grant.revoked_at = datetime.now(UTC)
    await session.flush()
    await audit_service.write(
        session,
        action="server.admin_grant.delete",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="server_admin_grant",
        target_id=grant.id,
        target_lab_id=lab_id,
        target_server_id=server_id,
        payload={"user_id": user_id},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return grant


async def list_capabilities(session: AsyncSession, *, server_id: int) -> Sequence[AgentCapability]:
    result = await session.execute(
        select(AgentCapability)
        .where(AgentCapability.server_id == server_id)
        .order_by(AgentCapability.capability_key)
    )
    return result.scalars().all()


async def update_capability(
    session: AsyncSession,
    *,
    server_id: int,
    capability_key: str,
    enabled: bool,
    notes: str | None,
    actor_user_id: int,
    lab_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> AgentCapability:
    result = await session.execute(
        select(AgentCapability).where(
            AgentCapability.server_id == server_id,
            AgentCapability.capability_key == capability_key,
        )
    )
    cap = result.scalar_one_or_none()
    if cap is None:
        raise ServerError(f"capability {capability_key} not found on server {server_id}")
    if enabled and cap.is_dangerous and (notes is None or len(notes.strip()) < 10):
        raise DangerousCapabilityWithoutNotesError(
            "dangerous capabilities require notes >= 10 chars"
        )
    cap.is_enabled = 1 if enabled else 0
    cap.notes = notes
    cap.updated_by_user_id = actor_user_id
    await session.flush()
    await audit_service.write(
        session,
        action="capability.toggle",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="agent_capability",
        target_id=cap.id,
        target_lab_id=lab_id,
        target_server_id=server_id,
        payload={"capability_key": capability_key, "enabled": enabled, "notes": notes},
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return cap


async def regenerate_enrollment_token(
    session: AsyncSession,
    *,
    server_id: int,
    lab_id: int,
    actor_user_id: int,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, datetime, str, list[int]]:
    """Mint a fresh enrollment token for ``server_id``; expire any prior unused ones.

    Returns ``(plaintext, expires_at, install_snippet, revoked_token_ids)``.
    Prior tokens scoped to this server's hostname
    (``expected_hostname_pattern``) and still unused are flipped to
    expired immediately so a leaked install snippet stops working the
    moment the admin regenerates.
    """
    server = await get_server(session, server_id, lab_id=lab_id)
    if not server.is_active:
        raise ServerError(f"server {server_id} is soft-deleted")

    now = datetime.now(UTC)
    result = await session.execute(
        select(EnrollmentToken).where(
            EnrollmentToken.lab_id == lab_id,
            EnrollmentToken.expected_hostname_pattern == server.hostname,
            EnrollmentToken.used_at.is_(None),
            EnrollmentToken.expires_at > now,
        )
    )
    revoked_token_ids: list[int] = []
    for old in result.scalars().all():
        old.expires_at = now
        revoked_token_ids.append(old.id)

    plaintext, hashed = _generate_token()
    expires_at = now + timedelta(days=7)
    new_token = EnrollmentToken(
        lab_id=lab_id,
        token_hash=hashed,
        expected_hostname_pattern=server.hostname,
        expires_at=expires_at,
        created_by_user_id=actor_user_id,
    )
    session.add(new_token)
    await session.flush()

    snippet = await _build_install_snippet(
        session,
        lab_id=lab_id,
        server_id=server_id,
        plaintext_token=plaintext,
    )

    await audit_service.write(
        session,
        action="server.enrollment_token.regenerate",
        actor_user_id=actor_user_id,
        lab_id=lab_id,
        target_type="server",
        target_id=server_id,
        target_lab_id=lab_id,
        target_server_id=server_id,
        payload={
            "new_token_id": new_token.id,
            "revoked_token_ids": revoked_token_ids,
            "expires_at": expires_at.isoformat(),
        },
        ip_address=request_ip,
        user_agent=user_agent,
    )
    return plaintext, expires_at, snippet, revoked_token_ids


async def list_enrollment_tokens(
    session: AsyncSession, *, lab_id: int
) -> Sequence[EnrollmentToken]:
    result = await session.execute(
        select(EnrollmentToken)
        .where(EnrollmentToken.lab_id == lab_id)
        .order_by(EnrollmentToken.created_at.desc())
    )
    return result.scalars().all()
