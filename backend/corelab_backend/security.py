"""Crypto primitives: password hashing, setup-token hashing, JWT issue/verify.

This module owns every place where CoreLab applies cryptography so the
audit surface is minimal. Anything stronger or different (SSH challenge
verify, agent token bcrypt, etc.) gets its own dedicated module in a
later phase.

Invariants (docs/04-security.md):
- Platform user passwords: bcrypt cost=12 (configurable, default 12).
- Setup tokens: SHA-256 hash of plaintext; plaintext returned to caller
  exactly once and never persisted.
- Access tokens: JWT HS256; payload includes user_id, role, lab_id,
  iat, exp; verified with constant-time signature compare via PyJWT.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from .config import get_settings


def hash_password(plaintext: str) -> str:
    """Return a bcrypt hash of ``plaintext`` at the configured cost."""
    settings = get_settings()
    salt = bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    return bcrypt.hashpw(plaintext.encode("utf-8"), salt).decode("ascii")


def verify_password(plaintext: str, password_hash: str) -> bool:
    """Constant-time bcrypt compare. Returns False on any malformed input."""
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def generate_setup_token() -> tuple[str, str]:
    """Generate a fresh setup token. Returns ``(plaintext, sha256_hash_hex)``.

    Plaintext goes into the activation URL exactly once; only the hash
    persists in MySQL (``setup_token.token_hash``).
    """
    plaintext = secrets.token_urlsafe(32)
    return plaintext, hash_setup_token(plaintext)


def hash_setup_token(plaintext: str) -> str:
    """SHA-256(plaintext) lower-hex, the canonical form stored in DB."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def issue_access_token(
    user_id: int,
    lab_id: int,
    role: str,
    *,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Sign and return ``(jwt_string, expires_at_utc)``."""
    settings = get_settings()
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.jwt_access_ttl_minutes)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "lab_id": lab_id,
        "role": role,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "typ": "access",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_at


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify signature + exp. Raises ``jwt.PyJWTError`` subclasses on failure."""
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["exp", "iat", "sub"]},
    )
