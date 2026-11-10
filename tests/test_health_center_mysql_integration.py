"""Integração opt-in: somente schema descartável em MySQL/MariaDB local."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import pymysql

from modules.health_center import database
from modules.health_center.models import Bot, Outcome, Status
from modules.health_center.repository import HealthRepository


@pytest.fixture(scope="module")
def disposable_config():
    if os.getenv("HEALTH_TEST_DB_DISPOSABLE") != "YES_I_CONFIRM_DISPOSABLE":
        pytest.skip("MySQL descartável não habilitado explicitamente")
    name = os.getenv("HEALTH_TEST_DB_NAME", "")
    host = os.getenv("HEALTH_TEST_DB_HOST", "")
    if (not name.startswith("hc_disposable_")
            or os.getenv("HEALTH_TEST_DB_CONFIRM_NAME") != name
            or host not in ("localhost", "127.0.0.1", "::1")):
        pytest.fail("Configuração de banco descartável inválida; conexão recusada")
    if not os.getenv("HEALTH_TEST_DB_USER") or not os.getenv("HEALTH_TEST_DB_PASSWORD"):
        pytest.fail("Credenciais do banco descartável ausentes; conexão recusada")
    return {
        "host": host,
        "port": int(os.getenv("HEALTH_TEST_DB_PORT", "3306")),
        "user": os.environ["HEALTH_TEST_DB_USER"],
        "password": os.environ["HEALTH_TEST_DB_PASSWORD"],
        "database": name,
        "autocommit": False,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "connect_timeout": 2,
        "read_timeout": 2,
        "write_timeout": 2,
    }


@pytest.fixture(scope="module")
def migrated(disposable_config):
    sql = (Path(__file__).resolve().parents[1] / "modules" / "health_center"
           / "migrations" / "001_create_health_tables.sql").read_text(encoding="utf-8")
    sql = "\n".join(line for line in sql.splitlines()
                    if not line.lstrip().startswith("--"))
    for _ in range(2):
        connection = pymysql.connect(**disposable_config)
        try:
            with connection.cursor() as cursor:
                for statement in sql.split(";"):
                    if statement.strip():
                        cursor.execute(statement)
            connection.commit()
        finally:
            connection.close()
    return disposable_config


def test_migration_idempotence_and_schema(migrated):
    connection = pymysql.connect(**migrated)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW TABLES LIKE 'health_%'")
            names = {next(iter(row.values())) for row in cursor.fetchall()}
            assert {"health_bots", "health_executions"} <= names
            cursor.execute("SHOW INDEX FROM health_executions WHERE Key_name = 'PRIMARY'")
            assert cursor.fetchall()
    finally:
        connection.close()


def test_real_uuid_idempotence_concurrency_and_conditional_finish(migrated):
    connections = []

    def connect():
        connection = pymysql.connect(**migrated)
        connections.append(connection)
        return connection

    execution_id = str(uuid.uuid4())
    bot = Bot("hc_test_" + uuid.uuid4().hex[:16], "Teste sintético")
    now = datetime.now(timezone.utc)
    with patch.object(database, "get_connection", side_effect=connect):
        repository = HealthRepository()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: repository.start(
                execution_id=execution_id, bot=bot, origin="test", started_at=now,
            ), range(2)))
        with ThreadPoolExecutor(max_workers=2) as pool:
            closed = list(pool.map(lambda _: repository.finish(
                execution_id=execution_id, outcome=Outcome(Status.SUCCESS),
                finished_at=now, duration_seconds=0.1,
            ), range(2)))
    assert sorted(closed) == [False, True]
    assert all(not connection.open for connection in connections)


def test_rollback_real_e_leitura_sem_escrita(migrated):
    bot_id = "hc_rollback_" + uuid.uuid4().hex[:16]
    connection = pymysql.connect(**migrated)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO health_bots (identifier, name, category, enabled, "
                "created_at_utc, updated_at_utc) VALUES (%s, %s, %s, 0, NOW(6), NOW(6))",
                (bot_id, "Teste sintético", "teste"),
            )
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            cursor.execute("SELECT COUNT(*) AS total FROM health_bots WHERE identifier = %s", (bot_id,))
            assert cursor.fetchone()["total"] == 0
        connection.rollback()
    finally:
        connection.close()
    assert not connection.open
