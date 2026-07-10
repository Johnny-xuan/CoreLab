"""Phase 9 C3 — FU-18 rate-limit middleware (P9-6 / P9-7).

Two distinct concerns:

1. **Per-user request budget** — sliding-window counter per
   (user_id, GET|WRITE) or (ip, "anon") tuple. docs/05 §1.8 字面:
   read  -> 600 req/min/user, write -> 60 req/min/user. Excess
   returns ``429 RATE_LIMITED`` with ``Retry-After`` +
   ``X-RateLimit-Limit`` + ``X-RateLimit-Remaining`` headers.

2. **Failure-count account lock** (docs/04 §4.2 + §5.5) — login /
   ssh_challenge accumulate failed attempts per (key); 3 within
   5 minutes locks the key for 5 minutes (return 429 LOCKED).
   The endpoint owns the "what counts as a failure" decision and
   calls :meth:`LoginLockManager.record_failure` /
   ``record_success`` (which clears the counter).

Both code paths use Redis when available and an in-process LRU as
fallback (single-instance dev mode). ADR-012 — Redis is optional.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import uuid4

from fastapi import status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from ..cache import get_redis_client
from ..logging_setup import get_logger
from ..security import decode_access_token

_log = get_logger("corelab.rate_limit")

DEFAULT_READ_PER_MINUTE: Final[int] = 600  # docs/05 §1.8
DEFAULT_WRITE_PER_MINUTE: Final[int] = 60  # docs/05 §1.8
WINDOW_SECONDS: Final[int] = 60

LOGIN_FAILURE_THRESHOLD: Final[int] = 3
LOGIN_LOCK_SECONDS: Final[int] = 300  # 5 min, docs/04 §4.2
LOGIN_FAILURE_WINDOW_SECONDS: Final[int] = 300

# Endpoints exempt from the per-user request budget — login + signup
# self-serve, openapi probe, health, websocket upgrade.
_EXEMPT_PATH_PREFIXES: tuple[str, ...] = (
    "/healthz",
    "/readyz",
    "/openapi.json",
    "/docs",
    "/ws/",
    "/api/v1/auth/login",  # account lock handles bursts via LoginLockManager
)

_WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


# ─── in-process fallback storage ────────────────────────────────────


@dataclass(slots=True)
class _BucketState:
    timestamps_ms: list[int] = field(default_factory=list)


class _InProcessSlidingWindow:
    """Single-instance fallback when Redis is not configured.

    Multi-uvicorn-worker deployments need Redis to be coherent — the
    in-process variant only sees its own worker's traffic. Logged on
    first use so operators are not surprised.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, _BucketState] = {}
        self._locks_until_ms: dict[str, int] = {}
        self._warned = False

    def _warn_once(self) -> None:
        if not self._warned:
            _log.info("rate_limit.in_process_fallback_active")
            self._warned = True

    def _bucket_count_after_hit(self, *, key: str, window_seconds: int) -> int:
        self._warn_once()
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - window_seconds * 1000
        bucket = self._buckets.setdefault(key, _BucketState())
        bucket.timestamps_ms = [ts for ts in bucket.timestamps_ms if ts > cutoff_ms]
        bucket.timestamps_ms.append(now_ms)
        return len(bucket.timestamps_ms)

    def hit(self, *, key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
        count = self._bucket_count_after_hit(key=key, window_seconds=window_seconds)
        return count <= max_requests, max(0, max_requests - count)

    def failure_hit(
        self,
        *,
        count_key: str,
        lock_key: str,
        threshold: int,
        window_seconds: int,
        lock_seconds: int,
    ) -> bool:
        count = self._bucket_count_after_hit(key=count_key, window_seconds=window_seconds)
        if count >= threshold:
            self._locks_until_ms[lock_key] = int(time.time() * 1000) + lock_seconds * 1000
            return True
        return False

    def is_locked(self, key: str) -> bool:
        self._warn_once()
        expires_at = self._locks_until_ms.get(key)
        if expires_at is None:
            return False
        if expires_at <= int(time.time() * 1000):
            self._locks_until_ms.pop(key, None)
            return False
        return True

    def clear_failure(self, *, count_key: str, lock_key: str) -> None:
        self._buckets.pop(count_key, None)
        self._locks_until_ms.pop(lock_key, None)


_in_process = _InProcessSlidingWindow()


async def _sliding_window_hit(
    *,
    key: str,
    max_requests: int,
    window_seconds: int,
) -> tuple[bool, int]:
    """Return (allowed, remaining) for one request against this key."""
    client = get_redis_client()
    if client is None:
        return _in_process.hit(key=key, max_requests=max_requests, window_seconds=window_seconds)
    now_ms = int(time.time() * 1000)
    window_start_ms = now_ms - window_seconds * 1000
    try:
        async with client.pipeline() as pipe:
            pipe.zremrangebyscore(key, 0, window_start_ms)
            pipe.zadd(key, {str(uuid4()): now_ms})
            pipe.zcard(key)
            pipe.expire(key, window_seconds + 60)
            _, _, count, _ = await pipe.execute()
    except Exception as exc:
        _log.warning("rate_limit.redis_error_fallback_inprocess", error=str(exc))
        return _in_process.hit(key=key, max_requests=max_requests, window_seconds=window_seconds)
    allowed = bool(count <= max_requests)
    remaining = max(0, max_requests - int(count))
    return allowed, remaining


# ─── per-user request budget middleware ─────────────────────────────


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply per-user request budget per docs/05 §1.8.

    Identifies the caller by JWT ``sub`` when present, falls back to
    ``X-Forwarded-For`` / ``client.host`` when not (so anonymous
    /auth/* bursts still get capped by IP).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        read_per_minute: int = DEFAULT_READ_PER_MINUTE,
        write_per_minute: int = DEFAULT_WRITE_PER_MINUTE,
    ) -> None:
        super().__init__(app)
        self._read = read_per_minute
        self._write = write_per_minute

    async def dispatch(
        self,
        request: Request,
        call_next: Any,
    ) -> Response:
        path = request.url.path
        if any(path.startswith(p) for p in _EXEMPT_PATH_PREFIXES):
            return await call_next(request)
        identity = _request_identity(request)
        is_write = request.method in _WRITE_METHODS
        limit = self._write if is_write else self._read
        key = f"ratelimit:{identity}:{'w' if is_write else 'r'}"
        allowed, remaining = await _sliding_window_hit(
            key=key, max_requests=limit, window_seconds=WINDOW_SECONDS
        )
        if not allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": {"code": "RATE_LIMITED", "scope": "request_budget"}},
                headers={
                    "Retry-After": str(WINDOW_SECONDS),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response


def _request_identity(request: Request) -> str:
    """Resolve the rate-limit identity for a request.

    Prefers the JWT subject when an ``Authorization: Bearer`` header is
    present (validates lazily; an invalid token falls through to IP).
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[len("Bearer ") :].strip()
        try:
            payload = decode_access_token(token)
        except Exception:
            payload = None
        if payload is not None:
            sub = payload.get("sub")
            if sub is not None:
                return f"user:{sub}"
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    client = request.client
    if client is not None:
        return f"ip:{client.host}"
    return "ip:unknown"


# ─── failure-count account lock ─────────────────────────────────────


class _FailureLockManager:
    """Shared base for login / ssh-challenge lock counters."""

    def __init__(
        self,
        *,
        key_prefix: str,
        threshold: int = LOGIN_FAILURE_THRESHOLD,
        window_seconds: int = LOGIN_FAILURE_WINDOW_SECONDS,
        lock_seconds: int = LOGIN_LOCK_SECONDS,
    ) -> None:
        self._key_prefix = key_prefix
        self._threshold = threshold
        self._window = window_seconds
        self._lock = lock_seconds

    def _count_key(self, subject: str) -> str:
        return f"{self._key_prefix}:fails:{subject}"

    def _lock_key(self, subject: str) -> str:
        return f"{self._key_prefix}:lock:{subject}"

    async def is_locked(self, subject: str) -> bool:
        client = get_redis_client()
        if client is None:
            return _in_process.is_locked(self._lock_key(subject))
        try:
            return await client.exists(self._lock_key(subject)) > 0
        except Exception as exc:
            _log.warning("rate_limit.lock_check_failed", error=str(exc))
            return False

    async def record_failure(self, subject: str) -> bool:
        """Returns True if this failure tripped the lock."""
        client = get_redis_client()
        if client is None:
            return _in_process.failure_hit(
                count_key=self._count_key(subject),
                lock_key=self._lock_key(subject),
                threshold=self._threshold,
                window_seconds=self._window,
                lock_seconds=self._lock,
            )
        try:
            async with client.pipeline() as pipe:
                pipe.incr(self._count_key(subject))
                pipe.expire(self._count_key(subject), self._window)
                result = await pipe.execute()
            count = int(result[0])
            if count >= self._threshold:
                await client.setex(self._lock_key(subject), self._lock, "1")
                return True
        except Exception as exc:
            _log.warning("rate_limit.record_failure_failed", error=str(exc))
        return False

    async def record_success(self, subject: str) -> None:
        """Clear the failure counter after a successful attempt."""
        client = get_redis_client()
        if client is None:
            _in_process.clear_failure(
                count_key=self._count_key(subject),
                lock_key=self._lock_key(subject),
            )
            return
        try:
            await client.delete(self._count_key(subject))
            await client.delete(self._lock_key(subject))
        except Exception as exc:
            _log.warning("rate_limit.record_success_failed", error=str(exc))


class LoginLockManager(_FailureLockManager):
    """Lock the (username, ip) tuple after 3 login failures in 5 min."""

    def __init__(self) -> None:
        super().__init__(key_prefix="rate_limit:login")


class SshChallengeLockManager(_FailureLockManager):
    """Lock the (user_id) after 3 ssh_challenge failures in 5 min
    (docs/04 §5.5)."""

    def __init__(self) -> None:
        super().__init__(key_prefix="rate_limit:ssh_challenge")
