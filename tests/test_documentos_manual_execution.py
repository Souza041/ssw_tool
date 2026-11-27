from pathlib import Path

import pytest
from fastapi import HTTPException

from modules.documentos import execution
from modules.documentos.health import BOT, classify_result
from modules.documentos.services.daily_pipeline import acquire_execution_lock
from modules.health_center.privacy import (
    checked_catalog_text,
    checked_metadata,
    checked_summary,
)
import modules.documentos.web as documentos_web
from web.jobs import Job


class FakeClient:
    usuario = "admin_teste"
    agent_id = "windows-admin-teste"
    session_binding = "s" * 48

    def identity_payload(self):
        return {
            "dominio": "ROD", "usuario": self.usuario, "unidade": "CWB",
            "session_binding": self.session_binding,
        }


class FakeLease:
    def __init__(self):
        self.released = False

    def release(self):
        self.released = True


@pytest.fixture
def web_request(monkeypatch):
    monkeypatch.setattr(documentos_web, "obter_client", lambda request: FakeClient())
    monkeypatch.setattr(documentos_web, "is_admin", lambda request: True)
    return type("RequestStub", (), {"session": {
        "ssw_session_id": "sessao-teste",
        "documentos_gce_csrf": "csrf-teste",
    }})()


def test_manual_endpoint_requires_valid_csrf(web_request, monkeypatch):
    monkeypatch.setattr(
        documentos_web,
        "acquire_execution_lock",
        lambda: pytest.fail("não deve reservar a trava"),
    )
    with pytest.raises(HTTPException) as error:
        documentos_web.documentos_executar(web_request, "incorreto")
    assert error.value.status_code == 403
    assert "CSRF" in error.value.detail


def test_manual_endpoint_requires_admin(web_request, monkeypatch):
    monkeypatch.setattr(documentos_web, "is_admin", lambda request: False)
    with pytest.raises(HTTPException) as error:
        documentos_web.documentos_executar(web_request, "csrf-teste")
    assert error.value.status_code == 403


def test_manual_endpoint_starts_background_job_without_running_pipeline(
    web_request, monkeypatch
):
    lease = FakeLease()
    captured = {}
    monkeypatch.setattr(documentos_web, "acquire_execution_lock", lambda: lease)

    def fake_background(job, func, *args, **kwargs):
        captured.update(job=job, func=func, args=args, kwargs=kwargs)

    monkeypatch.setattr(documentos_web, "executar_job", fake_background)
    response = documentos_web.documentos_executar(web_request, "csrf-teste")

    assert response["accepted"] is True
    assert captured["func"] is documentos_web._run_manual_job
    assert captured["kwargs"]["execution_lease"] is lease
    assert lease.released is False


def test_manual_endpoint_delegates_gce_without_notifications(web_request, monkeypatch):
    calls = []
    monkeypatch.setattr("modules.local_agent.delegation.gce_central_agent", lambda: "windows-central")
    monkeypatch.setenv("NEXUS_SSW_DELEGATION_ENABLED", "true")
    monkeypatch.setenv("NEXUS_SSW_DELEGATED_TASKS", "ssw.documentos_gce")
    monkeypatch.setattr(
        "modules.local_agent.delegation.enqueue_ssw_task",
        lambda task, payload, **kwargs: calls.append((task, payload, kwargs)) or "remote-job",
    )
    monkeypatch.setattr(
        documentos_web, "acquire_execution_lock",
        lambda: pytest.fail("execução local não deve reservar trava"),
    )
    response = documentos_web.documentos_executar(web_request, "csrf-teste")
    assert response["job_id"] == "remote-job"
    assert calls[0][0] == "ssw.documentos_gce"
    assert calls[0][1]["send_notifications"] is False
    assert calls[0][1]["preview_notifications"] is False
    assert calls[0][2]["target_agent_id"] == "windows-central"
    assert calls[0][2]["concurrency_key"] == "ssw.documentos_gce"
    assert not {"dominio", "usuario", "unidade", "session_binding"} & calls[0][1].keys()


def test_manual_endpoint_reports_scheduled_or_manual_conflict(web_request, monkeypatch):
    def conflict():
        raise RuntimeError("ocupada")

    monkeypatch.setattr(documentos_web, "acquire_execution_lock", conflict)
    with pytest.raises(HTTPException) as error:
        documentos_web.documentos_executar(web_request, "csrf-teste")
    assert error.value.status_code == 409
    assert "manual ou agendada" in error.value.detail


def test_shared_file_lock_rejects_parallel_execution(tmp_path: Path):
    name = f"documentos-gce-test-{tmp_path.name}.lock"
    first = acquire_execution_lock(name)
    try:
        with pytest.raises(RuntimeError, match="já está em execução"):
            acquire_execution_lock(name)
    finally:
        first.release()

    second = acquire_execution_lock(name)
    second.release()


def test_manual_worker_exposes_real_progress_and_logs_without_real_pipeline(monkeypatch):
    job = Job(id="gce-job-test")
    lease = FakeLease()

    def fake_execute(**kwargs):
        assert kwargs["triggered_by"] == "MANUAL"
        assert kwargs["execution_lease"] is lease
        kwargs["progress"]("[1/6] Baixando relatórios do Portal GCE...")
        kwargs["progress"]("[4/6] Consolidando vínculos pendentes...")
        return {"success": True}

    monkeypatch.setattr(documentos_web, "execute_daily_documents", fake_execute)
    result = documentos_web._run_manual_job(job, execution_lease=lease)

    assert result == {"success": True}
    assert (job.progress, job.total) == (6, 6)
    logs = documentos_web._job_logs(job)
    assert any("[1/6]" in line for line in logs)
    assert any("[4/6]" in line for line in logs)


def test_execution_entrypoint_preserves_origin_user_and_pipeline(monkeypatch):
    calls = {}

    class FakePipeline:
        def __init__(self, progress):
            calls["progress"] = progress

        def run(self, **kwargs):
            calls["pipeline"] = kwargs
            return {"success": True, "matching": {}}

    def fake_monitored(**kwargs):
        calls["health"] = kwargs
        return kwargs["operation"]()

    monkeypatch.setattr(execution, "DailyDocumentsPipeline", FakePipeline)
    monkeypatch.setattr(execution, "run_monitored", fake_monitored)
    lease = FakeLease()
    result = execution.execute_daily_documents(
        triggered_by="manual",
        progress=lambda message: None,
        execution_lease=lease,
    )

    assert result["success"] is True
    assert calls["health"]["origin"] == "manual"
    assert calls["health"].get("responsible_user") is None
    assert calls["pipeline"]["triggered_by"] == "MANUAL"
    assert calls["pipeline"]["execution_lease"] is lease


def test_health_contract_accepts_sanitized_gce_catalog_and_outcome():
    outcome = classify_result({
        "success": True,
        "pipeline_run_id": 42,
        "matching": {"total": 8, "matched": 6, "errors": 0},
    })
    assert checked_catalog_text(BOT.name, maximum=150) == BOT.name
    assert checked_catalog_text(BOT.description, maximum=500) == BOT.description
    assert checked_catalog_text(BOT.category, maximum=80) == BOT.category
    assert checked_catalog_text(BOT.schedule_source, maximum=80) == BOT.schedule_source
    assert checked_summary(outcome.summary) == ""
    assert checked_metadata(outcome.metadata) == "{}"
    assert outcome.processed == 8
    assert outcome.succeeded == 6


def test_gce_central_agent_requires_static_authorization(monkeypatch):
    from types import SimpleNamespace
    from modules.local_agent import delegation
    settings = SimpleNamespace(remote_auth_agent_id="windows-central", tokens={}, agent_tasks={})
    monkeypatch.setattr(delegation, "load_server_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="Agente central"):
        delegation.gce_central_agent()
    settings.tokens = {"windows-central": "test"}
    with pytest.raises(RuntimeError, match="Agente central"):
        delegation.gce_central_agent()
    settings.agent_tasks = {"windows-central": {"ssw.documentos_gce"}}
    assert delegation.gce_central_agent() == "windows-central"
