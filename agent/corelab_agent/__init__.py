"""CoreLab on-server agent package.

Phase 1 ships the WSS reconnect loop, structured logging, TOML config
loading, and mock mode for non-Linux dev. RPC handlers / telemetry
collector / compliance monitor land in subsequent phases.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.0.0"
