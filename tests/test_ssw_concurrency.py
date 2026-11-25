"""Concorrencia de jobs SSW sem chamadas reais ou escrita operacional."""

import threading
import time
from types import SimpleNamespace

from ssw.browser_transport import _OperationCoordinator
from web import jobs
from web import routes


class LockedClient:
    def __init__(self):
        self.session = object()
        self.coordinator = _OperationCoordinator()

    def acquire_operation(self, automation):
        return self.coordinator.acquire(automation)


def wait_status(job, expected, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if job.status == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"job permaneceu em {job.status!r}")


def test_job_acquires_before_thread_and_rejects_duplicate_tabs(monkeypatch):
    monkeypatch.setattr(jobs.automation_metrics, "finished", lambda *_args: None)
    client = LockedClient()
    started = threading.Event()
    finish = threading.Event()

    def operation(_job, _client):
        started.set()
        assert finish.wait(2)
        return "ok"

    first = jobs.Job(id="first", module="OP001 · Coletas")
    jobs.executar_job(first, operation, client)
    assert started.wait(1)

    duplicate = jobs.Job(id="duplicate", module="OP455 · CTRCs")
    jobs.executar_job(duplicate, operation, client)

    assert duplicate.status == "error"
    assert "OP001 · Coletas" in duplicate.error
    assert "em andamento" in duplicate.error
    finish.set()
    wait_status(first, "done")

    after = jobs.Job(id="after", module="OP455 · CTRCs")
    jobs.executar_job(after, lambda *_args: "ok", client)
    wait_status(after, "done")


def test_job_releases_session_lock_after_operational_error(monkeypatch):
    monkeypatch.setattr(jobs.automation_metrics, "finished", lambda *_args: None)
    client = LockedClient()

    failed = jobs.Job(id="failed", module="OP001 · Coletas")
    jobs.executar_job(
        failed,
        lambda *_args: (_ for _ in ()).throw(RuntimeError("falha simulada")),
        client,
    )
    wait_status(failed, "error")

    retry = jobs.Job(id="retry", module="OP001 · Coletas")
    jobs.executar_job(retry, lambda *_args: "ok", client)
    wait_status(retry, "done")


def test_non_ssw_jobs_are_not_serialized(monkeypatch):
    monkeypatch.setattr(jobs.automation_metrics, "finished", lambda *_args: None)
    both_started = threading.Barrier(3)
    finish = threading.Event()

    def operation(_job, _value):
        both_started.wait(timeout=2)
        assert finish.wait(2)
        return "ok"

    first = jobs.Job(id="one", module="Renomeador")
    second = jobs.Job(id="two", module="Whirlpool")
    jobs.executar_job(first, operation, SimpleNamespace())
    jobs.executar_job(second, operation, SimpleNamespace())
    both_started.wait(timeout=2)
    finish.set()
    wait_status(first, "done")
    wait_status(second, "done")


def test_nexus_session_endpoint_exposes_safe_state(monkeypatch):
    expected = {
        "state": "busy",
        "message": "Automacao em andamento: OP455.",
        "operation": "OP455",
        "observed_unit": "CWB",
    }
    client = SimpleNamespace(session_status=lambda: expected)
    monkeypatch.setattr(routes, "obter_client", lambda _request: client)

    result = routes.ssw_session_status(SimpleNamespace(session={"ssw_session_id": "x"}))

    assert result == expected


def test_nexus_shell_has_session_state_and_reauthentication_link():
    source = open("web/templates/base.html", encoding="utf-8").read()
    assert "/api/ssw-session-status" in source
    assert "reauthentication_required" in source
    assert "badge.href='/login'" in source

