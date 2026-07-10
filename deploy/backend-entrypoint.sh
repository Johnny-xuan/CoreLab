#!/bin/sh
# CoreLab backend entrypoint.
#
# Runs `alembic upgrade head` before the application starts. The command is
# idempotent and fails the container when the database cannot be migrated,
# preventing a healthy-looking backend from serving an incompatible schema.

set -eu

echo "==> Running alembic upgrade head"
alembic upgrade head
echo "==> Migrations applied (or already at head)."

exec "$@"
