from datetime import date, datetime, timezone
from types import SimpleNamespace

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
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_SSW_TASKS", TASK_METRICAS)
    names = (
        "METRICAS_SSW_DOMINIO", "METRICAS_SSW_CPF", "METRICAS_SSW_USUARIO",
        "METRICAS_SSW_SENHA", "METRICAS_DB_HOST", "METRICAS_DB_USER",
        "METRICAS_DB_PASSWORD", "METRICAS_DB_NAME",
    )
    for name in names:
        monkeypatch.setenv(name, "synthetic-test-value")
    monkeypatch.setenv("METRICAS_DB_PORT", "3306")
    monkeypatch.setenv("METRICAS_SNAPSHOT_STORAGE", "database")
    validate_ssw_runtime_environment()
    monkeypatch.setenv("METRICAS_SNAPSHOT_STORAGE", "filesystem")
    with pytest.raises(RuntimeError, match="database"):
        validate_ssw_runtime_environment()


def test_ssw_handler_allowlist_is_fixed_and_explicit(monkeypatch):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED", "true")
    monkeypatch.setenv(
        "NEXUS_LOCAL_AGENT_SSW_TASKS",
        ",".join((TASK_DOCUMENTOS, TASK_METRICAS)),
    )
    registry = HandlerRegistry()
    register_ssw_handlers(registry)
    assert registry.capabilities() == [
        "ssw.documentos_gce",
        "ssw.metricas_op455",
    ]


def test_metrics_can_be_the_only_registered_ssw_handler(monkeypatch):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED", "true")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_SSW_TASKS", TASK_METRICAS)
    registry = HandlerRegistry()
    register_ssw_handlers(registry)
    assert registry.capabilities() == [TASK_METRICAS]


def test_oc73_cannot_be_selected_on_windows(monkeypatch):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED", "true")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_SSW_TASKS", TASK_OC73)
    with pytest.raises(RuntimeError, match="desconhecida"):
        register_ssw_handlers(HandlerRegistry())


def test_vps_delegation_requires_global_gate_and_task_allowlist(monkeypatch):
    from modules.local_agent.delegation import delegation_enabled, delegated_tasks

    monkeypatch.setenv("NEXUS_SSW_DELEGATION_ENABLED", "false")
    monkeypatch.setenv("NEXUS_SSW_DELEGATED_TASKS", f"{TASK_DOCUMENTOS},{TASK_METRICAS}")
    assert delegation_enabled() is False
    assert delegation_enabled(TASK_DOCUMENTOS) is False

    monkeypatch.setenv("NEXUS_SSW_DELEGATION_ENABLED", "true")
    assert delegated_tasks() == frozenset({TASK_DOCUMENTOS, TASK_METRICAS})
    assert delegation_enabled(TASK_DOCUMENTOS) is True
    assert delegation_enabled(TASK_METRICAS) is True
    assert delegation_enabled(TASK_OC73) is False


def test_vps_rejects_oc73_in_delegated_allowlist(monkeypatch):
    from modules.local_agent.delegation import delegated_tasks

    monkeypatch.setenv("NEXUS_SSW_DELEGATED_TASKS", TASK_OC73)
    with pytest.raises(RuntimeError, match="não autorizada"):
        delegated_tasks()


def test_enqueue_rejects_task_outside_delegated_allowlist(monkeypatch):
    from modules.local_agent.delegation import enqueue_ssw_task

    monkeypatch.setenv("NEXUS_SSW_DELEGATION_ENABLED", "true")
    monkeypatch.setenv("NEXUS_SSW_DELEGATED_TASKS", TASK_DOCUMENTOS)
    with pytest.raises(RuntimeError, match="desativada ou não autorizada"):
        enqueue_ssw_task(
            TASK_METRICAS,
            {"origin": "manual", "days": 30},
            idempotency_key=None,
            concurrency_key="ssw.metricas_op455",
        )


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
        (TASK_METRICAS, {"days": 31, "origin": "homologation", "mode": "validate_only", "correlation_id": "HML-20261119"}),
        (TASK_METRICAS, {"days": 1, "origin": "homologation", "mode": "validate_only", "correlation_id": "curto"}),
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
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_SSW_TASKS", TASK_METRICAS)
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


def test_metrics_homologation_uses_validate_only_without_database(monkeypatch, tmp_path):
    from modules.metricas.service import MetricasService

    monkeypatch.setenv("NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED", "true")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_SSW_TASKS", TASK_METRICAS)
    database_calls = []
    monkeypatch.setattr("modules.metricas.repository.criar_execucao", lambda *a, **k: database_calls.append("create"))

    def fake_collect(self, *, dias, output_dir):
        path = output_dir / "op455-test.sswweb"
        path.write_bytes(b"fixture-op455")
        return path, {"meta": {"total": 3}, "DATA": []}

    monkeypatch.setattr(MetricasService, "_coletar_op455", fake_collect)
    registry = HandlerRegistry()
    register_ssw_handlers(registry)
    result = registry.get(TASK_METRICAS)({
        "days": 1,
        "origin": "homologation",
        "mode": "validate_only",
        "correlation_id": "HML-20261119-01",
    }, ExecutionContext(lambda *_: None))
    assert result["mode"] == "validate_only"
    assert result["total"] == 3
    assert result["database_writes"] == 0
    assert len(result["sha256"]) == 64
    assert database_calls == []


def test_gce_handler_preserves_manual_no_notification_payload(monkeypatch):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED", "true")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_SSW_TASKS", TASK_DOCUMENTOS)
    calls = []

    def fake_execute(**kwargs):
        calls.append(kwargs)
        return {
            "success": True,
            "pipeline_run_id": 88,
            "reference_date": "2026-11-19",
            "period": {"start": "2026-11-13", "end": "2026-11-19"},
            "matching": {"total": 4, "matched": 3, "errors": 0},
        }

    monkeypatch.setattr("modules.documentos.execution.execute_daily_documents", fake_execute)
    registry = HandlerRegistry()
    register_ssw_handlers(registry)
    result = registry.get(TASK_DOCUMENTOS)({
        "origin": "MANUAL",
        "send_notifications": False,
        "preview_notifications": False,
    }, ExecutionContext(lambda *_: None))
    assert calls[0]["triggered_by"] == "MANUAL"
    assert calls[0]["send_notifications"] is False
    assert calls[0]["preview_notifications"] is False
    assert result["pipeline_run_id"] == 88
    assert result["matching"]["matched"] == 3


def test_scheduled_gce_script_delegates_without_notifications(monkeypatch, capsys):
    from scripts import processar_documentos_diario as script

    calls = []
    monkeypatch.setattr("modules.local_agent.delegation.gce_central_agent", lambda: "windows-central")
    monkeypatch.setattr(
        script, "delegation_enabled", lambda task=None: task == TASK_DOCUMENTOS
    )
    monkeypatch.setattr(
        script, "enqueue_ssw_task",
        lambda task, payload, **kwargs: calls.append((task, payload, kwargs)) or "scheduled-job",
    )
    monkeypatch.setattr(
        script, "execute_daily_documents",
        lambda **kwargs: pytest.fail("pipeline local não deve executar"),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["processar-documentos", "--data-referencia", "2026-11-19"],
    )
    script.main()
    assert calls[0][0] == TASK_DOCUMENTOS
    assert calls[0][1]["origin"] == "SCHEDULED"
    assert calls[0][2]["target_agent_id"] == "windows-central"
    assert calls[0][1]["send_notifications"] is False
    assert calls[0][1]["preview_notifications"] is False
    assert "scheduled-job" in capsys.readouterr().out


def test_metrics_production_refactor_preserves_persistence_contract(monkeypatch, tmp_path):
    from modules.metricas.service import MetricasService

    calls = []
    monkeypatch.setattr("modules.metricas.repository.criar_execucao", lambda **kwargs: calls.append(("start", kwargs)) or 41)
    monkeypatch.setattr("modules.metricas.repository.salvar_snapshot", lambda run_id, payload: calls.append(("snapshot", run_id, payload)))
    monkeypatch.setattr("modules.metricas.repository.finalizar_execucao_sucesso", lambda **kwargs: calls.append(("success", kwargs)))

    def fake_collect(self, *, dias, output_dir):
        path = tmp_path / "op455.sswweb"
        path.write_bytes(b"fixture")
        return path, {"meta": {"total": 2}, "DATA": []}

    monkeypatch.setattr(MetricasService, "_coletar_op455", fake_collect)
    service = MetricasService()
    service.output_dir = tmp_path
    result = service.atualizar_op455(triggered_by="manual", dias=7)
    assert result == {"success": True, "run_id": 41, "file": "op455.sswweb", "total": 2}
    assert [item[0] for item in calls] == ["start", "snapshot", "success"]


def test_metrics_production_error_does_not_persist_secret(monkeypatch, tmp_path):
    from modules.metricas.service import MetricasService

    errors = []
    monkeypatch.setattr("modules.metricas.repository.criar_execucao", lambda **kwargs: 42)
    monkeypatch.setattr("modules.metricas.repository.finalizar_execucao_erro", lambda run_id, error: errors.append(str(error)))
    monkeypatch.setattr(
        MetricasService, "_coletar_op455",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("senha-super-secreta")),
    )
    service = MetricasService()
    service.output_dir = tmp_path
    result = service.atualizar_op455()
    assert result["success"] is False
    assert "senha-super-secreta" not in result["error"]
    assert "senha-super-secreta" not in errors[0]


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


def test_isolated_metrics_cli_enqueues_validate_only_once(monkeypatch, capsys):
    from scripts import nexus_local_agent_metrics_homologation as cli

    calls = []

    class FakeRepository:
        def __init__(self, *args, **kwargs):
            pass

        def enqueue(self, **kwargs):
            calls.append(kwargs)
            return "11111111-1111-1111-1111-111111111111"

    settings = SimpleNamespace(
        enabled=True,
        allowed_tasks=frozenset({TASK_METRICAS}),
        agent_tasks={"windows-agent": frozenset({TASK_METRICAS})},
        database_path="ignored.sqlite3",
        max_payload_bytes=65536,
        max_result_bytes=65536,
        max_log_bytes=4096,
    )
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_METRICS_HOMOLOGATION_ENABLED", "true")
    monkeypatch.setattr(cli, "load_private_environment", lambda *a, **k: None)
    monkeypatch.setattr(cli, "delegation_enabled", lambda: False)
    monkeypatch.setattr(cli, "load_server_settings", lambda: settings)
    monkeypatch.setattr(cli, "LocalAgentRepository", FakeRepository)
    monkeypatch.setattr(
        "sys.argv",
        ["metrics-hml", "enqueue", "--days", "1", "--correlation-id", "HML-OP455-20261119"],
    )
    assert cli.main() == 0
    assert calls[0]["max_attempts"] == 1
    assert calls[0]["idempotency_key"] == "metrics-hml:HML-OP455-20261119"
    assert calls[0]["payload"]["mode"] == "validate_only"
    assert "nenhum segredo" in capsys.readouterr().out


def test_oc73_scheduler_is_blocked_while_global_delegation_is_active(monkeypatch):
    from modules.ocorrencia_73 import scheduler

    monkeypatch.setenv("NEXUS_SSW_DELEGATION_ENABLED", "true")
    monkeypatch.setenv("NEXUS_SSW_DELEGATED_TASKS", f"{TASK_DOCUMENTOS},{TASK_METRICAS}")
    monkeypatch.setattr(
        scheduler, "Ocorrencia73Repository",
        lambda: pytest.fail("VPS não deve construir o executor OC73 quando delega"),
    )
    result = scheduler._executar_ocorrencia_73_agendada_serializada(
        "BIG", date(2026, 11, 19)
    )
    assert result is None
