"""Phase 9 — request-scoped middleware (rate-limit / future).

Currently exposes the FU-18 rate-limit middleware and the lock-counter
helpers used by ``/auth/login`` and ``/ssh/challenge`` for the
3-strike 5-minute account lock (docs/04 §4.2 + §5.5).
"""

from __future__ import annotations

from .rate_limit import (
    LoginLockManager,
    RateLimitMiddleware,
    SshChallengeLockManager,
)

__all__ = [
    "LoginLockManager",
    "RateLimitMiddleware",
    "SshChallengeLockManager",
]
