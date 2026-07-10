# CoreLab backend multi-stage container build.
#
# Stage 1 (frontend-build): compiles the Vue 3 SPA to static dist/.
# Stage 2 (backend-build): builds Python dependencies, including C extensions.
# Stage 3 (backend-runtime): Python 3.11 slim + the prebuilt environment + the
#   FastAPI app. Runs uvicorn on :8000.

ARG PYTHON_VERSION=3.11

# ─── Stage 1: frontend build ────────────────────────────────────────────
FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend
RUN corepack enable && corepack prepare pnpm@10 --activate

# Lockfile + manifest first for layer caching.
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# Source + build.
COPY frontend/ ./
RUN pnpm build && mkdir -p /build/dist && cp -r dist/. /build/dist/

# ─── Stage 2: backend dependency build ─────────────────────────────────
# The full Python image includes gcc. asyncmy publishes no Linux/aarch64 wheel,
# so arm64 builds compile its Cython extension here. The compiler does not cross
# into the final runtime image.
FROM python:${PYTHON_VERSION} AS backend-build

RUN pip install --no-cache-dir "uv==0.9.26"

WORKDIR /app

# Copy workspace files needed for uv sync. Order matters for layer cache:
# pyproject.toml + lock first, then sources.
COPY pyproject.toml uv.lock ./
COPY shared/ ./shared/
COPY backend/pyproject.toml backend/README.md ./backend/
COPY backend/corelab_backend/ ./backend/corelab_backend/

# Sync only the backend (uv automatically pulls workspace deps it needs).
WORKDIR /app/backend
RUN uv sync --frozen --no-dev --package corelab-backend

# ─── Stage 3: backend runtime ───────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS backend-runtime

WORKDIR /app

# Editable workspace packages in the venv point to these same /app paths.
COPY --from=backend-build /app/.venv /app/.venv
COPY shared/ ./shared/
COPY backend/pyproject.toml backend/README.md ./backend/
COPY backend/corelab_backend/ ./backend/corelab_backend/
WORKDIR /app/backend

# Copy database migrations.
COPY backend/alembic.ini ./
COPY backend/alembic/ ./alembic/

# The entrypoint runs `alembic upgrade head` before uvicorn so
# `docker compose up -d` lands a usable schema with
# no manual migration step.
COPY deploy/backend-entrypoint.sh /usr/local/bin/corelab-entrypoint
RUN chmod 0755 /usr/local/bin/corelab-entrypoint

# Copy the freshly built frontend into the runtime image.
COPY --from=frontend-build /build/dist/ ./static/

# The /install/agent.sh and /install/agent.tar.gz endpoints
# pack and serve the agent source from disk; bundle it into the image
# so enrollment works without an out-of-band distribution step. Cwd is
# /app/backend; the installer module reads from the
# workspace root (parents[4]) so paths line up between dev and container.
WORKDIR /app
COPY agent/ ./agent/
COPY deploy/install-agent.sh ./deploy/install-agent.sh
WORKDIR /app/backend

# Run as non-root.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin corelab \
    && chown -R corelab:corelab /app
USER corelab

# uv puts the venv at the workspace root (/app), shared by all members.
ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]

ENTRYPOINT ["/usr/local/bin/corelab-entrypoint"]
CMD ["uvicorn", "corelab_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
