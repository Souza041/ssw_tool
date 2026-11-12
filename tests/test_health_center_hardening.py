"""Contratos offline de privacidade, leitura e autorização administrativa."""

import os
import importlib.util
import sys
import types
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from modules.health_center import database, routes
from modules.health_center.models import Bot, Outcome, Status
from modules.health_center.privacy import checked_metadata
from modules.health_center.repository import HealthRepository
from web import session_config


class Cursor:
    def __init__(self, rows=()):
        self.rows = rows
        self.calls = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, rows=()):
        self.c = Cursor(rows)
        self.rollbacks = 0
        self.closes = 0

    def cursor(self):
        return self.c

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


def repository_with(connection):
    @contextmanager
    def tx():
        yield connection
    return HealthRepository(tx)


@pytest.mark.parametrize("metadata", [
    {"cpf": "12345678901"}, {"token": "secret"}, {"cookie": "secret"},
    {"documento": "texto"}, {"total_op455": "12345678901"},
    {"total_op455": 12345678901}, {"total_op455": -1},
    {"dry_run": "true"}, {"total_op455": [1]},
])
def test_metadados_sensiveis_ou_fora_do_contrato_rejeitados(metadata):
    with pytest.raises(ValueError):
        checked_metadata(metadata)


def test_repository_rejeita_texto_livre_antes_da_conexao():
    connection = Connection()
    repo = repository_with(connection)
    with pytest.raises(ValueError):
        repo.start(
            execution_id="id", bot=Bot("bot", "Bot", parameters={"password": "secret"}),
            origin="cron", started_at=datetime.now(timezone.utc),
        )
    with pytest.raises(ValueError):
        repo.start(
            execution_id="id", bot=Bot("bot", "CPF 12345678901"),
            origin="cron", started_at=datetime.now(timezone.utc),
        )
    with pytest.raises(ValueError):
        repo.finish(
            execution_id="id", outcome=Outcome(Status.FAILED, summary="CPF 12345678901"),
            finished_at=datetime.now(timezone.utc), duration_seconds=1,
        )
    with pytest.raises(ValueError):
        repo.finish(
            execution_id="id", outcome=Outcome(Status.FAILED),
            finished_at=datetime.now(timezone.utc), duration_seconds=1,
            error_message="token=secret",
        )
    assert connection.c.calls == []


def test_running_suspeita_somente_leitura_sem_transicao():
    now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
    connection = Connection(({"id": "synthetic", "started_at_utc": now - timedelta(hours=3)},))
    repo = repository_with(connection)
    rows = repo.suspicious_running(now=now, threshold_minutes=120, limit=5)
    assert rows[0]["observability_state"] == "SUSPECTED_INTERRUPTION"
    sql, params = connection.c.calls[0]
    assert sql.lstrip().startswith("SELECT")
    assert params == (datetime(2026, 11, 10, 10), 5)
    assert "UPDATE" not in sql


def test_read_transaction_usa_start_read_only_e_rollback():
    connection = Connection()
    with patch.object(database, "get_connection", return_value=connection):
        with database.read_transaction() as current:
            assert current is connection
    assert connection.c.calls == [("START TRANSACTION READ ONLY", None)]
    assert (connection.rollbacks, connection.closes) == (1, 1)


def test_catalogo_nao_inventa_proxima_execucao():
    connection = Connection(({"identifier": "ocorrencia_73", "category": "ocorrencias",
                              "schedule_source": "cron_ou_scheduler", "enabled": 1},))
    rows = repository_with(connection).list_bots()
    assert rows == [{"identifier": "ocorrencia_73", "category": "ocorrencias",
                     "operational_state": "unverified", "next_execution": None}]
    assert "schedule_source" not in connection.c.calls[0][0]


@pytest.mark.parametrize("value,expected", [
    (None, False), ("invalid", False), ("true", True), ("false", False),
])
def test_flag_padroa_desativado_e_env_de_processo(value, expected):
    env = {} if value is None else {"HEALTH_CENTER_ENABLED": value}
    with patch.dict(os.environ, env, clear=True):
        assert routes.enabled() is expected


@pytest.mark.parametrize("process_value,expected", [
    (None, None), ("invalid", "invalid"), ("true", "true"), ("false", "false"),
])
def test_cron_preserva_apenas_flag_externo_apos_dotenv_sintetico(process_value, expected):
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    fake_dotenv = types.ModuleType("dotenv")
    def load_synthetic(_path, *, override):
        assert override is True
        os.environ["HEALTH_CENTER_ENABLED"] = "true"
        os.environ["OCORRENCIA_73_ENABLED"] = "false"
    fake_dotenv.load_dotenv = load_synthetic
    env = {} if process_value is None else {"HEALTH_CENTER_ENABLED": process_value}
    with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, {"dotenv": fake_dotenv}):
        spec = importlib.util.spec_from_file_location(
            "health_cron_config_test", root / "scripts" / "testar_ocorrencia_73.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)
        assert os.environ.get("HEALTH_CENTER_ENABLED") == expected
        assert os.environ["OCORRENCIA_73_ENABLED"] == "false"


@pytest.mark.parametrize("process_value,expected", [
    (None, None), ("invalid", "invalid"), ("true", "true"), ("false", "false"),
])
def test_fastapi_preserva_flag_externo_apos_dotenv_sintetico(process_value, expected):
    env = {session_config.SECRET_ENV_NAME: "synthetic-secret"}
    if process_value is not None:
        env["HEALTH_CENTER_ENABLED"] = process_value
    def load_synthetic(_path, *, override):
        assert override is False
        os.environ.setdefault("HEALTH_CENTER_ENABLED", "true")
    with patch.dict(os.environ, env, clear=True), \
            patch.object(session_config, "load_dotenv", side_effect=load_synthetic), \
            patch.object(session_config, "validar_session_secret_key", return_value="synthetic-secret"):
        session_config.carregar_configuracao_sessao()
        assert os.environ.get("HEALTH_CENTER_ENABLED") == expected


def test_endpoints_exigem_admin_no_backend_e_off_nao_abre_banco(monkeypatch):
    calls = []
    monkeypatch.setattr(routes, "HealthRepository", lambda: calls.append("db"))
    monkeypatch.setattr(routes, "obter_client", lambda request: None)
    request = SimpleNamespace()
    with pytest.raises(HTTPException) as unauthenticated:
        routes.health_bots(request, limit=50)
    assert unauthenticated.value.status_code == 401
    monkeypatch.setattr(routes, "obter_client", lambda request: object())
    monkeypatch.setattr(routes, "is_admin", lambda request: False)
    with pytest.raises(HTTPException) as forbidden:
        routes.health_bots(request, limit=50)
    assert forbidden.value.status_code == 403
    monkeypatch.setattr(routes, "is_admin", lambda request: True)
    with patch.dict(os.environ, {"HEALTH_CENTER_ENABLED": "false"}):
        with pytest.raises(HTTPException) as disabled:
            routes.health_bots(request, limit=50)
        assert disabled.value.status_code == 503
        assert routes.health_status(request)["enabled"] is False
    assert calls == []


def test_endpoint_admin_ligado_devolve_catalogo_sem_agendamento(monkeypatch):
    request = SimpleNamespace()
    monkeypatch.setattr(routes, "obter_client", lambda request: object())
    monkeypatch.setattr(routes, "is_admin", lambda request: True)
    class Repository:
        def catalog(self, *, limit, now, threshold_minutes):
            assert limit == 5
            assert threshold_minutes == 120
            return [{"identifier": "ocorrencia_73", "category": "ocorrencias",
                     "operational_state": "unverified", "next_execution": None,
                     "registered": True, "instrumented": True,
                     "observed": False, "schedule_verified": False}]
    monkeypatch.setattr(routes, "HealthRepository", Repository)
    with patch.dict(os.environ, {"HEALTH_CENTER_ENABLED": "true"}):
        result = routes.health_bots(request, limit=5)
    assert result["items"][0]["next_execution"] is None
    assert result["items"][0]["observed"] is False
    assert "schedule_source" not in result["items"][0]


def test_router_expoe_somente_metodos_get():
    assert {route.path for route in routes.router.routes} == {
        "/admin/health-center/status", "/admin/health-center/bots",
        "/admin/health-center/executions", "/admin/health-center/suspicious-running",
        "/admin/health-center/overview",
        "/admin/health-center/bots/{bot_id}",
        "/admin/health-center/bots/{bot_id}/view",
        "/admin/health-center/executions/{execution_id}",
        "/admin/health-center/executions/{execution_id}/view",
        "/admin/health-center/dependencies",
        "/admin/health-center/dependencies/{dependency_id}",
        "/admin/health-center/dependencies/{dependency_id}/view",
    }
    assert all(route.methods == {"GET"} for route in routes.router.routes)
