"""Optional Redis client.

ADR-012: Redis is not strictly required for v1; rate-limit / nonce can
fall back to in-process memory. We keep the dependency listed (it's tiny)
but tolerate ``CORELAB_REDIS_URL`` being unset and report Redis as
"disabled" in /readyz instead of an error.
"""

from __future__ import annotations

from functools import lru_cache

import redis.asyncio as aioredis

from .config import get_settings


@lru_cache(maxsize=1)
def get_redis_client() -> aioredis.Redis | None:
    """Return a shared async Redis client, or None if not configured."""
    settings = get_settings()
    if not settings.redis_url:
        return None
    return aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )


async def ping_redis() -> bool | None:
    """Ping Redis.

    Returns:
        True  - configured and reachable
        False - configured but unreachable (treated as readyz failure)
        None  - not configured (graceful degradation, /readyz reports "disabled")
    """
    client = get_redis_client()
    if client is None:
        return None
    try:
        pong = await client.ping()
    except Exception:
        return False
    return bool(pong)
