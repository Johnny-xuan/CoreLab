"""Phase 8 C7 — SERIALIZABLE deadlock retry middleware (FU-31 / P8-16).

Wraps an awaitable factory and retries on InnoDB deadlock (MySQL
error 1213) with exponential backoff. Used by the scheduler tick to
survive concurrent transactions that touch the same reservation rows
at SERIALIZABLE isolation.

Backoff schedule: ``backoff_base_s * (2 ** attempt)`` — 50/100/200/
400/800ms with defaults, total ~1.55s for the full 5-attempt budget.

Non-deadlock OperationalErrors propagate immediately — the middleware
only knows how to recover from MySQL's deadlock signal.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError

from ..logging_setup import get_logger

_log = get_logger("corelab.serializable_retry")

T = TypeVar("T")


def _is_deadlock(exc: BaseException) -> bool:
    """MySQL InnoDB deadlock signal — error code 1213 or the canonical
    string "Deadlock found when trying to get lock"."""
    msg = str(exc)
    return "1213" in msg or "Deadlock found" in msg


async def with_serializable_retry(
    coro_factory: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 5,
    backoff_base_s: float = 0.05,
) -> T:
    """Run ``coro_factory()`` and retry on InnoDB deadlock.

    Each retry calls ``coro_factory()`` fresh — the caller is
    responsible for opening + closing the session each time so a
    poisoned transaction state from the prior attempt doesn't leak.
    Raises the last :class:`~sqlalchemy.exc.OperationalError` once
    ``max_retries`` is exhausted.
    """
    last_exc: OperationalError | None = None
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except OperationalError as exc:
            if not _is_deadlock(exc):
                raise
            last_exc = exc
            if attempt == max_retries - 1:
                _log.error(
                    "serializable.retry.exhausted",
                    attempts=max_retries,
                    error=str(exc),
                )
                raise
            sleep_s = backoff_base_s * (2**attempt)
            _log.info(
                "serializable.retry.deadlock",
                attempt=attempt + 1,
                sleep_s=sleep_s,
            )
            await asyncio.sleep(sleep_s)
    # Unreachable — either we returned or we raised. The assertion is
    # to satisfy mypy on the implicit "fell off the loop" branch.
    assert last_exc is not None
    raise last_exc
