import logging
from pathlib import Path
import threading

import pytest

from modules.local_agent.executor import AgentProtocolError
from modules.local_agent.windows_runtime import (
    SingleInstanceLock,
    configure_runtime_logging,
    sanitize_operational_log,
)
from scripts.nexus_local_agent_windows import run_continuously


def test_operational_log_redacts_credentials_tokens_cpf_and_cookies(monkeypatch):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_TOKEN", "synthetic-agent-secret-value")
    safe = sanitize_operational_log(
        "authorization=Bearer abcdefghijklmnop token=synthetic-agent-secret-value "
        "senha=minha-senha cpf=123.456.789-01 cookie=session-value "
        "https://user:password@example.test"
    )
    for forbidden in (
        "abcdefghijklmnop", "synthetic-agent-secret-value", "minha-senha",
        "123.456.789-01", "session-value", "password@example",
    ):
        assert forbidden not in safe
    assert safe.count("[REDACTED") >= 5


def test_rotating_file_log_is_persistent_and_sanitized(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_TOKEN", "secret-never-written")
    log_file = tmp_path / "logs" / "agent.log"
    logger = configure_runtime_logging(log_file, max_bytes=180, backup_count=2)
    for index in range(20):
        logger.warning(
            "tentativa=%s token=%s cpf=12345678901",
            index,
            "secret-never-written",
        )
    for handler in logger.handlers:
        handler.flush()
    files = list(log_file.parent.glob("agent.log*"))
    assert log_file in files
    assert 2 <= len(files) <= 3
    combined = "".join(path.read_text(encoding="utf-8") for path in files)
    assert "secret-never-written" not in combined
    assert "12345678901" not in combined


def test_single_instance_lock_rejects_duplicate_and_recovers_after_release(tmp_path):
    path = tmp_path / "runtime" / "agent.lock"
    first = SingleInstanceLock(path)
    second = SingleInstanceLock(path)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


class CapturingLogger:
    def __init__(self):
        self.messages = []

    def warning(self, message, *args):
        self.messages.append(("WARNING", message % args))

    def info(self, message, *args):
        self.messages.append(("INFO", message % args))


def test_continuous_loop_retries_transient_failure_and_reports_recovery():
    class ImmediateEvent:
        stopped = False

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, _seconds):
            return self.stopped

    stop = ImmediateEvent()
    logger = CapturingLogger()
    calls = []

    def run_once():
        calls.append(len(calls))
        if len(calls) <= 2:
            raise AgentProtocolError("token=must-not-appear")
        stop.set()
        return False

    run_continuously(
        run_once,
        stop_event=stop,
        poll_seconds=0,
        logger=logger,
    )
    assert len(calls) == 3
    assert sum(level == "WARNING" for level, _ in logger.messages) == 1
    assert any("restabelecida" in message for _, message in logger.messages)
    assert "must-not-appear" not in repr(logger.messages)


def test_continuous_loop_does_not_hide_unexpected_programming_failure():
    with pytest.raises(ValueError, match="synthetic"):
        run_continuously(
            lambda: (_ for _ in ()).throw(ValueError("synthetic")),
            stop_event=threading.Event(),
            poll_seconds=0,
            logger=CapturingLogger(),
        )

