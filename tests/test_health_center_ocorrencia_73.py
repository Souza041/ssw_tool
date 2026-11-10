"""Contratos offline da classificação e dos pontos de entrada OC73."""

import importlib.util
import sys
import types
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from modules.health_center.models import Status
from modules.health_center.ocorrencia_73 import classify_result
from modules.health_center import repository as health_repository
from modules.ocorrencia_73 import scheduler


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DATE = date(2026, 10, 12)
EXPECTED_ARGUMENTS = {
    "data_referencia": REFERENCE_DATE,
    "triggered_by": "scheduler:CWB",
    "fluxo": "CWB",
    "execucao_id_reservada": 700,
}


def _manual_module(monkeypatch):
    """Importa o CLI sem ler o .env real."""
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)
    spec = importlib.util.spec_from_file_location(
        "health_test_oc73_manual", ROOT / "scripts/testar_ocorrencia_73.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _scheduled_fakes(monkeypatch, *, result=None, operation_error=None):
    """Isola calendário, reserva e serviço; registra a chamada operacional."""
    calls = []
    reservation_calls = []
    actual_result = result if result is not None else {"success": True}

    class ReservationFactory:
        def __new__(cls):
            return reservation_repository

    class ReservationRepository:
        def reservar_execucao_agendada(self, **kwargs):
            reservation_calls.append(kwargs)
            return {
                "reservada": True,
                "execucao_id": 700,
                "triggered_by": "scheduler:CWB",
            }

    reservation_repository = ReservationRepository()

    class Service:
        def __init__(self, *, repository):
            assert repository is reservation_repository

        def executar(self, **kwargs):
            calls.append(kwargs)
            if operation_error is not None:
                raise operation_error
            return actual_result

    monkeypatch.setattr(scheduler, "calcular_janelas_oc73", lambda *a, **k: [object()])
    monkeypatch.setattr(scheduler, "Ocorrencia73Repository", ReservationFactory)
    monkeypatch.setattr(scheduler, "Ocorrencia73Service", Service)
    return calls, reservation_calls, actual_result


def test_partial_even_when_service_reports_success():
    result = {
        "success": True, "dry_run": True,
        "total_relatorio": 20, "total_filtrado": 8,
        "total_consultado": 8, "total_erro_op101": 1,
        "total_erro_lancamento": 1,
        "diagnostico": {
            "cwb_curitiba": 2, "joi_florianopolis": 1,
            "joi_big_oc64_cliente": 3, "cliente": 7,
        },
        "itens": [{"cpf": "sensitive"}],
    }
    outcome = classify_result(result)
    assert outcome.status is Status.PARTIAL
    assert (outcome.processed, outcome.succeeded, outcome.errors) == (8, 6, 2)
    assert outcome.metadata["rejected_customer_count"] == 13
    assert outcome.metadata["big_oc64_allowed_customer"] == 3
    assert "itens" not in outcome.metadata
    assert "sensitive" not in repr(outcome.metadata)


@pytest.mark.parametrize("fields", [
    {"total_erro_tecnico": 1},
    {"total_incerto": 1},
    {"total_bloqueado": 1},
])
def test_operational_issues_are_not_success(fields):
    assert classify_result({"success": True, **fields}).status is Status.PARTIAL


def test_technical_counter_does_not_double_count_stage_errors():
    outcome = classify_result({
        "success": True,
        "total_consultado": 4,
        "total_erro_op101": 1,
        "total_erro_lancamento": 1,
        "total_erro_tecnico": 2,
        "total_incerto": 1,
    })
    assert outcome.status is Status.PARTIAL
    assert (outcome.processed, outcome.succeeded, outcome.errors) == (4, 1, 3)


def test_success_and_explicit_failure():
    assert classify_result({"success": True}).status is Status.SUCCESS
    assert classify_result({"success": False}).status is Status.FAILED
    assert classify_result({"success": "False"}).status is Status.FAILED


@pytest.mark.parametrize("bad", [-1, True, "1"])
def test_invalid_counter_is_not_silently_marked_success(bad):
    with pytest.raises(ValueError):
        classify_result({"success": True, "total_erro_op101": bad})


def test_manual_entrypoint_preserves_exception_without_monitor(monkeypatch):
    module = _manual_module(monkeypatch)
    original = RuntimeError("original bot failure")
    calls = []

    class Service:
        def executar(self, **kwargs):
            calls.append(kwargs)
            raise original

    monkeypatch.setattr(module, "_carregar_fluxo_manual", lambda: module.FluxoManual(
        Service, True, False, 1, "CWB123"
    ))
    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "true")
    forbidden_health = Mock(side_effect=AssertionError("manual CLI must not be monitored"))
    monkeypatch.setattr(health_repository, "HealthRepository", forbidden_health)
    with pytest.raises(RuntimeError) as caught:
        module.main(["--ctrc", "CWB123"])
    assert caught.value is original
    assert calls == [{"triggered_by": "homologacao_manual"}]
    forbidden_health.assert_not_called()


def test_manual_entrypoint_runs_once_with_original_origin_and_exit_code(monkeypatch):
    module = _manual_module(monkeypatch)
    calls = []

    class Service:
        def executar(self, **kwargs):
            calls.append(kwargs)
            return {"success": True}

    monkeypatch.setattr(module, "_carregar_fluxo_manual", lambda: module.FluxoManual(
        Service, True, False, 1, "CWB123"
    ))
    assert module.main(["--ctrc", "CWB123"]) == 0
    assert calls == [{"triggered_by": "homologacao_manual"}]


def test_manual_entrypoint_gate_prevents_execution(monkeypatch):
    module = _manual_module(monkeypatch)
    service = Mock(side_effect=AssertionError("service must not run"))
    monkeypatch.setattr(module, "_carregar_fluxo_manual", lambda: module.FluxoManual(
        service, False, True, 1, "CWB123"
    ))
    monkeypatch.delenv(module.VAR_AUTORIZACAO_MANUAL, raising=False)
    with pytest.raises(RuntimeError, match="Homologação manual real não autorizada"):
        module.main(["--ctrc", "CWB123"])
    service.assert_not_called()


def test_scheduler_runs_once_and_preserves_exception_handling(monkeypatch):
    original = RuntimeError("fake error")
    calls, reservations, _ = _scheduled_fakes(monkeypatch, operation_error=original)
    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "false")
    logged = Mock()
    monkeypatch.setattr(scheduler.logger, "exception", logged)
    assert scheduler.executar_ocorrencia_73_agendada("CWB", REFERENCE_DATE) is None
    assert calls == [EXPECTED_ARGUMENTS]
    assert reservations == [{
        "data_referencia": REFERENCE_DATE, "fluxo": "CWB", "dry_run": scheduler.DRY_RUN
    }]
    logged.assert_called_once()


def test_scheduler_enabled_records_result_with_fake_repository(monkeypatch):
    calls, reservations, result = _scheduled_fakes(monkeypatch, result={
        "success": True, "total_consultado": 1, "total_incerto": 1,
    })
    recorded = []

    class HealthRepository:
        def start(self, **kwargs):
            recorded.append(("start", kwargs))

        def finish(self, **kwargs):
            recorded.append(("finish", kwargs))
            return True

    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "true")
    monkeypatch.setattr(health_repository, "HealthRepository", HealthRepository)
    assert scheduler.executar_ocorrencia_73_agendada("CWB", REFERENCE_DATE) is result
    assert calls == [EXPECTED_ARGUMENTS]
    assert reservations == [{
        "data_referencia": REFERENCE_DATE, "fluxo": "CWB", "dry_run": scheduler.DRY_RUN
    }]
    assert [kind for kind, _ in recorded] == ["start", "finish"]
    assert recorded[0][1]["origin"] == "scheduler:CWB"
    assert recorded[1][1]["outcome"].status is Status.PARTIAL


def test_scheduler_disabled_does_not_open_health_repository(monkeypatch):
    calls, _, result = _scheduled_fakes(monkeypatch)
    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "false")
    monkeypatch.setattr(health_repository, "HealthRepository", Mock(
        side_effect=AssertionError("health DB must not open")
    ))
    assert scheduler.executar_ocorrencia_73_agendada("CWB", REFERENCE_DATE) is result
    assert calls == [EXPECTED_ARGUMENTS]
    health_repository.HealthRepository.assert_not_called()


@pytest.mark.parametrize("stage", ["start", "finish"])
def test_scheduler_health_database_failure_preserves_result(monkeypatch, stage):
    calls, _, result = _scheduled_fakes(monkeypatch)

    class HealthRepository:
        def start(self, **kwargs):
            if stage == "start":
                raise TimeoutError("fake database timeout")

        def finish(self, **kwargs):
            if stage == "finish":
                raise TimeoutError("fake database timeout")
            return True

    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "true")
    monkeypatch.setattr(health_repository, "HealthRepository", HealthRepository)
    assert scheduler.executar_ocorrencia_73_agendada("CWB", REFERENCE_DATE) is result
    assert calls == [EXPECTED_ARGUMENTS]


def test_scheduler_classification_failure_preserves_result(monkeypatch):
    calls, _, result = _scheduled_fakes(monkeypatch)
    health = Mock()
    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "true")
    monkeypatch.setattr(health_repository, "HealthRepository", lambda: health)
    monkeypatch.setattr(scheduler, "classify_result", Mock(side_effect=ValueError("bad counter")))
    assert scheduler.executar_ocorrencia_73_agendada("CWB", REFERENCE_DATE) is result
    assert calls == [EXPECTED_ARGUMENTS]
    health.start.assert_called_once()
    health.finish.assert_not_called()


def test_scheduler_exception_identity_with_monitor_enabled(monkeypatch):
    original = RuntimeError("original operation exception")
    calls, _, _ = _scheduled_fakes(monkeypatch, operation_error=original)
    caught = []

    class HealthRepository:
        def start(self, **kwargs):
            pass

        def finish(self, **kwargs):
            return True

    def record_exception(*args, **kwargs):
        caught.append(sys.exc_info()[1])

    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "true")
    monkeypatch.setattr(health_repository, "HealthRepository", HealthRepository)
    monkeypatch.setattr(scheduler.logger, "exception", record_exception)
    assert scheduler.executar_ocorrencia_73_agendada("CWB", REFERENCE_DATE) is None
    assert calls == [EXPECTED_ARGUMENTS]
    assert caught == [original]
