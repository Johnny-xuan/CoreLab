"""CoreLab FastAPI backend package.

Phase 1 exposes only the FastAPI application factory and health endpoints.
Subsequent phases add auth, setup wizard, business APIs, agent WS hub,
and scheduler — see ``docs/10-roadmap.md``.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.0.0"
