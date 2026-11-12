import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "modules/health_center/migrations/002_create_dependency_health_tables.sql"


def migration_sql():
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_is_incremental_idempotent_and_mysql57_compatible():
    sql = migration_sql().upper()
    expected = {
        "HEALTH_DEPENDENCIES",
        "HEALTH_DEPENDENCY_CHECKS",
        "HEALTH_DEPENDENCY_INCIDENTS",
        "HEALTH_DEPENDENCY_STATE",
        "HEALTH_EXECUTION_DEPENDENCY_INCIDENTS",
    }
    for table in expected:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "ALTER TABLE" not in sql
    assert "DROP TABLE" not in sql
    assert "CREATE TRIGGER" not in sql
    assert "CREATE EVENT" not in sql
    assert "JSON_TABLE" not in sql
    assert " ROW_NUMBER(" not in sql
    assert "DATETIME(6)" in sql


def test_migration_has_period_and_deduplication_indexes():
    sql = migration_sql().lower()
    for index in (
        "idx_health_dep_check_period",
        "idx_health_dep_incident_period",
        "uq_health_dep_active_incident",
        "idx_health_dep_state_stale",
    ):
        assert index in sql


@pytest.mark.skipif(
    os.getenv("HEALTH_TEST_DB_DISPOSABLE") != "YES_I_CONFIRM_DISPOSABLE",
    reason="requires an explicitly confirmed disposable MySQL 5.7/Percona schema",
)
def test_migration_runtime_requires_disposable_database_confirmation():
    database = os.environ.get("HEALTH_TEST_DB_NAME", "")
    assert database.startswith(("test_", "qa_", "tmp_"))
    assert os.environ.get("HEALTH_TEST_DB_HOST") in {"127.0.0.1", "localhost"}
