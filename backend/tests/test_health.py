"""Health endpoint tests.

``/healthz`` is purely a liveness ping and should always succeed if the
app routes; ``/readyz`` exercises the DB + Redis ping path with the
underlying functions monkey-patched (a real MySQL test container is the
job of Phase 2 integration tests).
"""

from __future__ import annotations

import pytest
from corelab_backend.api import health as health_module
from httpx import AsyncClient


class TestHealthz:
    async def test_liveness_returns_ok(self, async_client: AsyncClient) -> None:
        response = await async_client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestReadyz:
    async def test_all_ok_when_db_passes_and_redis_disabled(
        self, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _db_ok() -> bool:
            return True

        async def _redis_disabled() -> bool | None:
            return None  # not configured

        monkeypatch.setattr(health_module, "ping_db", _db_ok)
        monkeypatch.setattr(health_module, "ping_redis", _redis_disabled)

        response = await async_client.get("/readyz")
        assert response.status_code == 200
        body = response.json()
        assert body == {"status": "ok", "db": "ok", "redis": "disabled"}

    async def test_ok_when_db_passes_and_redis_passes(
        self, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _ok() -> bool:
            return True

        monkeypatch.setattr(health_module, "ping_db", _ok)
        monkeypatch.setattr(health_module, "ping_redis", _ok)

        response = await async_client.get("/readyz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["db"] == "ok"
        assert body["redis"] == "ok"

    async def test_503_when_db_fails(
        self, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _db_dies() -> bool:
            raise RuntimeError("connection refused")

        async def _redis_disabled() -> bool | None:
            return None

        monkeypatch.setattr(health_module, "ping_db", _db_dies)
        monkeypatch.setattr(health_module, "ping_redis", _redis_disabled)

        response = await async_client.get("/readyz")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["db"] == "RuntimeError"

    async def test_503_when_redis_configured_but_fails(
        self, async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _db_ok() -> bool:
            return True

        async def _redis_fail() -> bool | None:
            return False

        monkeypatch.setattr(health_module, "ping_db", _db_ok)
        monkeypatch.setattr(health_module, "ping_redis", _redis_fail)

        response = await async_client.get("/readyz")
        assert response.status_code == 503
        body = response.json()
        assert body["redis"] == "fail"
