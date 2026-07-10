"""Phase 7 C4 — /usage/me API + usage_service tests (P7-9/10/11).

Covers:
* response schema (8 fields per docs/05 §3.15).
* gpu_hours formula — only ``active`` / ``completed`` count, with the
  month boundary double-truncation (LEAST / GREATEST).
* month UTC boundary — a reservation that spans two months is counted
  in each month only for its in-window portion.
* completion_rate — completed / (completed + failed + cancelled),
  with terminal rows only in the denominator.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from corelab_backend.db import get_session_factory
from corelab_backend.models import (
    AccountLink,
    Gpu,
    Lab,
    Notification,
    PhysicalAccount,
    Reservation,
    Server,
    User,
)
from corelab_backend.security import hash_password
from corelab_backend.services import (
    account_link_service as als,
)
from corelab_backend.services import (
    usage_service,
)
from httpx import AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def seeded(integration_client: AsyncClient) -> dict[str, Any]:
    del integration_client
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="Phase7 Usage Lab", slug="phase7-usage")
        session.add(lab)
        await session.flush()
        frank = User(
            lab_id=lab.id,
            username="frank",
            email="frank@usage.test",
            display_name="Frank",
            password_hash=hash_password("FrankPass!2024"),  # pragma: allowlist secret
            role="user",
        )
        other = User(
            lab_id=lab.id,
            username="usage_other",
            email="usage-other@usage.test",
            display_name="Usage Other",
            password_hash=hash_password("OtherPass!2024"),  # pragma: allowlist secret
            role="user",
        )
        session.add_all([frank, other])
        await session.flush()
        server = Server(
            lab_id=lab.id,
            hostname="gpu-u1.usage",
            display_name="Usage GPU 1",
            status="online",
            created_by_user_id=frank.id,
            max_reservation_hours=240,
        )
        session.add(server)
        await session.flush()
        gpu = Gpu(server_id=server.id, gpu_index=0, model="RTX 4090", memory_total_mb=24576)
        session.add(gpu)
        await session.flush()
        pa = PhysicalAccount(
            server_id=server.id,
            linux_username="frank_lab",
            uid=1100,
            source="admin_manual_register",
            created_by_user_id=frank.id,
        )
        session.add(pa)
        await session.flush()
        await als.establish_via_ssh_challenge(
            session,
            user_id=frank.id,
            physical_account_id=pa.id,
            challenge_id="chal-frank",
            signer_fingerprint="SHA256:frank",
            lab_id=lab.id,
            server_id=server.id,
        )

    factory2 = get_session_factory()
    async with factory2() as session:
        link = (
            (
                await session.execute(
                    select(AccountLink).where(AccountLink.user_id == frank.id).limit(1)
                )
            )
            .scalars()
            .one()
        )
    return {
        "lab_id": lab.id,
        "frank_id": frank.id,
        "other_user_id": other.id,
        "server_id": server.id,
        "gpu_id": gpu.id,
        "pa_id": pa.id,
        "link_id": link.id,
        "server_hostname": server.hostname,
        "pa_linux_username": pa.linux_username,
    }


async def _insert(
    seeded: dict[str, Any],
    *,
    start_at: datetime,
    end_at: datetime,
    status: str,
) -> int:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = Reservation(
            user_id=seeded["frank_id"],
            server_id=seeded["server_id"],
            gpu_id=seeded["gpu_id"],
            account_link_id=seeded["link_id"],
            start_at=start_at,
            end_at=end_at,
            status=status,
        )
        session.add(row)
        await session.flush()
        return row.id


# ─── unit-level service tests ────────────────────────────────────────


async def test_gpu_hours_only_active_and_completed(seeded: dict[str, Any]) -> None:
    """cancelled + failed + scheduled rows must NOT count towards gpu_hours."""
    base_month_start = datetime(2026, 6, 1, tzinfo=UTC)
    # 1h completed
    await _insert(
        seeded,
        start_at=base_month_start + timedelta(days=2, hours=10),
        end_at=base_month_start + timedelta(days=2, hours=11),
        status="completed",
    )
    # 2h cancelled — must NOT be counted
    await _insert(
        seeded,
        start_at=base_month_start + timedelta(days=3, hours=10),
        end_at=base_month_start + timedelta(days=3, hours=12),
        status="cancelled",
    )
    # 3h failed — must NOT be counted
    await _insert(
        seeded,
        start_at=base_month_start + timedelta(days=4, hours=10),
        end_at=base_month_start + timedelta(days=4, hours=13),
        status="failed",
    )
    # 4h scheduled future — must NOT be counted
    await _insert(
        seeded,
        start_at=base_month_start + timedelta(days=25, hours=10),
        end_at=base_month_start + timedelta(days=25, hours=14),
        status="scheduled",
    )
    factory = get_session_factory()
    async with factory() as session:
        out = await usage_service.monthly_usage(
            session,
            user_id=seeded["frank_id"],
            month="2026-06",
            now=base_month_start + timedelta(days=28),
        )
    assert out["gpu_hours_used"] == pytest.approx(1.0, abs=0.01)


async def test_usage_month_boundary_double_truncates_cross_month_row(
    seeded: dict[str, Any],
) -> None:
    """A row 2026-05-30 → 2026-06-05 (6 days) counted into June only its
    in-window slice (~4 days)."""
    start_at = datetime(2026, 5, 30, 0, 0, tzinfo=UTC)
    end_at = datetime(2026, 6, 5, 0, 0, tzinfo=UTC)
    await _insert(seeded, start_at=start_at, end_at=end_at, status="completed")
    factory = get_session_factory()
    async with factory() as session:
        out = await usage_service.monthly_usage(
            session,
            user_id=seeded["frank_id"],
            month="2026-06",
            now=datetime(2026, 6, 20, tzinfo=UTC),
        )
    # 4 days = 96 hours; allow ~0.1h tolerance for TIMESTAMPDIFF
    # boundary rounding.
    assert out["gpu_hours_used"] == pytest.approx(96.0, abs=0.1)


async def test_completion_rate_denominator_only_terminal(
    seeded: dict[str, Any],
) -> None:
    """4 completed + 1 failed + 1 cancelled → 4/(4+1+1) ≈ 0.6667. Active /
    scheduled rows must NOT influence the ratio."""
    base = datetime(2026, 6, 5, tzinfo=UTC)
    for hour in range(4):
        await _insert(
            seeded,
            start_at=base + timedelta(hours=hour),
            end_at=base + timedelta(hours=hour, minutes=30),
            status="completed",
        )
    await _insert(
        seeded,
        start_at=base + timedelta(days=1),
        end_at=base + timedelta(days=1, hours=1),
        status="failed",
    )
    await _insert(
        seeded,
        start_at=base + timedelta(days=2),
        end_at=base + timedelta(days=2, hours=1),
        status="cancelled",
    )
    # Active (in-flight) should NOT change the rate.
    await _insert(
        seeded,
        start_at=base + timedelta(days=3),
        end_at=base + timedelta(days=3, hours=2),
        status="active",
    )
    factory = get_session_factory()
    async with factory() as session:
        out = await usage_service.monthly_usage(
            session,
            user_id=seeded["frank_id"],
            month="2026-06",
            now=datetime(2026, 6, 20, tzinfo=UTC),
        )
    assert out["completion_rate"] == pytest.approx(4 / 6, abs=0.001)


async def test_usage_counts_governance_events(seeded: dict[str, Any]) -> None:
    """Alert and compliance counters should reflect user-facing notifications."""
    in_month = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    outside_month = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    factory = get_session_factory()
    async with factory() as session, session.begin():
        session.add_all(
            [
                Notification(
                    recipient_user_id=seeded["frank_id"],
                    type="compliance.foreign_process",
                    severity="warn",
                    title="Compliance",
                    created_at=in_month,
                ),
                Notification(
                    recipient_user_id=seeded["frank_id"],
                    type="reservation.started",
                    severity="info",
                    title="Reservation",
                    created_at=in_month,
                ),
                Notification(
                    recipient_user_id=seeded["frank_id"],
                    type="compliance.next_month",
                    severity="warn",
                    title="Compliance",
                    created_at=outside_month,
                ),
                Notification(
                    recipient_user_id=seeded["other_user_id"],
                    type="compliance.other_user",
                    severity="warn",
                    title="Compliance",
                    created_at=in_month,
                ),
            ]
        )

    async with factory() as session:
        out = await usage_service.monthly_usage(
            session,
            user_id=seeded["frank_id"],
            month="2026-06",
            now=datetime(2026, 6, 20, tzinfo=UTC),
        )

    assert out["alerts_received"] == 1
    assert out["compliance_violations"] == 1


async def test_usage_response_schema_8_fields(
    integration_client: AsyncClient,
    seeded: dict[str, Any],
) -> None:
    """GET /api/v1/usage/me?month=YYYY-MM returns the 8 docs/05 §3.15 fields."""
    # Frank logs in (real bcrypt verify path).
    login = await integration_client.post(
        "/api/v1/auth/login",
        json={"username": "frank", "password": "FrankPass!2024"},  # pragma: allowlist secret
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    resp = await integration_client.get(
        "/api/v1/usage/me?month=2026-06",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    expected_keys = {
        "month",
        "gpu_hours_used",
        "completion_rate",
        "reservation_count",
        "by_server",
        "by_pa",
        "alerts_received",
        "compliance_violations",
    }
    assert set(body.keys()) == expected_keys
    assert body["alerts_received"] == 0
    assert body["compliance_violations"] == 0


async def test_usage_invalid_month_returns_422(
    integration_client: AsyncClient,
    seeded: dict[str, Any],
) -> None:
    login = await integration_client.post(
        "/api/v1/auth/login",
        json={"username": "frank", "password": "FrankPass!2024"},  # pragma: allowlist secret
    )
    token = login.json()["access_token"]
    resp = await integration_client.get(
        "/api/v1/usage/me?month=2026-13",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_MONTH"
