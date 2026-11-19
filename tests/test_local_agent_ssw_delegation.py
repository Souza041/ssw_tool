from datetime import date, datetime, timezone

import pytest

from modules.local_agent.executor import ExecutionContext, HandlerRegistry
from modules.local_agent.contracts import checked_log_message
from modules.local_agent.repository import LocalAgentRepository
from modules.local_agent.ssw_tasks import (
    TASK_DOCUMENTOS,
    TASK_METRICAS,
    TASK_OC73,
    register_ssw_handlers,
    validate_ssw_payload,
    validate_ssw_runtime_environment,
)


def test_ssw_handlers_remain_disabled_by_default(monkeypatch):
    monkeypatch.delenv("NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED", raising=False)
    registry = HandlerRegistry()
    register_ssw_handlers(registry)
    assert registry.capabilities() == []


def test_operational_logs_redact_environment_secrets(monkeypatch):
    monkeypatch.setenv("SSW_SENHA", "segredo-super-privado")
    message = checked_log_message(
        "falha segredo-super-privado em https://usuario:senha@ssw.example.test/x",
        maximum_bytes=4096,
    )
    assert "segredo-super-privado" not in message
    assert "senha@" not in message
    assert message.count("[REDACTED]") == 2


def test_runtime_preflight_requires_central_snapshot_and_credentials(monkeypatch):
    names = (
        "DOCUMENTOS_SSW_DOMINIO", "DOCUMENTOS_SSW_CPF", "DOCUMENTOS_SSW_USUARIO",
        "DOCUMENTOS_SSW_SENHA", "GCE_LOGIN", "GCE_PASSWORD",
        "DOCUMENTOS_DB_HOST", "DOCUMENTOS_DB_USER", "DOCUMENTOS_DB_PASSWORD",
        "DOCUMENTOS_DB_NAME",
        "METRICAS_SSW_DOMINIO", "METRICAS_SSW_CPF", "METRICAS_SSW_USUARIO",
        "METRICAS_SSW_SENHA", "METRICAS_DB_HOST", "METRICAS_DB_USER",
        "METRICAS_DB_PASSWORD", "METRICAS_DB_NAME",
        "OCORRENCIA_73_SSW_DOMINIO", "OCORRENCIA_73_SSW_CPF",
        "OCORRENCIA_73_SSW_USUARIO", "OCORRENCIA_73_SSW_SENHA",
        "AUTOMATION_DB_HOST", "AUTOMATION_DB_USER",
        "AUTOMATION_DB_PASSWORD", "AUTOMATION_DB_NAME",
    )
    for name in names:
        monkeypatch.setenv(name, "synthetic-test-value")
    monkeypatch.setenv("AUTOMATION_DB_PORT", "3306")
    monkeypatch.setenv("DOCUMENTOS_DB_PORT", "3306")
    monkeypatch.setenv("METRICAS_DB_PORT", "3306")
    monkeypatch.setenv("METRICAS_SNAPSHOT_STORAGE", "database")
    validate_ssw_runtime_environment()
    monkeypatch.setenv("METRICAS_SNAPSHOT_STORAGE", "filesystem")
    with pytest.raises(RuntimeError, match="database"):
        validate_ssw_runtime_environment()


def test_ssw_handler_allowlist_is_fixed_and_explicit(monkeypatch):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED", "true")
    registry = HandlerRegistry()
    register_ssw_handlers(registry)
    assert registry.capabilities() == [
        "ssw.documentos_gce",
        "ssw.metricas_op455",
        "ssw.ocorrencia_73",
        "ssw.ocorrencia_73.recheck_big",
    ]


def test_vps_blocks_only_direct_interactive_posts_when_delegation_is_enabled(monkeypatch):
    from modules.local_agent.vps_guard import direct_ssw_post_blocked

    monkeypatch.setenv("NEXUS_SSW_DELEGATION_ENABLED", "true")
    assert direct_ssw_post_blocked("POST", "/op455") is True
    assert direct_ssw_post_blocked("POST", "/documentos-lg/executar") is True
    assert direct_ssw_post_blocked("GET", "/op455") is False
    assert direct_ssw_post_blocked("POST", "/documentos/executar") is False
    monkeypatch.setenv("NEXUS_SSW_DELEGATION_ENABLED", "false")
    assert direct_ssw_post_blocked("POST", "/op455") is False


@pytest.mark.parametrize(
    ("task", "payload"),
    [
        (TASK_OC73, {"flow": "CWB", "reference_date": "2026-11-19", "extra": 1}),
        (TASK_METRICAS, {"days": 0, "origin": "manual"}),
        (TASK_DOCUMENTOS, {"origin": "SCHEDULED", "period_start": "2026-11-20", "period_end": "2026-11-19"}),
        (TASK_DOCUMENTOS, {"origin": "SCHEDULED", "send_notifications": True, "preview_notifications": True}),
    ],
)
def test_payload_validation_rejects_unsafe_or_inconsistent_values(task, payload):
    with pytest.raises(ValueError):
        validate_ssw_payload(task, payload)


def test_repository_idempotency_and_concurrency_are_atomic(tmp_path):
    repository = LocalAgentRepository(tmp_path / "agent.sqlite3")
    first = repository.enqueue(
        task=TASK_OC73,
        payload={"flow": "CWB", "reference_date": "2026-11-19"},
        max_attempts=1,
        idempotency_key="oc73:cwb:20261119",
        concurrency_key="oc73",
    )
    same_idempotency = repository.enqueue(
        task=TASK_OC73,
        payload={"flow": "CWB", "reference_date": "2026-11-19"},
        max_attempts=1,
        idempotency_key="oc73:cwb:20261119",
        concurrency_key="oc73",
    )
    coalesced = repository.enqueue(
        task=TASK_OC73,
        payload={"flow": "BIG", "reference_date": "2026-11-19"},
        max_attempts=1,
        idempotency_key="oc73:big:20261119",
        concurrency_key="oc73",
        coalesce_active=True,
    )
    assert first == same_idempotency == coalesced
    assert len(repository.list_jobs()) == 1


def test_same_concurrency_jobs_queue_but_cannot_lease_together(tmp_path):
    repository = LocalAgentRepository(tmp_path / "agent.sqlite3")
    first = repository.enqueue(
        task=TASK_OC73,
        payload={"flow": "CWB", "reference_date": "2026-11-19"},
        max_attempts=1,
        concurrency_key="oc73",
    )
    second = repository.enqueue(
        task=TASK_OC73,
        payload={"flow": "BIG", "reference_date": "2026-11-19"},
        max_attempts=1,
        concurrency_key="oc73",
    )
    claimed = repository.claim(
        agent_id="windows-one", capabilities={TASK_OC73}, lease_seconds=120
    )
    blocked = repository.claim(
        agent_id="windows-two", capabilities={TASK_OC73}, lease_seconds=120
    )
    assert first != second
    assert claimed["id"] == first
    assert blocked is None


def test_expired_operational_lease_is_not_retried(tmp_path):
    repository = LocalAgentRepository(tmp_path / "agent.sqlite3")
    created = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    job_id = repository.enqueue(
        task=TASK_METRICAS,
        payload={"days": 30, "origin": "scheduler"},
        max_attempts=1,
        concurrency_key="metrics",
        now=created,
    )
    claimed = repository.claim(
        agent_id="windows-agent",
        capabilities={TASK_METRICAS},
        lease_seconds=30,
        now=created,
    )
    assert claimed["id"] == job_id
    assert repository.claim(
        agent_id="windows-agent-2",
        capabilities={TASK_METRICAS},
        lease_seconds=30,
        now=datetime(2026, 11, 19, 12, 1, tzinfo=timezone.utc),
    ) is None
    assert repository.get_job(job_id)["status"] == "FAILED"
    assert repository.get_job(job_id)["attempts"] == 1


def test_metrics_handler_reuses_service_without_real_ssw(monkeypatch):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED", "true")
    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "false")
    monkeypatch.setenv("METRICAS_SNAPSHOT_STORAGE", "database")
    calls = []

    def fake_update(self, **kwargs):
        calls.append(kwargs)
        return {"success": True, "run_id": 7, "file": "safe.sswweb", "total": 12}

    monkeypatch.setattr("modules.metricas.service.MetricasService.atualizar_op455", fake_update)
    registry = HandlerRegistry()
    register_ssw_handlers(registry)
    context = ExecutionContext(lambda *_: None)
    result = registry.get(TASK_METRICAS)({"days": 30, "origin": "scheduler"}, context)
    assert result == {"success": True, "run_id": 7, "file": "safe.sswweb", "total": 12}
    assert calls == [{
        "triggered_by": "local_agent:scheduler",
        "triggered_user_id": None,
        "dias": 30,
    }]
    assert context.progress_snapshot().current == 1


def test_metrics_remote_snapshot_is_persisted_in_central_database(monkeypatch):
    from modules.metricas import repository

    calls = []
    monkeypatch.setenv("METRICAS_SNAPSHOT_STORAGE", "database")
    monkeypatch.setattr(repository, "execute", lambda sql, params=None: calls.append((sql, params)))
    repository.salvar_snapshot(9, {"meta": {"total": 2}, "DATA": []})
    assert len(calls) == 1
    assert "payload_json" in calls[0][0]
    assert calls[0][1][0] == 9
    assert '"total": 2' in calls[0][1][1]


def test_oc73_scheduler_delegates_without_constructing_service(monkeypatch):
    from modules.ocorrencia_73 import scheduler

    calls = []
    monkeypatch.setattr("modules.local_agent.delegation.delegation_enabled", lambda: True)
    monkeypatch.setattr(
        "modules.local_agent.delegation.enqueue_ssw_task",
        lambda task, payload, **options: calls.append((task, payload, options)) or "job-safe",
    )
    monkeypatch.setattr(
        scheduler, "Ocorrencia73Repository",
        lambda: pytest.fail("VPS não deve construir o executor OC73 quando delega"),
    )
    result = scheduler._executar_ocorrencia_73_agendada_serializada(
        "BIG", date(2026, 11, 19)
    )
    assert result["delegated"] is True
    assert result["job_id"] == "job-safe"
    assert calls[0][0] == TASK_OC73
    assert calls[0][2]["concurrency_key"] == "ssw.ocorrencia_73"
