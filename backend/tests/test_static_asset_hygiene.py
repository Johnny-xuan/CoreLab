"""Static bundle hygiene checks for the backend image build."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_backend_image_uses_only_freshly_built_frontend_assets() -> None:
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY backend/static/ ./static/" not in dockerfile
    assert "RUN rm -rf ./static/assets" not in dockerfile
    assert "COPY --from=frontend-build /build/dist/ ./static/" in dockerfile
