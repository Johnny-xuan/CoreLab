"""Structured logging setup using structlog.

Per planner invariant #5 (Phase 1) + docs/04-security.md §6: sensitive
field names (``password*`` / ``token*`` / ``nonce*`` / ``signature*``) are
redacted from any log record so they cannot leak even if a downstream
caller passes them as kwargs.

JSON output goes to stdout (production); plain console output for dev.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

# Field-name fragments that trigger redaction. Match is substring,
# case-insensitive, on the key — so ``user_password`` / ``ssh_signature`` /
# ``setup_token`` all get caught.
SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "password",
    "token",
    "nonce",
    "signature",
    "secret",
    "private_key",
    "authorization",
)

# Phase 6 FU-24 — reservation script body. A user-supplied script can
# carry secrets (``export AWS_SECRET_ACCESS_KEY=...``) and must not
# appear verbatim in any log or audit row. We can't add ``script`` to
# SENSITIVE_KEY_FRAGMENTS because that would also hit metadata keys
# like ``script_status`` / ``script_started_at`` / ``script_log_path``
# that are valid diagnostics. Use an exact-name set instead, and
# preserve the original string length so audit can prove the column
# was non-empty without leaking content.
SENSITIVE_SCRIPT_KEYS: frozenset[str] = frozenset(
    {
        "script",
        "script_body",
        "request.script",
        "event.script",
    }
)

_REDACTED = "***REDACTED***"


def _redact_script_value(value: object) -> str:
    if isinstance(value, str):
        return f"<REDACTED {len(value)} chars>"
    return _REDACTED


def _redact_sensitive(_logger: object, _method_name: str, event_dict: EventDict) -> EventDict:
    """Replace values whose key matches a sensitive rule.

    Two rules:
    1. exact match against ``SENSITIVE_SCRIPT_KEYS`` (Phase 6 FU-24) →
       length-preserving placeholder.
    2. substring match against ``SENSITIVE_KEY_FRAGMENTS`` (Phase 1+) →
       fixed ``***REDACTED***`` placeholder.
    """
    for key in list(event_dict.keys()):
        lowered = key.lower()
        if lowered in SENSITIVE_SCRIPT_KEYS:
            event_dict[key] = _redact_script_value(event_dict[key])
            continue
        if any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS):
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Install structlog + bridge stdlib logging.

    Idempotent — safe to call multiple times (e.g. tests).
    """
    level_int = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_sensitive,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    final_processor: Processor
    if json_output:
        final_processor = structlog.processors.JSONRenderer()
    else:
        final_processor = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, final_processor],
        wrapper_class=structlog.make_filtering_bound_logger(level_int),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging (uvicorn / sqlalchemy / etc.) into the same
    # output pipeline so we get one stream.
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level_int)
    root.addHandler(handler)
    root.setLevel(level_int)


def get_logger(name: str | None = None) -> Any:
    """Convenience wrapper so callers don't import structlog directly."""
    return structlog.get_logger(name)
