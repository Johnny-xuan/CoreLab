"""Agent-side structured logging.

Independent of backend's logging_setup so the agent can ship without
backend deps. Same secret-redaction policy (planner invariant #5):
any field whose key contains ``password / token / nonce / signature /
secret / private_key / authorization`` is replaced with a redacted
sentinel before serialization.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "password",
    "token",
    "nonce",
    "signature",
    "secret",
    "private_key",
    "authorization",
)

# Phase 6 FU-24 — same exact-name policy as backend logging_setup so a
# reservation script body never appears verbatim in the agent log
# either. Length-preserving so the agent operations log can still prove
# whether the column was non-empty.
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
    for key in list(event_dict.keys()):
        lowered = key.lower()
        if lowered in SENSITIVE_SCRIPT_KEYS:
            event_dict[key] = _redact_script_value(event_dict[key])
            continue
        if any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS):
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    level_int = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_sensitive,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    final: Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[*shared, final],
        wrapper_class=structlog.make_filtering_bound_logger(level_int),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Agent logs to stderr (stdout is reserved for any future child-proc
    # stream forwarding, e.g. script.output_chunk).
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level_int)
    root.addHandler(handler)
    root.setLevel(level_int)


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
