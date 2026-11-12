from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from modules.health_center import routes


class Request:
    pass


class Repository:
    def overview(self, now):
        return {"total": 1, "healthy": 1, "degraded": 0, "down_count": 0,
                "unknown_count": 0, "maintenance": 0, "stale": 0}

    def catalog(self, now, limit):
        return [{"identifier": "ssw_web", "current_state": "HEALTHY"}]

    def incidents(self, limit):
        return []

    def detail(self, dependency_id, now):
        return {"dependency": {"identifier": dependency_id}, "checks": [], "incidents": []}


def test_anonymous_and_non_admin_are_denied_before_repository(monkeypatch):
    monkeypatch.setattr(routes, "dependency_monitor_enabled", lambda: True)
    monkeypatch.setattr(routes, "obter_client", lambda _request: None)
    monkeypatch.setattr(
        routes, "DependencyRepository",
        lambda: (_ for _ in ()).throw(AssertionError("repository accessed")),
    )
    with pytest.raises(HTTPException) as error:
        routes.dependency_overview(Request())
    assert error.value.status_code == 401

    monkeypatch.setattr(routes, "obter_client", lambda _request: object())
    monkeypatch.setattr(routes, "is_admin", lambda _request: False)
    with pytest.raises(HTTPException) as error:
        routes.dependency_overview(Request())
    assert error.value.status_code == 403


def test_disabled_dashboard_never_opens_database(monkeypatch):
    monkeypatch.setattr(routes, "obter_client", lambda _request: object())
    monkeypatch.setattr(routes, "is_admin", lambda _request: True)
    monkeypatch.setattr(routes, "dependency_monitor_enabled", lambda: False)
    monkeypatch.setattr(
        routes, "DependencyRepository",
        lambda: (_ for _ in ()).throw(AssertionError("database accessed")),
    )
    context = routes.dependency_dashboard_context(Request())
    assert context == {
        "state": "disabled", "overview": None, "catalog": [], "incidents": [],
    }


def test_admin_receives_sanitized_dependency_context(monkeypatch):
    monkeypatch.setattr(routes, "obter_client", lambda _request: object())
    monkeypatch.setattr(routes, "is_admin", lambda _request: True)
    monkeypatch.setattr(routes, "dependency_monitor_enabled", lambda: True)
    monkeypatch.setattr(routes, "DependencyRepository", Repository)
    context = routes.dependency_dashboard_context(Request())
    assert context["state"] == "ready"
    assert context["catalog"][0]["identifier"] == "ssw_web"
    result = routes.dependency_detail(Request(), "ssw_web")
    assert result["dependency"]["identifier"] == "ssw_web"


def test_dependency_route_patterns_are_admin_health_center_routes():
    paths = {route.path for route in routes.router.routes}
    assert "/admin/health-center/dependencies" in paths
    assert "/admin/health-center/dependencies/{dependency_id}" in paths
    assert "/admin/health-center/dependencies/{dependency_id}/view" in paths
