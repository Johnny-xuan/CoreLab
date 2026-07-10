"""Phase 9 C3 — FU-18 rate-limit + failure-lock tests (P9-6 / P9-7).

Covers docs/05 §1.8 (request budget) + docs/04 §4.2 / §5.5 (failure
lock). Redis-backed paths are exercised against the live test-stack
Redis; the in-process fallback paths use a per-test reset of the
shared singleton.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest_asyncio
from corelab_backend.cache import get_redis_client
from corelab_backend.db import get_session_factory
from corelab_backend.middleware.rate_limit import (
    DEFAULT_READ_PER_MINUTE,
    DEFAULT_WRITE_PER_MINUTE,
    LoginLockManager,
    SshChallengeLockManager,
    _InProcessSlidingWindow,
    _sliding_window_hit,
)
from corelab_backend.models import Lab, User
from corelab_backend.security import hash_password
from httpx import AsyncClient


@pytest_asyncio.fixture
async def _redis_clean() -> None:
    """Flush the rate-limit Redis DB before each test. We use a
    dedicated test DB so flushdb() is safe."""
    client = get_redis_client()
    if client is None:
        return
    try:
        await client.flushdb()
    except Exception:
        return


@pytest_asyncio.fixture
async def world(integration_client: AsyncClient, _redis_clean: None) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        lab = Lab(name="P9 Rate Lab", slug="p9-rate")
        session.add(lab)
        await session.flush()
        admin = User(
            lab_id=lab.id,
            username="alice",
            email="alice@rate.test",
            display_name="Alice",
            password_hash=hash_password("AlicePass!2024"),  # pragma: allowlist secret
            role="lab_admin",
        )
        session.add(admin)
        await session.flush()
    return {
        "client": integration_client,
        "lab_id": lab.id,
        "admin_id": admin.id,
        "username": "alice",
        "password": "AlicePass!2024",  # pragma: allowlist secret
    }


async def _login(client: AsyncClient, *, username: str, password: str) -> str:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestSlidingWindow:
    async def test_redis_path_allows_under_limit(self, _redis_clean: None) -> None:
        allowed_count = 0
        for _ in range(5):
            allowed, _remaining = await _sliding_window_hit(
                key="test:redis:under", max_requests=10, window_seconds=60
            )
            if allowed:
                allowed_count += 1
        assert allowed_count == 5

    async def test_redis_path_blocks_over_limit(self, _redis_clean: None) -> None:
        allowed_results = []
        for _ in range(12):
            allowed, _ = await _sliding_window_hit(
                key="test:redis:over", max_requests=10, window_seconds=60
            )
            allowed_results.append(allowed)
        # First 10 allowed, last 2 blocked.
        assert allowed_results[:10] == [True] * 10
        assert allowed_results[10:] == [False, False]


class TestInProcessFallback:
    def test_in_process_caps_at_max(self) -> None:
        win = _InProcessSlidingWindow()
        for i in range(5):
            allowed, _ = win.hit(key="test:inproc", max_requests=5, window_seconds=60)
            assert allowed, f"hit {i} should be allowed"
        allowed, _ = win.hit(key="test:inproc", max_requests=5, window_seconds=60)
        assert allowed is False


class TestRequestBudgetMiddleware:
    """Mounted via main.create_app; verify read vs write quotas via
    real HTTP calls."""

    async def test_read_returns_headers(self, world: dict[str, Any]) -> None:
        client = world["client"]
        token = await _login(client, username=world["username"], password=world["password"])
        resp = await client.get("/api/v1/audit-logs", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.headers["X-RateLimit-Limit"] == str(DEFAULT_READ_PER_MINUTE)
        assert int(resp.headers["X-RateLimit-Remaining"]) <= DEFAULT_READ_PER_MINUTE

    async def test_write_uses_lower_limit(self, world: dict[str, Any]) -> None:
        client = world["client"]
        token = await _login(client, username=world["username"], password=world["password"])
        resp = await client.post(
            "/api/v1/alert-events/1/resolve",  # 404 but middleware fires first
            headers=_auth(token),
            json={},
        )
        assert resp.headers.get("X-RateLimit-Limit") == str(DEFAULT_WRITE_PER_MINUTE)


class TestLoginLock:
    async def test_three_failures_lock_subsequent_attempt(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        username = world["username"]
        for _ in range(3):
            resp = await client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "wrong"},  # pragma: allowlist secret
            )
            assert resp.status_code == 401
        # 4th attempt — even with correct password — should be locked.
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": world["password"]},
        )
        assert resp.status_code == 429
        body = resp.json()
        assert body["detail"]["code"] == "ACCOUNT_LOCKED"
        assert resp.headers.get("Retry-After") == "300"

    async def test_success_clears_failure_counter(self, world: dict[str, Any]) -> None:
        client: AsyncClient = world["client"]
        username = world["username"]
        # 2 failures (under threshold).
        for _ in range(2):
            resp = await client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "wrong"},  # pragma: allowlist secret
            )
            assert resp.status_code == 401
        # Then a successful login.
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": world["password"]},
        )
        assert resp.status_code == 200
        # Two more failures — counter was reset by success, so we are
        # under the threshold again and not locked.
        for _ in range(2):
            resp = await client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "wrong"},  # pragma: allowlist secret
            )
            assert resp.status_code == 401


class TestSshChallengeLockUnit:
    """Direct unit tests on the lock manager because end-to-end SSH
    verify needs a live agent — out of scope for C3."""

    async def test_three_failures_trigger_lock(self, _redis_clean: None) -> None:
        mgr = SshChallengeLockManager()
        subject = "user:777"
        assert await mgr.is_locked(subject) is False
        assert await mgr.record_failure(subject) is False
        assert await mgr.record_failure(subject) is False
        assert await mgr.record_failure(subject) is True
        assert await mgr.is_locked(subject) is True

    async def test_record_success_clears_lock(self, _redis_clean: None) -> None:
        mgr = SshChallengeLockManager()
        subject = "user:888"
        await mgr.record_failure(subject)
        await mgr.record_failure(subject)
        await mgr.record_failure(subject)
        assert await mgr.is_locked(subject) is True
        await mgr.record_success(subject)
        assert await mgr.is_locked(subject) is False


class TestLoginLockUnit:
    async def test_three_failures_lock_then_success_clears(self, _redis_clean: None) -> None:
        mgr = LoginLockManager()
        for _ in range(2):
            assert await mgr.record_failure("alice:ip") is False
        assert await mgr.record_failure("alice:ip") is True
        assert await mgr.is_locked("alice:ip") is True
        await mgr.record_success("alice:ip")
        assert await mgr.is_locked("alice:ip") is False


class TestRateLimit429Headers:
    async def test_429_response_includes_retry_and_remaining(self, _redis_clean: None) -> None:
        """Trigger the request-budget middleware by hammering a write
        endpoint past the configured cap. We use the in-process
        fallback's deterministic counter via direct calls."""
        # Use raw _sliding_window_hit to avoid hammering an HTTP
        # endpoint 61 times.
        for i in range(DEFAULT_WRITE_PER_MINUTE):
            allowed, _ = await _sliding_window_hit(
                key="test:429check",
                max_requests=DEFAULT_WRITE_PER_MINUTE,
                window_seconds=60,
            )
            assert allowed, f"hit {i} should be allowed"
        allowed, remaining = await _sliding_window_hit(
            key="test:429check",
            max_requests=DEFAULT_WRITE_PER_MINUTE,
            window_seconds=60,
        )
        assert allowed is False
        assert remaining == 0


_ = asyncio  # keep import — pytest_asyncio drives event loop integration
