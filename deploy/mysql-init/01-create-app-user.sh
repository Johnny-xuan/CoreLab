#!/bin/bash
#
# CoreLab MySQL init: provision the runtime app user with restricted grants.
#
# Runtime application traffic must not mutate audit_log rows.
# This init script keeps DELETE out of the broad runtime grant. UPDATE is
# still needed schema-wide for normal soft-delete/state transitions, so
# audit_log UPDATE/DELETE refusal is enforced by Alembic-created triggers
# on audit_log for the corelab_app runtime user.
#
# Soft-delete patterns in CoreLab use UPDATE is_active=0 rather than SQL
# DELETE. Features that legitimately need DELETE on a non-audit table should grant
# it narrowly per-table here so audit_log stays protected.
#
# MYSQL_USER ('corelab') keeps GRANT ALL on corelab.* via the MySQL
# image's own env handling; alembic uses that account to run DDL.
# Runtime FastAPI traffic uses corelab_app, configured below.

set -euo pipefail

if [ -z "${MYSQL_APP_USER_PASSWORD:-}" ]; then
    echo "FATAL: MYSQL_APP_USER_PASSWORD env var is required" >&2
    exit 1
fi

mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE USER IF NOT EXISTS 'corelab_app'@'%' IDENTIFIED BY '${MYSQL_APP_USER_PASSWORD}';
GRANT SELECT, INSERT, UPDATE ON corelab.* TO 'corelab_app'@'%';
FLUSH PRIVILEGES;
SQL

echo "[corelab-init] corelab_app user created with SELECT/INSERT/UPDATE on corelab.*; audit_log immutability is trigger-enforced"
