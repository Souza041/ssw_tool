import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from modules.health_center.models import Bot, Outcome, Status
from modules.health_center.repository import HealthRepository, checked_log_ref, utc_naive


class FakeCursor:
    def __init__(self, operations, rowcount=1, rows=()):
        self.operations = operations
        self.rowcount = rowcount
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, parameters):
        assert statement.count("%s") == len(parameters)
        self.operations.append((statement, parameters))

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def cursor(self):
        return self.fake_cursor


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.operations = []
        self.cursor = FakeCursor(self.operations)

        @contextmanager
        def transaction():
            yield FakeConnection(self.cursor)

        self.repo = HealthRepository(transaction)

    def test_start_is_atomic_and_idempotent_by_execution_id(self):
        started = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        self.repo.start(
            execution_id="same-id", bot=Bot("oc73", "OC73"),
            origin="cron", started_at=started,
        )
        self.assertEqual(len(self.operations), 2)
        self.assertIn("health_bots", self.operations[0][0])
        self.assertIn("ON DUPLICATE KEY UPDATE id = id", self.operations[1][0])
        self.assertEqual(self.operations[1][1][0], "same-id")
        self.assertEqual(self.operations[1][1][5], "RUNNING")
        self.assertIsNone(self.operations[1][1][6].tzinfo)

    def test_finish_only_transitions_running(self):
        now = datetime.now(timezone.utc)
        done = self.repo.finish(
            execution_id="id", outcome=Outcome(Status.PARTIAL, 3, 2, 1),
            finished_at=now, duration_seconds=2.5,
        )
        self.assertTrue(done)
        statement, parameters = self.operations[0]
        self.assertIn("status = 'RUNNING'", statement)
        self.assertEqual(parameters[0], "PARTIAL")
        self.assertEqual(parameters[3:6], (3, 2, 1))
        self.cursor.rowcount = 0
        self.assertFalse(self.repo.finish(
            execution_id="id", outcome=Outcome(Status.SUCCESS),
            finished_at=now, duration_seconds=3,
        ))

    def test_stale_running_is_read_only(self):
        self.cursor.rows = ({"id": "stale"},)
        before = datetime.now(timezone.utc) - timedelta(hours=3)
        self.assertEqual(
            self.repo.stale_running(before=before), [{"id": "stale"}]
        )
        statement, parameters = self.operations[0]
        self.assertTrue(statement.lstrip().startswith("SELECT"))
        self.assertEqual(parameters[1], 100)

    def test_utc_conversion_and_validation(self):
        local = timezone(timedelta(hours=-3))
        value = datetime(2026, 10, 7, 9, 0, tzinfo=local)
        self.assertEqual(utc_naive(value), datetime(2026, 10, 7, 12, 0))
        with self.assertRaises(ValueError):
            utc_naive(datetime(2026, 10, 7))
        with self.assertRaises(ValueError):
            self.repo.stale_running(before=datetime.now(timezone.utc), limit=0)

    def test_log_reference_is_limited_to_relative_logs_path(self):
        self.assertEqual(checked_log_ref("logs/oc73/run.log"), "logs/oc73/run.log")
        for unsafe in ("../secret", "logs/../secret", "logs\\secret", "/logs/a", "logs//a"):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                checked_log_ref(unsafe)


if __name__ == "__main__":
    unittest.main()
