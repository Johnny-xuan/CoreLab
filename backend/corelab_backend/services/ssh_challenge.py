"""SSH challenge-response — mint / consume nonces in Redis.

End of the security model described in docs/04-security.md §5 — the
backend never trusts a user's claim that they own a Linux account
without first asking them to ``ssh-keygen -Y sign`` a random nonce
and then having the agent verify that signature *against the server's
real authorized_keys file*. The signature verification subprocess
lives on the agent side (see Phase 4 C5); this module only handles
the backend bookkeeping: mint, store with TTL, consume-once.

Invariant references:
- #2 nonce 32 bytes random + Redis TTL 300s + DELETE on use (single-use)
- #3 actor binding — the consumed context records who *requested* the
  challenge so the verify endpoint can refuse mismatched actors
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..cache import get_redis_client

SIGNING_NAMESPACE = "corelab"
NONCE_BYTES = 32
TTL_SECONDS = 300
_REDIS_PREFIX = "ssh-challenge:"


class ChallengeError(Exception):
    pass


class RedisUnavailableError(ChallengeError):
    """Redis is required for SSH challenges; degraded mode cannot mint nonces."""


class ChallengeExpiredOrUsedError(ChallengeError):
    """Challenge id not in Redis — either TTL'd out or consumed."""


class ActorMismatchError(ChallengeError):
    """The verify caller is not the user who minted the challenge (invariant #3)."""


@dataclass(frozen=True, slots=True)
class ChallengeContext:
    user_id: int
    server_id: int
    physical_account_id: int
    ssh_public_key_id: int
    nonce: str
    challenge_id: str


@dataclass(frozen=True, slots=True)
class IssuedChallenge:
    challenge_id: str
    nonce: str
    expires_at: datetime
    sign_command: str
    signing_namespace: str = SIGNING_NAMESPACE


def _sign_command(nonce: str) -> str:
    """Render the copy-paste command the user runs in their local terminal."""
    return f'echo "{nonce}" | ssh-keygen -Y sign -n {SIGNING_NAMESPACE} -f ~/.ssh/id_ed25519'


async def mint(
    *,
    user_id: int,
    server_id: int,
    physical_account_id: int,
    ssh_public_key_id: int,
) -> IssuedChallenge:
    """Create a single-use challenge bound to (user, server, pa, key)."""
    client = get_redis_client()
    if client is None:
        raise RedisUnavailableError(
            "CORELAB_REDIS_URL not configured; SSH challenges require Redis"
        )
    nonce = secrets.token_urlsafe(NONCE_BYTES)
    challenge_id = secrets.token_urlsafe(16)
    expires_at = datetime.now(UTC) + timedelta(seconds=TTL_SECONDS)
    payload = json.dumps(
        {
            "user_id": user_id,
            "server_id": server_id,
            "physical_account_id": physical_account_id,
            "ssh_public_key_id": ssh_public_key_id,
            "nonce": nonce,
        }
    )
    await client.set(_REDIS_PREFIX + challenge_id, payload, ex=TTL_SECONDS)
    return IssuedChallenge(
        challenge_id=challenge_id,
        nonce=nonce,
        expires_at=expires_at,
        sign_command=_sign_command(nonce),
    )


async def consume(challenge_id: str, *, actor_user_id: int) -> ChallengeContext:
    """Read + DELETE the challenge atomically; enforce actor binding.

    The DELETE is unconditional even when the actor doesn't match —
    nonces are single-use regardless of who tried to spend them, so a
    failed verify also burns the challenge. This mirrors invariant #2.
    """
    client = get_redis_client()
    if client is None:
        raise RedisUnavailableError("Redis not configured")
    key = _REDIS_PREFIX + challenge_id
    # GETDEL is one round-trip atomic in Redis 6.2+.
    raw = await client.getdel(key)
    if raw is None:
        raise ChallengeExpiredOrUsedError("challenge expired or already used")
    data = json.loads(raw)
    if data["user_id"] != actor_user_id:
        raise ActorMismatchError("verify actor does not match challenge requester")
    return ChallengeContext(
        user_id=data["user_id"],
        server_id=data["server_id"],
        physical_account_id=data["physical_account_id"],
        ssh_public_key_id=data["ssh_public_key_id"],
        nonce=data["nonce"],
        challenge_id=challenge_id,
    )
