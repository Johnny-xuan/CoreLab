"""``/api/v1/install/*`` — Phase M M-1.2 stranger-first agent bootstrap.

Two unauthenticated read endpoints powering the one-line ``curl | bash``
install snippet emitted on server enrollment:

* ``GET /install/agent.sh``    — the installer bash script itself.
* ``GET /install/agent.tar.gz`` — the agent + protocol source tarball
  the script downloads, extracts to ``/opt/corelab-agent``, and pip
  installs into a venv.

Both endpoints are public-readable on purpose: the security boundary is
the ``CORELAB_ENROLLMENT_TOKEN`` the snippet injects into the script's
env, not the script's URL. Anybody downloading the script or tarball
without a token can't talk to the backend; the agent's first WSS frame
will be rejected.

Tarball assembly is cached in memory after the first request (it
walks ``agent/`` and ``shared/protocol/`` from the workspace root, which
is the same path in dev (``corelab/``) and in the container
(``/app/``) thanks to how the Dockerfile lays the workspace out).
"""

from __future__ import annotations

import io
import tarfile
import threading
from pathlib import Path

from anyio import Path as AsyncPath
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response

router = APIRouter(prefix="/install", tags=["install"])

# Workspace root: parents = [v1, api, corelab_backend, backend, <root>].
# Resolves to ``corelab/`` in dev and ``/app/`` in the container — both
# layouts put ``agent/`` and ``shared/protocol/`` directly under the
# workspace root.
_WORKSPACE_ROOT: Path = Path(__file__).resolve().parents[4]
_INSTALL_SCRIPT: Path = _WORKSPACE_ROOT / "deploy" / "install-agent.sh"
_AGENT_DIR: Path = _WORKSPACE_ROOT / "agent"
_PROTOCOL_DIR: Path = _WORKSPACE_ROOT / "shared" / "protocol"

# Cache for the tarball bytes. Build once, serve many.
_tarball_lock = threading.Lock()
_tarball_cache: bytes | None = None


def _build_tarball() -> bytes:
    """Pack ``agent/`` and ``shared/protocol/`` into a gzipped tar.

    Layout inside the archive matches what ``install-agent.sh`` expects
    when extracted to ``$INSTALL_DIR``:

      <install_dir>/
        agent/
          pyproject.toml
          corelab_agent/...
        shared/
          protocol/
            pyproject.toml
            corelab_protocol/...

    Caller-supplied filter strips compiled / cache directories that
    bloat the tarball without contributing to a working install.
    """
    if not _AGENT_DIR.is_dir():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"agent source missing at {_AGENT_DIR}",
        )
    if not _PROTOCOL_DIR.is_dir():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"protocol source missing at {_PROTOCOL_DIR}",
        )

    excluded_dir_names = {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "dist",
        "build",
        ".egg-info",
        "tests",  # tests are not needed at runtime; shaves install size
    }
    excluded_suffixes = {".pyc", ".pyo"}

    def _filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
        p = Path(tarinfo.name)
        if any(part in excluded_dir_names for part in p.parts):
            return None
        if p.suffix in excluded_suffixes:
            return None
        # Normalise ownership — the tarball ships under any UID/GID.
        tarinfo.uid = 0
        tarinfo.gid = 0
        tarinfo.uname = "root"
        tarinfo.gname = "root"
        return tarinfo

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        tar.add(_AGENT_DIR, arcname="agent", filter=_filter)
        tar.add(_PROTOCOL_DIR, arcname="shared/protocol", filter=_filter)
    return buf.getvalue()


@router.get("/agent.sh", response_class=Response)
async def get_install_script() -> Response:
    """Stream the installer bash script verbatim from disk.

    Re-read on each request to make dev iteration on the script frictionless;
    the script is tiny and this endpoint is rarely hit (once per enrollment).
    """
    install_script = AsyncPath(_INSTALL_SCRIPT)
    if not await install_script.is_file():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"install script missing at {_INSTALL_SCRIPT}",
        )
    body = await install_script.read_bytes()
    return Response(
        content=body,
        media_type="text/x-shellscript",
        headers={
            "Content-Disposition": 'inline; filename="install-agent.sh"',
            "Cache-Control": "no-cache",
        },
    )


@router.get("/agent.tar.gz", response_class=Response)
async def get_agent_tarball() -> Response:
    """Build (or serve from cache) the agent + protocol source tarball."""
    global _tarball_cache
    with _tarball_lock:
        if _tarball_cache is None:
            _tarball_cache = _build_tarball()
        body = _tarball_cache
    return Response(
        content=body,
        media_type="application/gzip",
        headers={
            "Content-Disposition": 'attachment; filename="agent.tar.gz"',
            "Cache-Control": "public, max-age=60",
        },
    )
