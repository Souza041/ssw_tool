import os
import unittest
from unittest.mock import patch

from modules.health_center import database as health_db
from modules.health_center.models import Bot, Outcome, Status
from modules.health_center.monitor import run_monitored


ENV = {
    "HEALTH_CENTER_ENABLED": "true",
    "AUTOMATION_DB_HOST": "fake-host",
    "AUTOMATION_DB_USER": "fake-user",
    "AUTOMATION_DB_PASSWORD": "fake-password",
    "AUTOMATION_DB_NAME": "fake-database",
}
BOT = Bot("fake", "Falso")


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, _statement, _parameters):
        self.connection.writes += 1
        if self.connection.fail_write_at == self.connection.writes:
            raise TimeoutError("password=fake-password")


class FakeConnection:
    def __init__(
        self, *, fail_write_at=None, fail_commit=False,
        fail_rollback=False, fail_close=False,
    ):
        self.fail_write_at = fail_write_at
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback
        self.fail_close = fail_close
        self.writes = 0
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.forced_closes = 0
        self.open = True
        self.transaction_open = True

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1
        if self.fail_commit:
            raise TimeoutError("commit timeout")
        self.transaction_open = False

    def rollback(self):
        self.rollbacks += 1
        if self.fail_rollback:
            raise TimeoutError("rollback timeout")
        self.transaction_open = False

    def close(self):
        self.closes += 1
        if self.fail_close:
            raise TimeoutError("close timeout")
        self.open = False
        self.transaction_open = False

    def _force_close(self):
        self.forced_closes += 1
        self.open = False
        self.transaction_open = False


def observe(operation):
    return run_monitored(
        bot=BOT, origin="cron", operation=operation,
        classify=lambda _: Outcome(Status.SUCCESS),
    )


class HealthDatabaseTests(unittest.TestCase):
    def test_defaults_and_independent_timeout_overrides(self):
        with patch.dict(os.environ, ENV, clear=True), \
                patch.object(health_db, "load_dotenv"), \
                patch.object(health_db.pymysql, "connect") as connect:
            health_db.get_connection()
            args = connect.call_args.kwargs
            self.assertEqual(
                (args["connect_timeout"], args["read_timeout"], args["write_timeout"]),
                (2.0, 2.0, 2.0),
            )
            self.assertEqual(args["host"], "fake-host")
            self.assertEqual(args["autocommit"], False)

        overrides = {
            **ENV, "HEALTH_DB_CONNECT_TIMEOUT": "1.5",
            "HEALTH_DB_READ_TIMEOUT": "3", "HEALTH_DB_WRITE_TIMEOUT": "4.5",
        }
        with patch.dict(os.environ, overrides, clear=True), \
                patch.object(health_db, "load_dotenv"), \
                patch.object(health_db.pymysql, "connect") as connect:
            health_db.get_connection()
            args = connect.call_args.kwargs
            self.assertEqual(
                (args["connect_timeout"], args["read_timeout"], args["write_timeout"]),
                (1.5, 3.0, 4.5),
            )

    def test_disabled_never_opens_connection(self):
        calls = []
        with patch.dict(os.environ, {**ENV, "HEALTH_CENTER_ENABLED": "false"}, clear=True), \
                patch.object(health_db.pymysql, "connect", side_effect=AssertionError):
            self.assertEqual(observe(lambda: calls.append(1) or "done"), "done")
        self.assertEqual(calls, [1])

    def test_invalid_timeout_fails_before_connect_without_running_twice(self):
        calls = []
        bad_env = {**ENV, "HEALTH_DB_READ_TIMEOUT": "nan"}
        with patch.dict(os.environ, bad_env, clear=True), \
                patch.object(health_db, "load_dotenv"), \
                patch.object(health_db.pymysql, "connect") as connect:
            self.assertEqual(observe(lambda: calls.append(1) or "done"), "done")
        self.assertEqual(calls, [1])
        connect.assert_not_called()

    def test_connection_timeout_does_not_retry_or_skip_bot(self):
        calls = []
        with patch.dict(os.environ, ENV, clear=True), \
                patch.object(health_db, "load_dotenv"), \
                patch.object(health_db.pymysql, "connect", side_effect=TimeoutError) as connect:
            self.assertEqual(observe(lambda: calls.append(1) or "done"), "done")
        self.assertEqual(calls, [1])
        connect.assert_called_once()

    def test_initial_write_timeout_rolls_back_closes_and_runs_once(self):
        connection = FakeConnection(fail_write_at=1)
        calls = []
        with patch.dict(os.environ, ENV, clear=True), \
                patch.object(health_db, "load_dotenv"), \
                patch.object(health_db.pymysql, "connect", return_value=connection) as connect:
            self.assertEqual(observe(lambda: calls.append(1) or "done"), "done")
        self.assertEqual(calls, [1])
        self.assertEqual((connection.rollbacks, connection.closes), (1, 1))
        self.assertFalse(connection.open)
        self.assertFalse(connection.transaction_open)
        connect.assert_called_once()

    def test_final_write_timeout_rolls_back_without_changing_result(self):
        start, finish = FakeConnection(), FakeConnection(fail_write_at=1)
        calls = []
        with patch.dict(os.environ, ENV, clear=True), \
                patch.object(health_db, "load_dotenv"), \
                patch.object(health_db.pymysql, "connect", side_effect=[start, finish]) as connect:
            self.assertEqual(observe(lambda: calls.append(1) or "done"), "done")
        self.assertEqual(calls, [1])
        self.assertEqual((start.commits, start.closes), (1, 1))
        self.assertEqual((finish.rollbacks, finish.closes), (1, 1))
        self.assertTrue(all(not c.open and not c.transaction_open for c in (start, finish)))
        self.assertEqual(connect.call_count, 2)

    def test_commit_timeout_rolls_back_and_preserves_bot(self):
        connection = FakeConnection(fail_commit=True)
        calls = []
        with patch.dict(os.environ, ENV, clear=True), \
                patch.object(health_db, "load_dotenv"), \
                patch.object(health_db.pymysql, "connect", return_value=connection):
            self.assertEqual(observe(lambda: calls.append(1) or "done"), "done")
        self.assertEqual(calls, [1])
        self.assertEqual((connection.commits, connection.rollbacks, connection.closes), (1, 1, 1))
        self.assertFalse(connection.transaction_open)

    def test_rollback_timeout_still_attempts_close_and_preserves_bot(self):
        connection = FakeConnection(fail_write_at=1, fail_rollback=True)
        calls = []
        with patch.dict(os.environ, ENV, clear=True), \
                patch.object(health_db, "load_dotenv"), \
                patch.object(health_db.pymysql, "connect", return_value=connection):
            self.assertEqual(observe(lambda: calls.append(1) or "done"), "done")
        self.assertEqual(calls, [1])
        self.assertEqual((connection.rollbacks, connection.closes), (1, 1))
        self.assertFalse(connection.open)
        self.assertFalse(connection.transaction_open)

    def test_close_timeout_uses_force_close_without_orphaning_start(self):
        start = FakeConnection(fail_close=True)
        finish = FakeConnection(fail_close=True)
        calls = []
        with patch.dict(os.environ, ENV, clear=True), \
                patch.object(health_db, "load_dotenv"), \
                patch.object(health_db.pymysql, "connect", side_effect=[start, finish]) as connect:
            self.assertEqual(observe(lambda: calls.append(1) or "done"), "done")
        self.assertEqual(calls, [1])
        self.assertEqual(connect.call_count, 2)
        for connection in (start, finish):
            self.assertEqual((connection.commits, connection.closes, connection.forced_closes), (1, 1, 1))
            self.assertFalse(connection.open)
            self.assertFalse(connection.transaction_open)

    def test_rollback_and_close_timeouts_attempt_forced_cleanup(self):
        connection = FakeConnection(
            fail_write_at=1, fail_rollback=True, fail_close=True,
        )
        calls = []
        with patch.dict(os.environ, ENV, clear=True), \
                patch.object(health_db, "load_dotenv"), \
                patch.object(health_db.pymysql, "connect", return_value=connection):
            self.assertEqual(observe(lambda: calls.append(1) or "done"), "done")
        self.assertEqual(calls, [1])
        self.assertEqual(
            (connection.rollbacks, connection.closes, connection.forced_closes),
            (1, 1, 1),
        )
        self.assertFalse(connection.open)
        self.assertFalse(connection.transaction_open)

    def test_failed_final_commit_does_not_replace_original_bot_exception(self):
        start = FakeConnection()
        finish = FakeConnection(fail_commit=True)
        calls = []

        def operation():
            calls.append(1)
            raise ValueError("original bot error")

        with patch.dict(os.environ, ENV, clear=True), \
                patch.object(health_db, "load_dotenv"), \
                patch.object(health_db.pymysql, "connect", side_effect=[start, finish]):
            with self.assertRaisesRegex(ValueError, "original bot error"):
                observe(operation)
        self.assertEqual(calls, [1])
        self.assertEqual((finish.rollbacks, finish.closes), (1, 1))
        self.assertFalse(finish.transaction_open)


if __name__ == "__main__":
    unittest.main()
