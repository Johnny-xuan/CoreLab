"""Static guards for the audit_log database immutability invariant."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260704_d5e6f7a8b9c0_audit_log_immutable_triggers.py"
)
MYSQL_INIT = ROOT / "deploy" / "mysql-init" / "01-create-app-user.sh"
COMPOSE_FILES = (
    ROOT / "deploy" / "docker-compose.yml",
    ROOT / "deploy" / "docker-compose.test.yml",
)


def test_audit_log_migration_blocks_runtime_update_and_delete() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "before update on audit_log" in lowered
    assert "before delete on audit_log" in lowered
    assert "trg_audit_log_no_update_for_app_user" in text
    assert "trg_audit_log_no_delete_for_app_user" in text
    assert "USER()" in text
    assert "CURRENT_USER()" not in text
    assert "corelab_app" in text
    assert "SIGNAL SQLSTATE '45000'" in text


def test_runtime_mysql_grant_does_not_include_delete() -> None:
    text = MYSQL_INIT.read_text(encoding="utf-8")
    grant_lines = [
        line.strip().lower()
        for line in text.splitlines()
        if line.strip().lower().startswith("grant ")
    ]

    assert "grant select, insert, update on corelab.* to 'corelab_app'@'%';" in grant_lines
    assert all("delete" not in line for line in grant_lines)


def test_mysql_compose_allows_trigger_migrations_without_super() -> None:
    for path in COMPOSE_FILES:
        text = path.read_text(encoding="utf-8")
        assert "--log-bin-trust-function-creators=1" in text, path
