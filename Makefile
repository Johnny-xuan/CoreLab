# CoreLab developer task runner.
# `test-integration` brings up disposable MySQL and Redis services, runs
# migrations and backend tests, then tears the services down. It uses host
# ports 3308 and 6380 so it can coexist with the development stack.

# Test-only credentials. Hard-coded here on purpose: this Makefile is
# the SSOT for `make test-integration` so it stays runnable without a
# .env file, the values are scoped to a tmpfs container that lives for
# the duration of the run, and they never reach disk or any network the
# host is not already on. Treat the four below as test fixtures, not
# secrets. Use real values in deploy/.env for the dev / prod stack.
TEST_MYSQL_ROOT_PASSWORD     ?= corelab-test-root
TEST_MYSQL_USER_PASSWORD     ?= corelab-test-mig
TEST_MYSQL_APP_USER_PASSWORD ?= corelab-test-app

TEST_COMPOSE := docker compose -f deploy/docker-compose.test.yml
TEST_MYSQL_HOST := 127.0.0.1
TEST_MYSQL_PORT := 3308
TEST_DB_NAME    := corelab

# Two SQLAlchemy URLs mirror the production split-credential model:
#   - migration URL = full grants (alembic upgrade head)
#   - runtime URL   = SELECT/INSERT/UPDATE only on corelab.*
# Conftest swaps the runtime URL for the migration URL during table wipe,
# so both must be set — see backend/tests/conftest.py:_has_real_db.
TEST_MIGRATION_URL := mysql+asyncmy://corelab:$(TEST_MYSQL_USER_PASSWORD)@$(TEST_MYSQL_HOST):$(TEST_MYSQL_PORT)/$(TEST_DB_NAME)
TEST_RUNTIME_URL   := mysql+asyncmy://corelab_app:$(TEST_MYSQL_APP_USER_PASSWORD)@$(TEST_MYSQL_HOST):$(TEST_MYSQL_PORT)/$(TEST_DB_NAME)
TEST_REDIS_URL     := redis://127.0.0.1:6380/0

export TEST_MYSQL_ROOT_PASSWORD
export TEST_MYSQL_USER_PASSWORD
export TEST_MYSQL_APP_USER_PASSWORD

.PHONY: help test-python test-frontend check test-integration test-integration-up test-integration-down test-integration-migrate test-integration-pytest

help:
	@echo "CoreLab make targets:"
	@echo "  check                    Run Python and frontend static checks"
	@echo "  test-python              Run backend and agent tests without external services"
	@echo "  test-frontend            Run frontend unit tests"
	@echo "  test-integration         End-to-end: up → migrate → pytest → down"
	@echo "  test-integration-up      Start mysql-test + redis-test (host 3308 / 6380)"
	@echo "  test-integration-migrate Run alembic upgrade head against mysql-test"
	@echo "  test-integration-pytest  Run backend pytest against mysql-test (skip-free)"
	@echo "  test-integration-down    Stop the test stack and wipe its volumes/tmpfs"

check:
	uv run ruff check backend agent shared/protocol
	uv run ruff format --check backend agent shared/protocol
	cd backend && uv run mypy corelab_backend
	cd agent && uv run mypy corelab_agent
	cd shared/protocol && uv run mypy corelab_protocol
	cd frontend && pnpm lint && pnpm type-check && pnpm build

test-python:
	cd backend && uv run pytest
	cd agent && uv run pytest

test-frontend:
	cd frontend && pnpm test

# Full integration cycle. `set -e` semantics via Make's default; the
# `down` after pytest runs only on success, but `trap`-style cleanup is
# left to the developer (CTRL-C leaves containers running; run
# `make test-integration-down` to clean up).
test-integration: test-integration-up test-integration-migrate test-integration-pytest test-integration-down

test-integration-up:
	@echo "[corelab] Starting MySQL + Redis test stack on host 3308 / 6380…"
	$(TEST_COMPOSE) up -d --wait

test-integration-migrate:
	@echo "[corelab] Running alembic upgrade head against mysql-test…"
	cd backend && \
	  CORELAB_DATABASE_URL='$(TEST_RUNTIME_URL)' \
	  CORELAB_MIGRATION_DATABASE_URL='$(TEST_MIGRATION_URL)' \
	  CORELAB_REDIS_URL='$(TEST_REDIS_URL)' \
	  uv run alembic upgrade head

test-integration-pytest:
	@echo "[corelab] Running backend pytest against mysql-test (skip-free)…"
	cd backend && \
	  CORELAB_DATABASE_URL='$(TEST_RUNTIME_URL)' \
	  CORELAB_MIGRATION_DATABASE_URL='$(TEST_MIGRATION_URL)' \
	  CORELAB_REDIS_URL='$(TEST_REDIS_URL)' \
	  uv run pytest -v

test-integration-down:
	@echo "[corelab] Tearing down test stack and wiping volumes…"
	$(TEST_COMPOSE) down -v
