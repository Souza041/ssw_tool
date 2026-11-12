import json
import logging

import pytest

from modules.health_center.dependency_monitor import MonitorSettings
from scripts import run_dependency_monitor as worker


def run_immediate(coroutine):
    """Executa handlers com doubles que não suspendem, sem criar sockets/event loop."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("test double unexpectedly suspended")


def test_environment_file_does_not_override_supervisor_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "HEALTH_DEPENDENCY_INTERVAL_SECONDS=999\n"
        "HEALTH_DEPENDENCY_TIMEOUT_SECONDS=7\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HEALTH_DEPENDENCY_INTERVAL_SECONDS", "300")
    monkeypatch.delenv("HEALTH_DEPENDENCY_TIMEOUT_SECONDS", raising=False)
    assert worker.load_worker_environment(env_file) == env_file
    assert worker.MonitorSettings.from_env().interval_seconds == 300
    assert worker.MonitorSettings.from_env().timeout_seconds == 7


def test_environment_loader_explicitly_disables_override(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        worker, "load_dotenv",
        lambda **kwargs: calls.append(kwargs),
    )
    env_file = tmp_path / ".env"
    worker.load_worker_environment(env_file)
    assert calls == [{"dotenv_path": env_file, "override": False}]


def production_settings():
    return MonitorSettings(
        enabled=True, interval_seconds=300, timeout_seconds=5,
        max_concurrency=1, retries=0, failure_threshold=3,
        recovery_threshold=2, stale_after_seconds=900,
        alert_cooldown_seconds=1800,
    )


def test_worker_is_opt_in_and_does_not_construct_monitor(monkeypatch):
    monkeypatch.setattr(worker, "load_worker_environment", lambda: None)
    monkeypatch.setattr(
        worker.MonitorSettings, "from_env",
        classmethod(lambda cls: MonitorSettings(enabled=False)),
    )
    monkeypatch.setattr(
        worker, "DependencyMonitor",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("monitor constructed")),
    )
    with pytest.raises(RuntimeError, match="precisa ser true"):
        run_immediate(worker._main())


def test_worker_builds_monitor_without_web_or_scheduler_startup(monkeypatch, caplog):
    calls = []

    class Monitor:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        async def run_forever(self, stop_event):
            calls.append(("run", stop_event.is_set()))

    monkeypatch.setattr(worker, "load_worker_environment", lambda: None)
    monkeypatch.setattr(
        worker.MonitorSettings, "from_env", classmethod(lambda cls: production_settings()),
    )
    monkeypatch.setattr(worker, "definitions_from_env", lambda: [])
    monkeypatch.setattr(worker, "DependencyMonitor", Monitor)
    with caplog.at_level(logging.INFO, logger="nexus.health.dependencies"):
        run_immediate(worker._main())
    assert calls[0][0] == "init" and calls[1] == ("run", False)
    assert calls[0][1]["settings"] == production_settings()
    events = [json.loads(record.message)["event"] for record in caplog.records]
    assert events == ["dependency_worker_started", "dependency_worker_stopped"]
