import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from modules.health_center.models import Bot, Outcome, Status
from modules.health_center.monitor import run_monitored


BOT = Bot("fake_bot", "Bot falso")


class FakeRepository:
    def __init__(self, fail_start=False, fail_finish=False):
        self.fail_start = fail_start
        self.fail_finish = fail_finish
        self.started = []
        self.finished = []
        self.lock = threading.Lock()

    def start(self, **kwargs):
        if self.fail_start:
            raise ConnectionError("password=secret")
        with self.lock:
            self.started.append(kwargs)

    def finish(self, **kwargs):
        if self.fail_finish:
            raise ConnectionError("password=secret")
        with self.lock:
            self.finished.append(kwargs)
        return True


def success(_result):
    return Outcome(Status.SUCCESS, processed=3, succeeded=3)


class MonitorTests(unittest.TestCase):
    def test_flag_absent_defaults_to_disabled(self):
        repo = FakeRepository()
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(run_monitored(
                bot=BOT, origin="cron", operation=lambda: "untouched",
                classify=success, repository=repo,
            ), "untouched")
        self.assertEqual(repo.started, [])

    def test_disabled_does_not_touch_repository_or_classify(self):
        repo = FakeRepository()
        calls = []
        with patch.dict(os.environ, {"HEALTH_CENTER_ENABLED": "false"}):
            result = run_monitored(
                bot=BOT, origin="cron", operation=lambda: calls.append(1) or 7,
                classify=lambda _: self.fail("classificador acionado"),
                repository=repo,
            )
        self.assertEqual(result, 7)
        self.assertEqual(calls, [1])
        self.assertEqual(repo.started, [])
        self.assertEqual(repo.finished, [])

    def test_enabled_records_start_and_success_with_utc_duration(self):
        repo = FakeRepository()
        with patch.dict(os.environ, {"HEALTH_CENTER_ENABLED": "true"}):
            result = run_monitored(
                bot=BOT, origin="cron", operation=lambda: {"ok": True},
                classify=success, repository=repo,
            )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(repo.started), 1)
        self.assertEqual(len(repo.finished), 1)
        start = repo.started[0]
        finish = repo.finished[0]
        self.assertEqual(start["execution_id"], finish["execution_id"])
        self.assertEqual(start["correlation_id"], start["execution_id"])
        self.assertEqual(start["started_at"].utcoffset().total_seconds(), 0)
        self.assertGreaterEqual(finish["duration_seconds"], 0)
        self.assertEqual(finish["outcome"].status, Status.SUCCESS)

    def test_exception_is_reraised_and_sanitized(self):
        repo = FakeRepository()

        def operation():
            raise RuntimeError("password=secret")

        with patch.dict(os.environ, {"HEALTH_CENTER_ENABLED": "true"}):
            with self.assertRaisesRegex(RuntimeError, "password=secret"):
                run_monitored(
                    bot=BOT, origin="cron", operation=operation,
                    classify=success, repository=repo,
                )
        finish = repo.finished[0]
        self.assertEqual(finish["outcome"].status, Status.FAILED)
        self.assertNotIn("secret", finish["error_message"])

    def test_database_failure_does_not_repeat_or_change_operation(self):
        for fail_start, fail_finish in ((True, False), (False, True)):
            with self.subTest(fail_start=fail_start):
                calls = []
                repo = FakeRepository(fail_start, fail_finish)
                with patch.dict(os.environ, {"HEALTH_CENTER_ENABLED": "true"}):
                    value = run_monitored(
                        bot=BOT, origin="cron",
                        operation=lambda: calls.append(1) or "original",
                        classify=success, repository=repo,
                    )
                self.assertEqual(value, "original")
                self.assertEqual(calls, [1])

    def test_database_failure_preserves_original_exception(self):
        repo = FakeRepository(fail_start=True)
        calls = []

        def operation():
            calls.append(1)
            raise ValueError("original failure")

        with patch.dict(os.environ, {"HEALTH_CENTER_ENABLED": "true"}):
            with self.assertRaisesRegex(ValueError, "original failure"):
                run_monitored(
                    bot=BOT, origin="cron", operation=operation,
                    classify=success, repository=repo,
                )
        self.assertEqual(calls, [1])
        self.assertEqual(repo.finished, [])

    def test_classification_error_does_not_change_result(self):
        repo = FakeRepository()
        with patch.dict(os.environ, {"HEALTH_CENTER_ENABLED": "true"}):
            result = run_monitored(
                bot=BOT, origin="cron", operation=lambda: "done",
                classify=lambda _: 1 / 0, repository=repo,
            )
        self.assertEqual(result, "done")
        self.assertEqual(repo.finished, [])

    def test_concurrent_executions_have_unique_ids(self):
        repo = FakeRepository()

        def one(_):
            return run_monitored(
                bot=BOT, origin="scheduler", operation=lambda: "done",
                classify=success, repository=repo,
            )

        with patch.dict(os.environ, {"HEALTH_CENTER_ENABLED": "true"}):
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(list(pool.map(one, range(24))), ["done"] * 24)
        ids = [row["execution_id"] for row in repo.started]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(repo.finished), 24)

    def test_outcome_rejects_invalid_status_and_counts(self):
        with self.assertRaises(ValueError):
            Outcome("SUCCESS")
        for bad in (-1, True, 1.5, 2**64):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Outcome(Status.SUCCESS, processed=bad)


if __name__ == "__main__":
    unittest.main()
