"""Homologação HTTP local: app mínimo, sessão sintética e zero startups operacionais."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from modules.health_center import routes as health_routes
from modules.ocorrencia_73 import routes as oc73_routes
from web import admin_access, routes as web_routes


class FakeHealthRepository:
    created = 0
    calls = []
    unavailable = False

    def __init__(self):
        type(self).created += 1
        if type(self).unavailable:
            raise TimeoutError("password=synthetic-secret")

    def overview(self, **kwargs):
        return {
            "registered": 1, "observed": 0, "healthy": 0, "issues": 0,
            "suspicious": 0, "running": 0, "unverified": 1,
            "runs_24h": 0, "success_rate_24h": None,
            "avg_duration_seconds": None, "last_confirmed_at_utc": None,
        }

    def catalog(self, **kwargs):
        type(self).calls.append(("catalog", kwargs))
        return [{
            "identifier": "ocorrencia_73", "name": "OC73", "category": "ocorrencias",
            "origin": None, "registered": True, "instrumented": True,
            "observed": False, "schedule_verified": False,
            "operational_state": "unverified", "last_execution_at_utc": None,
            "last_duration_seconds": None, "next_execution": None,
        }]

    def history(self, **kwargs):
        type(self).calls.append(("history", kwargs))
        return {"items": [], "total": 0, "page": kwargs.get("page", 1),
                "per_page": kwargs.get("per_page", 25)}

    def bot_activity(self, **kwargs):
        return {"executions": 0, "successes": 0, "partials": 0,
                "failures": 0, "avg_duration_seconds": None}

    def execution_detail(self, execution_id):
        if execution_id != "synthetic-id":
            return None
        return {
            "id": execution_id, "bot_id": "ocorrencia_73", "bot_name": "OC73",
            "origin": "scheduler:CWB", "status": "RUNNING",
            "started_at_utc": datetime(2026, 11, 10, 12, tzinfo=timezone.utc),
            "finished_at_utc": None, "duration_seconds": None,
            "processed_count": 0, "success_count": 0, "error_count": 0,
            "summary": None, "error_message": None, "indicators": {},
        }

    def suspicious_running(self, **kwargs):
        return []


class FakeOC73Repository:
    def listar_execucoes(self, **kwargs):
        return {"itens": [], "total": 0, "pagina": 1,
                "por_pagina": kwargs["por_pagina"], "total_paginas": 1}

    def ultimas_execucoes_por_fluxo(self):
        return {"CWB": None, "BIG": None}

    def execucoes_anormais(self, minutos):
        return []


@pytest.fixture
def isolated_app(monkeypatch):
    """Inclui só leituras administrativas; nunca importa web.main/startup."""
    FakeHealthRepository.created = 0
    FakeHealthRepository.calls = []
    FakeHealthRepository.unavailable = False
    monkeypatch.setenv("NEXUS_ADMIN_USERS", "synthetic_admin")
    monkeypatch.setenv("OC73_OBSERVABILIDADE_USUARIOS", "synthetic_admin,oc73_viewer")
    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "false")

    def fake_client(request):
        username = request.session.get("synthetic_user")
        return SimpleNamespace(usuario=username) if username else None

    monkeypatch.setattr(web_routes, "obter_client", fake_client)
    monkeypatch.setattr(health_routes, "obter_client", fake_client)
    monkeypatch.setattr(oc73_routes, "obter_client", fake_client)
    monkeypatch.setattr(oc73_routes, "Ocorrencia73ObservabilidadeRepository", FakeOC73Repository)
    monkeypatch.setattr(admin_access, "obter_client", fake_client)
    monkeypatch.setattr(web_routes, "is_admin", admin_access.is_admin)
    monkeypatch.setattr(health_routes, "is_admin", admin_access.is_admin)
    monkeypatch.setattr(health_routes, "HealthRepository", FakeHealthRepository)
    monkeypatch.setattr(web_routes, "snapshot", lambda **kwargs: {
        "scope": "Jobs web sintéticos", "total": 0, "rate": None,
        "running": 0, "errors": 0, "daily": [], "recent": [],
        "avg_seconds": None,
    })

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="synthetic-test-key")
    app.add_api_route(
        "/admin/observabilidade", web_routes.nexus_observabilidade,
        methods=["GET"], response_class=HTMLResponse,
    )
    app.include_router(health_routes.router)
    app.include_router(oc73_routes.router)

    @app.get("/_synthetic_session/{username}")
    async def synthetic_session(request: Request, username: str):
        request.session["synthetic_user"] = username
        request.session["ssw_session_id"] = "synthetic-session"
        request.session["oc73_observabilidade"] = username in {"synthetic_admin", "oc73_viewer"}
        return {"synthetic": True}

    assert app.router.on_startup == []
    return app


def test_monitoring_sidebar_and_permissions_on_both_pages(isolated_app):
    with TestClient(isolated_app) as client:
        assert client.get("/oc73/observabilidade", follow_redirects=False).status_code == 303
        client.get("/_synthetic_session/viewer")
        assert client.get("/oc73/observabilidade").status_code == 403
        assert client.get("/admin/observabilidade").status_code == 403

        client.get("/_synthetic_session/synthetic_admin")
        for path in ("/admin/observabilidade", "/oc73/observabilidade"):
            response = client.get(path)
            assert response.status_code == 200
            assert 'href="/admin/observabilidade" data-nexus-path="/admin/observabilidade"' in response.text
            assert 'href="/oc73/observabilidade" data-nexus-path="/oc73/observabilidade"' in response.text
            assert response.text.count('data-nexus-path="/admin/observabilidade"') == 1
            assert response.text.count('data-nexus-path="/oc73/observabilidade"') == 1
            assert 'id="nexus-menu-toggle"' in response.text

        client.get("/_synthetic_session/oc73_viewer")
        assert client.get("/admin/observabilidade").status_code == 403
        oc73_page = client.get("/oc73/observabilidade")
        assert oc73_page.status_code == 200
        assert 'data-nexus-path="/admin/observabilidade"' not in oc73_page.text


def test_http_authentication_admin_and_disabled_state(isolated_app):
    protected_paths = (
        "/admin/health-center/status", "/admin/health-center/overview",
        "/admin/health-center/bots", "/admin/health-center/bots/ocorrencia_73",
        "/admin/health-center/bots/ocorrencia_73/view",
        "/admin/health-center/executions",
        "/admin/health-center/executions/synthetic-id",
        "/admin/health-center/executions/synthetic-id/view",
        "/admin/health-center/suspicious-running",
    )
    with TestClient(isolated_app) as client:
        assert client.get("/admin/observabilidade", follow_redirects=False).status_code == 303
        assert all(client.get(path).status_code == 401 for path in protected_paths)
        client.get("/_synthetic_session/viewer")
        assert client.get("/admin/observabilidade").status_code == 403
        assert all(client.get(path).status_code == 403 for path in protected_paths)
        client.get("/_synthetic_session/synthetic_admin")
        page = client.get("/admin/observabilidade")
        assert page.status_code == 200
        assert "Monitoramento desativado" in page.text
        assert client.get("/admin/health-center/status").json()["enabled"] is False
        assert all(client.get(path).status_code == 503 for path in protected_paths[1:])
        assert FakeHealthRepository.created == 0


def test_http_filters_pagination_details_and_empty_state(isolated_app, monkeypatch):
    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "true")
    with TestClient(isolated_app) as client:
        client.get("/_synthetic_session/synthetic_admin")
        page = client.get("/admin/observabilidade?health_bot=ocorrencia_73&health_status=FAILED&health_days=30&health_page=2")
        assert page.status_code == 200
        assert "Sem execuções no período" in page.text
        assert any(kind == "history" and args["page"] == 2 and args["status"] == "FAILED"
                   and args["bot_id"] == "ocorrencia_73"
                   for kind, args in FakeHealthRepository.calls)
        response = client.get("/admin/health-center/executions?bot_id=ocorrencia_73&status=SUCCESS&days=30&page=2&per_page=5")
        assert response.status_code == 200
        assert response.json()["page"] == 2
        assert client.get("/admin/health-center/executions?status=INVALID").status_code == 422
        assert client.get("/admin/health-center/executions?per_page=101").status_code == 422
        assert client.get("/admin/health-center/bots/ocorrencia_73/view").status_code == 200
        detail = client.get("/admin/health-center/executions/synthetic-id/view")
        assert detail.status_code == 200
        assert "não foi confirmado" in detail.text or "não confirmado" in detail.text
        assert client.get("/admin/health-center/executions/unknown-id").status_code == 404


def test_http_health_database_failure_is_sanitized(isolated_app, monkeypatch):
    monkeypatch.setenv("HEALTH_CENTER_ENABLED", "true")
    FakeHealthRepository.unavailable = True
    with TestClient(isolated_app) as client:
        client.get("/_synthetic_session/synthetic_admin")
        page = client.get("/admin/observabilidade")
        assert page.status_code == 200
        assert "Erro na consulta" in page.text
        response = client.get("/admin/health-center/overview")
        assert response.status_code == 503
        assert "synthetic-secret" not in response.text
