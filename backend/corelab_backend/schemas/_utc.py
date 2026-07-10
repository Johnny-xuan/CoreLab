"""Shared Pydantic helper — emit UTC datetimes as ISO with ``Z`` suffix.

Why this exists: MySQL ``TIMESTAMP`` columns come back from the driver as
naive ``datetime`` instances even though we store UTC. Pydantic's default
serializer turns a naive datetime into ``"2026-06-07T14:00:00"`` (no
suffix). The browser's ``new Date(s)`` parses that string in **local
time**, not UTC — so a reservation stored at 14:00 UTC ends up
rendered at 14:00 local (off by the user's TZ offset).

``UtcDatetime`` re-attaches UTC to naive values and forces a ``Z``
suffix on the JSON wire format. Use it for response-shaped datetime
fields. Input fields can keep the plain ``datetime`` type — pydantic
parses ``...Z`` and ``...+00:00`` into aware datetimes either way.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def _to_utc_z(v: datetime) -> str:
    v = v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
    return v.isoformat().replace("+00:00", "Z")


UtcDatetime = Annotated[
    datetime,
    PlainSerializer(_to_utc_z, return_type=str, when_used="json"),
]
