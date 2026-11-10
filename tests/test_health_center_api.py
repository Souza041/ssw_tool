"""API, consultas e apresentação Health Center com dados sintéticos, sem banco."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader, select_autoescape

from modules.health_center import routes
from modules.health_center.repository import HealthRepository, public_execution


NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


class Cursor:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        assert sql.lstrip().startswith("SELECT ")
        assert sql.count("%s") == len(params)

    def fetchone(self):
        return self.results.pop(0)

    def fetchall(self):
        return self.results.pop(0)


class Connection:
    def __init__(self, results):
        self.c = Cursor(results)

    def cursor(self):
        return self.c


def repository_with(*results):
    connection = Connection(results)

    @contextmanager
    def read():
        yield connection

    return HealthRepository(read, read), connection.c


def _access(monkeypatch, *, authenticated=True, admin=True):
    monkeypatch.setattr(routes, "obter_client", lambda request: object() if authenticated else None)
    monkeypatch.setattr(routes, "is_admin", lambda request: admin)
    return SimpleNamespace(session={}, url=SimpleNamespace(path="/admin/health-center"))


@pytest.mark.parametrize("handler", [
    lambda r: routes.health_status(r),
    lambda r: routes.health_overview(r),
    lambda r: routes.health_bots(r, limit=5),
    lambda r: routes.health_bot_detail(r, "ocorrencia_73", days=30, page=1),
    lambda r: routes.health_bot_page(r, "ocorrencia_73", days=30, page=1),
    lambda r: routes.health_executions(
        r, bot_id=None, status=None, days=7, page=1, per_page=5),
    lambda r: routes.health_execution_detail(r, "synthetic-id"),
    lambda r: routes.health_execution_page(r, "synthetic-id"),
    lambda r: routes.health_suspicious_running(r, limit=5),
])
def test_all_routes_enforce_admin_in_backend(monkeypatch, handler):
    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "true")
    with pytest.raises(HTTPException) as unauthenticated:
        handler(_access(monkeypatch, authenticated=False))
    assert unauthenticated.value.status_code == 401
    with pytest.raises(HTTPException) as forbidden:
        handler(_access(monkeypatch, admin=False))
    assert forbidden.value.status_code == 403


def test_disabled_never_constructs_repository(monkeypatch):
    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "false")
    forbidden = Mock(side_effect=AssertionError("database connection forbidden"))
    monkeypatch.setattr(routes, "HealthRepository", forbidden)
    request = _access(monkeypatch)
    assert routes.health_status(request) == {
        "enabled": False, "operational_state": "unverified"
    }
    for handler in (
        lambda: routes.health_overview(request),
        lambda: routes.health_bots(request, limit=5),
        lambda: routes.health_bot_detail(request, "ocorrencia_73", days=30, page=1),
        lambda: routes.health_executions(
            request, bot_id=None, status=None, days=7, page=1, per_page=5),
        lambda: routes.health_execution_detail(request, "synthetic-id"),
        lambda: routes.health_suspicious_running(request, limit=5),
    ):
        with pytest.raises(HTTPException) as disabled:
            handler()
        assert disabled.value.status_code == 503
    assert routes.dashboard_context(request)['state'] == "disabled"
    forbidden.assert_not_called()


def test_overview_no_runs_has_no_success_rate_or_healthy_bots():
    repo, cursor = repository_with(
        {"registered": 1, "observed": 0, "healthy": 0, "issues": 0,
         "suspicious": 0, "running": 0, "unverified": 1},
        {"runs_24h": 0, "successes_24h": 0, "completed_24h": 0,
         "avg_duration_seconds": None},
        {"last_confirmed_at_utc": None},
    )
    result = repo.overview(now=NOW)
    assert result["registered"] == 1
    assert result["healthy"] == 0
    assert result["unverified"] == 1
    assert result["success_rate_24h"] is None
    assert result["last_confirmed_at_utc"] is None
    assert len(cursor.calls) == 3


def test_overview_uses_only_completed_recorded_executions_for_rate():
    repo, _ = repository_with(
        {"registered": 2, "observed": 2, "healthy": 1, "issues": 1,
         "suspicious": 0, "running": 0, "unverified": 0},
        {"runs_24h": 3, "successes_24h": 1, "completed_24h": 2,
         "avg_duration_seconds": 2.5},
        {"last_confirmed_at_utc": NOW.replace(tzinfo=None)},
    )
    result = repo.overview(now=NOW)
    assert result["success_rate_24h"] == 50.0
    assert result["runs_24h"] == 3
    assert result["avg_duration_seconds"] == 2.5


def test_catalog_distinguishes_unverified_running_and_suspicious():
    repo, cursor = repository_with([
        {"identifier": "ocorrencia_73", "name": "OC73", "category": "ocorrencias",
         "origin": "scheduler:CWB", "status": "RUNNING",
         "started_at_utc": NOW.replace(tzinfo=None) - timedelta(hours=3),
         "last_execution_id": "synthetic-id", "duration_seconds": None},
        {"identifier": "other_bot", "name": "Outro", "category": "geral",
         "origin": None, "status": None, "started_at_utc": None,
         "last_execution_id": None, "duration_seconds": None},
    ])
    rows = repo.catalog(now=NOW)
    assert rows[0]["operational_state"] == "suspected"
    assert rows[0]["instrumented"] is True
    assert rows[0]["schedule_verified"] is False
    assert rows[1]["operational_state"] == "unverified"
    assert rows[1]["observed"] is False
    assert rows[1]["instrumented"] is False
    assert cursor.calls[0][1] == (50,)


@pytest.mark.parametrize("status,age_minutes,expected", [
    ("RUNNING", 10, "running"),
    ("RUNNING", 180, "suspected"),
    ("SUCCESS", 10, "success"),
    ("PARTIAL", 10, "partial"),
    ("FAILED", 10, "failed"),
    (None, 10, "unverified"),
])
def test_catalog_operational_states(status, age_minutes, expected):
    repo, _ = repository_with([{
        "identifier": "ocorrencia_73", "name": "OC73", "category": "ocorrencias",
        "origin": "scheduler:CWB", "status": status,
        "started_at_utc": (NOW - timedelta(minutes=age_minutes)).replace(tzinfo=None),
        "finished_at_utc": NOW.replace(tzinfo=None) if status in {"SUCCESS", "PARTIAL", "FAILED"} else None,
        "last_execution_id": "synthetic-id" if status else None,
        "duration_seconds": None,
    }])
    assert repo.catalog(now=NOW)[0]["operational_state"] == expected


def test_history_filters_and_pagination_are_parameterized():
    repo, cursor = repository_with(
        {"total": 21},
        [{"id": "synthetic-id", "bot_id": "ocorrencia_73", "bot_name": "OC73",
          "origin": "scheduler:CWB", "status": "PARTIAL",
          "started_at_utc": NOW.replace(tzinfo=None), "finished_at_utc": None,
          "duration_seconds": 1.5, "processed_count": 2,
          "success_count": 1, "error_count": 1}],
    )
    result = repo.history(bot_id="ocorrencia_73", status="PARTIAL",
                          since=NOW - timedelta(days=7), page=2, per_page=20)
    assert (result["total"], result["page"], len(result["items"])) == (21, 2, 1)
    assert cursor.calls[1][1][-2:] == (20, 20)
    assert cursor.calls[0][1][1:] == ("ocorrencia_73", "PARTIAL")
    assert "metadata_json" not in cursor.calls[1][0]


def test_bot_detail_uses_filtered_read_only_activity_and_history(monkeypatch):
    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "true")
    request = _access(monkeypatch)
    repo = Mock()
    repo.catalog.return_value = [{
        "identifier": "ocorrencia_73", "name": "OC73", "origin": "scheduler:CWB",
        "observed": True, "operational_state": "success",
        "instrumented": True, "schedule_verified": False,
        "last_execution_at_utc": NOW.replace(tzinfo=None),
        "last_duration_seconds": 2.0,
    }]
    repo.bot_activity.return_value = {"executions": 1, "successes": 1,
                                      "partials": 0, "failures": 0,
                                      "avg_duration_seconds": 2.0}
    repo.history.return_value = {"items": [], "total": 1,
                                 "page": 2, "per_page": 20}
    monkeypatch.setattr(routes, "HealthRepository", lambda: repo)
    result = routes.health_bot_detail(request, "ocorrencia_73", days=30, page=2)
    assert result["bot"]["identifier"] == "ocorrencia_73"
    assert result["activity"]["executions"] == 1
    assert repo.catalog.call_args.kwargs["bot_id"] == "ocorrencia_73"
    assert repo.history.call_args.kwargs["bot_id"] == "ocorrencia_73"
    assert repo.history.call_args.kwargs["page"] == 2
    page = routes.health_bot_page(request, "ocorrencia_73", days=30, page=2)
    assert page.status_code == 200
    assert b"ocorrencia_73" in page.body


def test_bot_activity_is_aggregate_and_parameterized():
    repo, cursor = repository_with({
        "executions": 3, "successes": 1, "partials": 1,
        "failures": 1, "avg_duration_seconds": 4.5,
    })
    result = repo.bot_activity(bot_id="ocorrencia_73", since=NOW - timedelta(days=30))
    assert result == {"executions": 3, "successes": 1, "partials": 1,
                      "failures": 1, "avg_duration_seconds": 4.5}
    assert cursor.calls[0][1][0] == "ocorrencia_73"


def test_suspicious_endpoint_exposes_only_minimal_fields(monkeypatch):
    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "true")
    request = _access(monkeypatch)
    repo = Mock()
    repo.suspicious_running.return_value = [{
        "id": "synthetic-id", "bot_id": "ocorrencia_73",
        "origin": "cookie=secret", "started_at_utc": NOW.replace(tzinfo=None),
        "raw_payload": "token=secret",
    }]
    monkeypatch.setattr(routes, "HealthRepository", lambda: repo)
    response = routes.health_suspicious_running(request, limit=5)
    assert response["classification"] == "suspected_interruption_only"
    assert response["items"][0]["observability_state"] == "SUSPECTED_INTERRUPTION"
    assert "secret" not in repr(response)


@pytest.mark.parametrize("options", [
    {"bot_id": "bad%id"}, {"status": "SUCCESS' OR 1=1"},
    {"page": 0}, {"page": 1001}, {"per_page": 101},
])
def test_invalid_history_filters_rejected_before_query(options):
    repo, cursor = repository_with()
    with pytest.raises(ValueError):
        repo.history(since=NOW, **options)
    assert cursor.calls == []


def test_detail_redacts_legacy_sensitive_content():
    repo, cursor = repository_with({
        "id": "synthetic-id", "bot_id": "ocorrencia_73", "bot_name": "OC73",
        "origin": "scheduler:CWB", "status": "FAILED",
        "summary": "token=secret", "error_message": "senha=secret",
        "metadata_json": '{"cookie":"secret"}',
    })
    result = repo.execution_detail("synthetic-id")
    assert result["summary"] is None
    assert result["error_message"] is None
    assert result["indicators"] == {}
    assert "secret" not in repr(result)
    assert cursor.calls[0][1] == ("synthetic-id",)


def test_api_validates_filters_and_hides_database_errors(monkeypatch):
    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "true")
    repository = Mock()
    repository.history.side_effect = TimeoutError("password=secret")
    monkeypatch.setattr(routes, "HealthRepository", lambda: repository)
    request = _access(monkeypatch)
    with pytest.raises(HTTPException) as response:
        routes.health_executions(request, bot_id=None, status=None,
                                 days=7, page=1, per_page=25)
    assert response.value.status_code == 503
    assert "secret" not in response.value.detail


def test_dashboard_states_and_synthetic_render(monkeypatch):
    monkeypatch.setattr(routes, "obter_client", lambda request: object())
    monkeypatch.setattr(routes, "is_admin", lambda request: True)
    request = SimpleNamespace(session={"ssw_session_id": "synthetic"},
                              url=SimpleNamespace(path="/admin/observabilidade"))
    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "false")
    assert routes.dashboard_context(request)["state"] == "disabled"
    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "true")
    monkeypatch.setattr(routes, "HealthRepository", Mock(side_effect=TimeoutError("secret")))
    assert routes.dashboard_context(request)["state"] == "error"

    env = Environment(
        loader=FileSystemLoader(str(Path(__file__).resolve().parents[1] / "web/templates")),
        autoescape=select_autoescape(["html"]),
    )
    metrics = {"scope": "Jobs web", "total": 0, "rate": None, "running": 0,
               "errors": 0, "daily": [], "recent": [], "avg_seconds": None}
    health = {"state": "ready", "overview": {
        "registered": 1, "observed": 0, "healthy": 0, "issues": 0,
        "suspicious": 0, "running": 0, "unverified": 1,
        "runs_24h": 0, "success_rate_24h": None,
        "avg_duration_seconds": None, "last_confirmed_at_utc": None},
        "catalog": [], "history": {"items": [], "total": 0, "page": 1, "per_page": 20}}
    html = env.get_template("admin_observabilidade.html").render(
        request=request, is_admin=True, metrics=metrics, dias=7, modulo="", status="",
        health=health, health_bot="", health_status="", health_days=7,
    )
    assert "Saúde das automações" in html
    assert "Sem execuções no período" in html
    assert "Uma ausência de registros não indica sucesso" in html
    assert "NEXUS / HEALTH CENTER" in html

    disabled_html = env.get_template("admin_observabilidade.html").render(
        request=request, is_admin=True, metrics=metrics, dias=7, modulo="", status="",
        health={"state": "disabled"},
    )
    assert "Monitoramento desativado" in disabled_html
    error_html = env.get_template("admin_observabilidade.html").render(
        request=request, is_admin=True, metrics=metrics, dias=7, modulo="", status="",
        health={"state": "error"},
    )
    assert "Erro na consulta" in error_html


def test_detail_template_contains_only_sanitized_indicators():
    env = Environment(
        loader=FileSystemLoader(str(Path(__file__).resolve().parents[1] / "web/templates")),
        autoescape=select_autoescape(["html"]),
    )
    request = SimpleNamespace(session={"ssw_session_id": "synthetic"},
                              url=SimpleNamespace(path="/admin/health-center/executions/id/view"))
    detail = public_execution({
        "id": "synthetic-id", "bot_id": "ocorrencia_73", "bot_name": "OC73",
        "origin": "scheduler:CWB", "status": "PARTIAL",
        "started_at_utc": NOW.replace(tzinfo=None),
        "finished_at_utc": NOW.replace(tzinfo=None),
        "duration_seconds": 1.0, "processed_count": 2,
        "success_count": 1, "error_count": 1,
        "summary": "OC73: OP455=2; filtrados=2; OP101=2; erros=1.",
        "error_message": None,
        "metadata_json": '{"total_op455":2,"dry_run":true}',
        "payload": "token=secret",
    }, detail=True)
    html = env.get_template("health_execution_detail.html").render(
        request=request, is_admin=True, execution=detail,
    )
    assert "total_op455" in html
    assert "token=secret" not in html

    bot_html = env.get_template("health_bot_detail.html").render(
        request=request, is_admin=True, detail={
            "bot": {"name": "OC73", "identifier": "ocorrencia_73", "origin": None,
                    "operational_state": "unverified", "instrumented": True,
                    "schedule_verified": False, "last_execution_at_utc": None,
                    "last_duration_seconds": None},
            "activity": {"executions": 0, "successes": 0, "partials": 0,
                         "failures": 0, "avg_duration_seconds": None},
            "history": {"items": [], "total": 0, "page": 1, "per_page": 20},
            "days": 30,
        },
    )
    assert "Sem execuções registradas" in bot_html
    assert "Agendamento:" in bot_html


def test_public_execution_drops_arbitrary_server_fields():
    result = public_execution({"id": "synthetic-id", "bot_id": "ocorrencia_73",
                               "status": "SUCCESS", "cookie": "secret",
                               "payload": "cpf 12345678901"})
    assert "secret" not in repr(result)
    assert "12345678901" not in repr(result)


def test_existing_nexus_page_receives_health_context(monkeypatch):
    from web import routes as web_routes

    request = SimpleNamespace(session={"ssw_session_id": "synthetic"},
                              url=SimpleNamespace(path="/admin/observabilidade"))
    monkeypatch.setattr(web_routes, "obter_client", lambda request: object())
    monkeypatch.setattr(web_routes, "is_admin", lambda request: True)
    monkeypatch.setattr(web_routes, "snapshot", lambda **kwargs: {
        "scope": "Jobs web", "total": 0, "rate": None, "running": 0,
        "errors": 0, "daily": [], "recent": [], "avg_seconds": None,
    })
    captured = []
    def health_context(_request, **kwargs):
        captured.append(kwargs)
        return {"state": "disabled"}
    monkeypatch.setattr(web_routes, "dashboard_context", health_context)
    response = web_routes.nexus_observabilidade(
        request, dias=7, modulo="", status="", health_bot="ocorrencia_73",
        health_status="FAILED", health_days=30, health_page=2,
    )
    assert response.status_code == 200
    assert captured == [{"bot_id": "ocorrencia_73", "status": "FAILED",
                         "days": 30, "page": 2}]
    assert b"Health Center" in response.body
