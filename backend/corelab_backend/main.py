"""FastAPI application entry point.

The backend mounts health probes, the v1 REST API, user and agent WebSocket
hubs, rate limiting, background schedulers, and the built SPA fallback.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.agent_ws import router as agent_ws_router
from .api.health import router as health_router
from .api.v1.router import router as v1_router
from .api.ws_user import router as ws_user_router
from .config import get_settings
from .db import get_engine
from .logging_setup import configure_logging, get_logger
from .middleware import RateLimitMiddleware
from .services.lab_url_scheduler import (
    shutdown_scheduler as shutdown_url_scheduler,
)
from .services.lab_url_scheduler import (
    start_scheduler as start_url_scheduler,
)
from .services.reservation_scheduler import shutdown_scheduler, start_scheduler

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """App lifespan: configure logging on startup, dispose engine on shutdown.

    Phase 6 — opt-in APScheduler for the reservation tick. The default
    (``CORELAB_SCHEDULER_ENABLED=false``) keeps unit + integration test
    runs from spawning a background tick; the prod docker-compose
    service should set it to ``true``.
    """
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    log = get_logger("corelab.backend")
    log.info("backend.startup", version=__version__, log_level=settings.log_level)
    scheduler = None
    url_scheduler = None
    if settings.scheduler_enabled:
        scheduler = start_scheduler(tick_seconds=settings.scheduler_tick_seconds)
        # Phase M v5 — URL probe runs on the same opt-in flag as the
        # reservation tick: prod docker-compose has it on, tests off.
        url_scheduler = start_url_scheduler(tick_seconds=60)
    try:
        yield
    finally:
        log.info("backend.shutdown")
        if scheduler is not None:
            await shutdown_scheduler(scheduler)
        if url_scheduler is not None:
            await shutdown_url_scheduler(url_scheduler)
        # Dispose engine if it was created (lazy). Cached singleton always
        # returns the same instance, so .dispose() is safe.
        await get_engine().dispose()


def create_app() -> FastAPI:
    """Factory so tests can build isolated instances."""
    settings = get_settings()
    app = FastAPI(
        title="CoreLab Backend",
        version=__version__,
        description="REST + WebSocket API for CoreLab.",
        lifespan=lifespan,
    )

    # CORS — local frontend dev allows the Vite dev server on :5173 via env.
    if settings.cors_origins_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Phase 9 / FU-18 — per-user request budget.
    app.add_middleware(RateLimitMiddleware)

    # Health probes — mounted at root for compatibility with k8s-style
    # probes and the Caddyfile passthrough.
    app.include_router(health_router)
    app.include_router(v1_router)
    app.include_router(agent_ws_router)
    app.include_router(ws_user_router)

    # SPA fallback: serve static files from backend/static/. The multi-stage
    # Dockerfile copies the built frontend dist/ into this directory. The
    # catch-all route below must be registered AFTER every API/WS router so
    # FastAPI's first-match-wins resolution still hits real endpoints; it
    # only fires for unknown paths, returning index.html so vue-router can
    # take over (history mode → server must serve the SPA on deep-link / F5).
    if STATIC_DIR.exists():
        app.mount(
            "/assets", StaticFiles(directory=STATIC_DIR / "assets", check_dir=False), name="assets"
        )

        # index.html must never be cached: hashed asset names change every
        # build, and a stale index keeps loading the previous bundle.
        index_headers = {"Cache-Control": "no-cache"}

        def _index_response() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html", headers=index_headers)

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            # If the requested path matches a real file under STATIC_DIR
            # (favicon, logo.svg, robots.txt, etc.), serve it directly.
            # Otherwise return index.html so vue-router can take over.
            candidate = STATIC_DIR / full_path
            if full_path and candidate.is_file():
                # Guard against path traversal: candidate must stay inside
                # STATIC_DIR after resolution.
                try:
                    candidate.resolve().relative_to(STATIC_DIR.resolve())
                except ValueError:
                    return _index_response()
                return FileResponse(candidate)
            return _index_response()

    return app


app = create_app()
