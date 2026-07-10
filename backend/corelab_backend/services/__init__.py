"""Service layer — pure business logic that owns its own SQLAlchemy session.

API routers stay thin: parse pydantic, call service, return pydantic.
All writes that mutate user-visible state must also push an audit_log
row via :mod:`corelab_backend.services.audit_service`.
"""

from __future__ import annotations
