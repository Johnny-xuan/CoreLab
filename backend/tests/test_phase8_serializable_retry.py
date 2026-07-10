"""Phase 8 C7 — SERIALIZABLE retry middleware tests (FU-31 / P8-16).

* Non-deadlock OperationalError propagates immediately (1 attempt)
* Single deadlock retried + succeeds on attempt 2
* Persistent deadlock exhausts after max_retries with the last
  exception re-raised
* reservation_tick is wrapped (grep verification — no flaky concurrent
  setup needed; tick logic itself is exercised by Phase 7 tests)
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest
from corelab_backend.services._serializable_retry import with_serializable_retry
from sqlalchemy.exc import OperationalError


def _deadlock_exc() -> OperationalError:
    return OperationalError(
        statement="SELECT 1",
        params=None,
        orig=Exception("(1213, 'Deadlock found when trying to get lock')"),
    )


def _other_op_exc() -> OperationalError:
    return OperationalError(
        statement="SELECT 1",
        params=None,
        orig=Exception("(1042, 'Can't get hostname')"),
    )


async def test_non_deadlock_propagates_immediately() -> None:
    factory = AsyncMock(side_effect=_other_op_exc())
    with pytest.raises(OperationalError) as info:
        await with_serializable_retry(factory, max_retries=5)
    assert "1042" in str(info.value)
    factory.assert_called_once()


async def test_first_attempt_deadlocks_then_succeeds() -> None:
    """One deadlock followed by a success returns the second call's value."""
    call_count = 0

    async def factory() -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _deadlock_exc()
        return "ok"

    result = await with_serializable_retry(factory, max_retries=5, backoff_base_s=0.001)
    assert result == "ok"
    assert call_count == 2


async def test_exhausts_after_max_retries() -> None:
    factory = AsyncMock(side_effect=_deadlock_exc())
    with pytest.raises(OperationalError) as info:
        await with_serializable_retry(factory, max_retries=3, backoff_base_s=0.001)
    assert "1213" in str(info.value)
    assert factory.call_count == 3


async def test_backoff_grows_exponentially(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each retry should sleep base * 2**attempt seconds."""
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr("corelab_backend.services._serializable_retry.asyncio.sleep", fake_sleep)
    factory = AsyncMock(side_effect=_deadlock_exc())
    with pytest.raises(OperationalError):
        await with_serializable_retry(factory, max_retries=4, backoff_base_s=0.05)
    # 4 total attempts → 3 sleeps (no sleep after the final failed attempt).
    assert sleeps == [pytest.approx(0.05), pytest.approx(0.10), pytest.approx(0.20)]


def test_reservation_tick_wraps_with_serializable_retry() -> None:
    """Grep guard — P8-16 invariant: the scheduler tick must call the
    middleware. Catches refactors that break the wiring without going
    through this test."""
    from corelab_backend.services import reservation_scheduler

    src = inspect.getsource(reservation_scheduler.reservation_tick)
    assert "with_serializable_retry" in src
