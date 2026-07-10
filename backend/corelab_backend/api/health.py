"""Health probes.

Two endpoints, per planner invariant #3 (Phase 1) and 05-api-design §2.17:

- ``/healthz`` — liveness. The process is alive and the FastAPI app is
  routing. No external dependencies are touched, so this stays ok even
  during DB or Redis incidents (which is the point: liveness must not
  trigger restart on transient downstream failures).
- ``/readyz`` — readiness. Real checks on MySQL (SELECT 1) and Redis
  (PING) so an orchestrator can pull this pod out of rotation while
  dependencies recover. Returns 503 if any required dependency fails.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from ..cache import ping_redis
from ..db import ping_db

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe — process is alive and routing."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> JSONResponse:
    """Readiness probe — real DB + Redis check."""
    db_ok = False
    db_error: str | None = None
    try:
        db_ok = await ping_db()
    except Exception as exc:
        db_error = type(exc).__name__

    redis_state = await ping_redis()  # True / False / None (not configured)

    # Required: DB ok. Redis is required only if configured (None = ok).
    overall_ok = db_ok and redis_state is not False
    http_status = status.HTTP_200_OK if overall_ok else status.HTTP_503_SERVICE_UNAVAILABLE

    body: dict[str, str] = {
        "status": "ok" if overall_ok else "degraded",
        "db": "ok" if db_ok else (db_error or "fail"),
        "redis": ("disabled" if redis_state is None else ("ok" if redis_state else "fail")),
    }
    return JSONResponse(content=body, status_code=http_status)
