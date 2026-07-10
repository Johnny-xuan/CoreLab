"""Backend runtime configuration loaded from environment.

Uses pydantic-settings so values flow from ``$CORELAB_*`` env vars (set by
docker compose via ``deploy/.env``) and validation errors surface at
startup, not on first request. ``Settings`` is a singleton wired via
:func:`get_settings`.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All backend configuration.

    Field naming mirrors the env keys (uppercased + ``CORELAB_`` prefix
    applied by the env_prefix below).
    """

    model_config = SettingsConfigDict(
        env_prefix="CORELAB_",
        env_file=None,  # docker compose injects via environment, not a file
        case_sensitive=False,
        extra="ignore",
    )

    # ─── Storage ──────────────────────────────────────────────────────
    database_url: str = Field(
        # pragma: allowlist nextline secret
        default="mysql+asyncmy://corelab:corelab@localhost:3307/corelab",
        description=(
            "SQLAlchemy async URL for runtime traffic. Should point at the "
            "restricted corelab_app user (no DELETE grants — Phase 2 "
            "invariant #8). Dev defaults to host-mapped MySQL on 3307."
        ),
    )
    migration_database_url: str | None = Field(
        default=None,
        description=(
            "Optional separate SQLAlchemy async URL used by Alembic — should "
            "point at a user with full DDL grants (`corelab`, not "
            "`corelab_app`). Falls back to ``database_url`` if unset (test / "
            "single-credential setups)."
        ),
    )
    redis_url: str | None = Field(
        default=None,
        description="Redis URL (e.g. redis://redis:6379/0). None = no Redis; "
        "rate-limit/nonce fall back to in-process memory (ADR-012).",
    )

    # ─── Security ─────────────────────────────────────────────────────
    jwt_secret: str = Field(
        default="dev-only-jwt-secret-do-not-use-in-prod-32-chars",
        description="Secret for HS256 JWT signing (≥ 32 bytes in prod).",
    )
    jwt_algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm. HS256 only — asymmetric variants are out of scope.",
    )
    jwt_access_ttl_minutes: int = Field(
        default=480,
        description="Access token TTL in minutes (docs/04-security.md §6.1 = 8h).",
    )
    bcrypt_rounds: int = Field(
        default=12,
        description="bcrypt cost factor for user passwords (docs/04-security.md invariant: cost=12).",
    )
    setup_token_ttl_hours: int = Field(
        default=24,
        description="Setup / password-reset token TTL in hours (docs/02-data-model.md §5.3 = 24h).",
    )

    # ─── Public surface ──────────────────────────────────────────────
    backend_public_url: str = Field(
        default="http://localhost",
        description="URL where users reach CoreLab; used in setup links.",
    )
    cors_origins: str = Field(
        default="",
        description="Comma-separated additional CORS origins (frontend dev server).",
    )

    # ─── Logging ──────────────────────────────────────────────────────
    log_level: str = Field(default="INFO", description="DEBUG/INFO/WARNING/ERROR")
    log_json: bool = Field(
        default=True,
        description="Emit structured JSON logs (set false for human-readable dev console).",
    )

    # ─── Scheduler (Phase 6) ─────────────────────────────────────────
    scheduler_enabled: bool = Field(
        default=False,
        description=(
            "Run the APScheduler-backed reservation_tick in this process. Default "
            "off so unit + integration tests do not spawn a background tick; "
            "prod docker compose sets this to true on the backend service."
        ),
    )
    scheduler_tick_seconds: int = Field(
        default=30,
        description=(
            "Interval in seconds between reservation_tick runs (brief P6-3). "
            "Tests can shorten this via env so the scheduler exercises every "
            "transition path quickly."
        ),
    )

    # ─── User WSS (Phase 7) ──────────────────────────────────────────
    ws_user_heartbeat_seconds: int = Field(
        default=30,
        description=(
            "Browser → backend ping interval (docs/05 §4.5). The WS hub "
            "closes 1001 going_away when no client frame arrives inside "
            "2x this window (P7-4). Tests override to a small value so "
            "the timeout fires quickly."
        ),
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance.

    Cached so env is read once at startup; tests can ``get_settings.cache_clear()``.
    """
    return Settings()
