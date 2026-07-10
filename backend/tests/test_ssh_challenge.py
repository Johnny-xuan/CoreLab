"""Unit tests for ``ssh_challenge`` — single-use nonces + actor binding.

Uses a tiny in-memory fake Redis instead of spinning up a real instance
because the surface we care about is just ``set`` (with ``ex`` TTL) and
``getdel``. The real client is exercised end-to-end by the Phase 4 C10
integration suite.
"""

from __future__ import annotations

from typing import Any

import pytest
from corelab_backend.services import ssh_challenge


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        del ex  # we don't simulate time passing; tests force TTL manually
        self.store[key] = value

    async def getdel(self, key: str) -> str | None:
        return self.store.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()

    def _get_redis_client() -> Any:
        return fake

    monkeypatch.setattr(ssh_challenge, "get_redis_client", _get_redis_client)
    return fake


async def test_mint_returns_challenge_with_signed_command(fake_redis: FakeRedis) -> None:
    issued = await ssh_challenge.mint(
        user_id=42, server_id=7, physical_account_id=3, ssh_public_key_id=11
    )
    assert issued.nonce
    assert len(issued.nonce) >= 32
    assert issued.signing_namespace == "corelab"
    assert "ssh-keygen -Y sign" in issued.sign_command
    assert issued.nonce in issued.sign_command
    assert "ssh-challenge:" + issued.challenge_id in fake_redis.store


async def test_consume_returns_context_and_deletes(fake_redis: FakeRedis) -> None:
    issued = await ssh_challenge.mint(
        user_id=42, server_id=7, physical_account_id=3, ssh_public_key_id=11
    )
    ctx = await ssh_challenge.consume(issued.challenge_id, actor_user_id=42)
    assert ctx.user_id == 42
    assert ctx.server_id == 7
    assert ctx.physical_account_id == 3
    assert ctx.nonce == issued.nonce
    # Single-use: a second consume must fail.
    with pytest.raises(ssh_challenge.ChallengeExpiredOrUsedError):
        await ssh_challenge.consume(issued.challenge_id, actor_user_id=42)


async def test_consume_actor_mismatch_burns_challenge(fake_redis: FakeRedis) -> None:
    """Invariant #2: even a failed verify must burn the nonce."""
    issued = await ssh_challenge.mint(
        user_id=42, server_id=7, physical_account_id=3, ssh_public_key_id=11
    )
    with pytest.raises(ssh_challenge.ActorMismatchError):
        await ssh_challenge.consume(issued.challenge_id, actor_user_id=99)
    # The legitimate owner now finds the challenge already consumed.
    with pytest.raises(ssh_challenge.ChallengeExpiredOrUsedError):
        await ssh_challenge.consume(issued.challenge_id, actor_user_id=42)


async def test_consume_expired_or_used_raises(fake_redis: FakeRedis) -> None:
    with pytest.raises(ssh_challenge.ChallengeExpiredOrUsedError):
        await ssh_challenge.consume("never-issued-id", actor_user_id=42)


async def test_mint_requires_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_challenge, "get_redis_client", lambda: None)
    with pytest.raises(ssh_challenge.RedisUnavailableError):
        await ssh_challenge.mint(user_id=1, server_id=1, physical_account_id=1, ssh_public_key_id=1)
