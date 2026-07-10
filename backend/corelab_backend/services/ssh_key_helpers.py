"""Pure helpers for parsing + fingerprinting SSH public keys.

Computing ``SHA256:<base64>`` ourselves matches ``ssh-keygen -l -f``
output and avoids shelling out from the request path.
"""

from __future__ import annotations

import base64
import binascii
import hashlib

ALLOWED_KEY_TYPES = frozenset(
    {
        "ssh-ed25519",
        "ssh-rsa",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "sk-ssh-ed25519@openssh.com",
        "sk-ecdsa-sha2-nistp256@openssh.com",
    }
)


class InvalidPublicKeyError(ValueError):
    """Raised when a supplied public key is malformed or unsupported."""


def parse_public_key(raw: str) -> tuple[str, str, str | None]:
    """Return ``(key_type, base64_blob, comment_or_None)`` from a one-line key.

    Format: ``<type> <base64-blob> [comment]``.
    """
    cleaned = raw.strip()
    if "\n" in cleaned or "\r" in cleaned:
        raise InvalidPublicKeyError("public key must be a single line")
    parts = cleaned.split(maxsplit=2)
    if len(parts) < 2:
        raise InvalidPublicKeyError("public key needs at least <type> <blob>")
    key_type, blob = parts[0], parts[1]
    comment = parts[2] if len(parts) == 3 else None
    if key_type not in ALLOWED_KEY_TYPES:
        raise InvalidPublicKeyError(f"unsupported key type: {key_type}")
    try:
        base64.b64decode(blob, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidPublicKeyError("public key blob is not valid base64") from exc
    return key_type, blob, comment


def fingerprint_sha256(key_blob_b64: str) -> str:
    """``SHA256:<unpadded-base64>`` of the binary key, like ``ssh-keygen -l -f``."""
    raw = base64.b64decode(key_blob_b64, validate=True)
    digest = hashlib.sha256(raw).digest()
    return "SHA256:" + base64.b64encode(digest).rstrip(b"=").decode("ascii")
