import json

import pytest
from fastapi import HTTPException

from modules.local_agent.config import (
    LocalAgentConfigurationError,
    load_executor_settings,
    load_server_settings,
)
from modules.local_agent import routes
from modules.local_agent.executor import AgentClient


TOKEN = "token-seguro-para-testes-com-mais-de-32-caracteres"


def configure_server(monkeypatch, tmp_path, *, enabled="true"):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ENABLED", enabled)
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_DB", str(tmp_path / "agent.sqlite3"))
    monkeypatch.setenv(
        "NEXUS_LOCAL_AGENT_TOKENS_JSON", json.dumps({"windows-cwb-01": TOKEN})
    )
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ALLOWED_TASKS", "system.ping")
    monkeypatch.setenv(
        "NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON",
        json.dumps({"windows-cwb-01": ["system.ping"]}),
    )


def test_server_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("NEXUS_LOCAL_AGENT_ENABLED", raising=False)
    monkeypatch.delenv("NEXUS_LOCAL_AGENT_TOKENS_JSON", raising=False)
    assert load_server_settings().enabled is False


def test_enabled_server_requires_tokens(monkeypatch):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ENABLED", "true")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_TOKENS_JSON", "{}")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON", "{}")
    with pytest.raises(LocalAgentConfigurationError, match="sem tokens"):
        load_server_settings()


def test_enabled_server_requires_per_agent_allowlist(monkeypatch):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ENABLED", "true")
    monkeypatch.setenv(
        "NEXUS_LOCAL_AGENT_TOKENS_JSON", json.dumps({"windows-cwb-01": TOKEN})
    )
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ALLOWED_TASKS", "system.ping")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON", "{}")
    with pytest.raises(LocalAgentConfigurationError, match="allowlist própria"):
        load_server_settings()


def test_executor_requires_https_unless_explicitly_overridden(monkeypatch):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_SERVER_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ID", "windows-cwb-01")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_TOKEN", TOKEN)
    monkeypatch.delenv("NEXUS_LOCAL_AGENT_ALLOW_INSECURE_HTTP", raising=False)
    with pytest.raises(LocalAgentConfigurationError, match="exige HTTPS"):
        load_executor_settings()
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ALLOW_INSECURE_HTTP", "true")
    assert load_executor_settings().allow_insecure_http is True


def test_executor_uses_explicit_proxy_and_ca_without_user_profile(monkeypatch, tmp_path):
    ca_bundle = tmp_path / "corporate-ca.pem"
    ca_bundle.write_text("synthetic-ca", encoding="utf-8")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_SERVER_URL", "https://nexus.example.test")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ID", "windows-cwb-01")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_TOKEN", TOKEN)
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ALLOW_INSECURE_HTTP", "false")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_HTTPS_PROXY", "http://proxy.example.test:8080")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_CA_BUNDLE", str(ca_bundle))
    settings = load_executor_settings()
    client = AgentClient(settings)
    assert client.session.proxies["https"] == "http://proxy.example.test:8080"
    assert client.session.verify == str(ca_bundle.resolve())
    assert "proxy.example.test" not in repr(settings)


def test_agent_claim_authenticates_and_filters_capabilities(monkeypatch, tmp_path):
    configure_server(monkeypatch, tmp_path)
    response = routes.claim(
        routes.ClaimRequest(capabilities=["system.ping", "shell.execute"]),
        x_nexus_agent_id="windows-cwb-01",
        authorization=f"Bearer {TOKEN}",
    )
    assert response["job"] is None
    agents = routes._repository(load_server_settings()).list_agents()
    assert agents[0]["capabilities"] == ["system.ping"]


def test_agent_rejects_wrong_token_without_revealing_identity(monkeypatch, tmp_path):
    configure_server(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as error:
        routes.claim(
            routes.ClaimRequest(capabilities=["system.ping"]),
            x_nexus_agent_id="windows-cwb-01",
            authorization="Bearer incorreto",
        )
    assert error.value.status_code == 401
    assert "inválidas" in error.value.detail


def test_agent_protocol_claim_logs_heartbeat_and_complete(monkeypatch, tmp_path):
    configure_server(monkeypatch, tmp_path)
    settings = load_server_settings()
    repository = routes._repository(settings)
    job_id = repository.enqueue(task="system.ping", payload={"message": "e2e"})
    headers = {
        "x_nexus_agent_id": "windows-cwb-01",
        "authorization": f"Bearer {TOKEN}",
    }
    claimed = routes.claim(
        routes.ClaimRequest(capabilities=["system.ping"]), **headers
    )["job"]
    assert claimed["id"] == job_id
    lease = {"job_id": job_id, "lease_token": claimed["lease_token"]}
    assert routes.logs(
        routes.LogsRequest(
            **lease,
            entries=[routes.LogEntry(level="INFO", message="execução sintética")],
        ),
        **headers,
    ) == {"accepted": 1}
    heartbeat = routes.heartbeat(
        routes.HeartbeatRequest(**lease, progress_current=1, progress_total=1),
        **headers,
    )
    assert heartbeat["cancellation_requested"] is False
    assert routes.complete(
        routes.CompleteRequest(**lease, succeeded=True, result={"ok": True}),
        **headers,
    ) == {"status": "SUCCEEDED"}
    assert repository.get_job(job_id)["status"] == "SUCCEEDED"


def test_disabled_routes_do_not_create_database(monkeypatch, tmp_path):
    configure_server(monkeypatch, tmp_path, enabled="false")
    with pytest.raises(HTTPException) as error:
        routes.claim(
            routes.ClaimRequest(capabilities=["system.ping"]),
            x_nexus_agent_id="windows-cwb-01",
            authorization=f"Bearer {TOKEN}",
        )
    assert error.value.status_code == 503
    assert not (tmp_path / "agent.sqlite3").exists()


def test_admin_enqueue_requires_session_role_and_csrf(monkeypatch, tmp_path):
    configure_server(monkeypatch, tmp_path)
    request = type("RequestStub", (), {"session": {}})()
    monkeypatch.setattr(routes, "obter_client", lambda value: object())
    monkeypatch.setattr(routes, "is_admin", lambda value: True)
    overview = routes.admin_overview(request, limit=50)
    token = overview["csrf_token"]

    with pytest.raises(HTTPException) as error:
        routes.admin_enqueue(
            request,
            routes.EnqueueRequest(task="system.ping", payload={}),
            x_csrf_token="incorreto",
        )
    assert error.value.status_code == 403

    created = routes.admin_enqueue(
        request,
        routes.EnqueueRequest(task="system.ping", payload={"message": "teste"}),
        x_csrf_token=token,
    )
    assert created["status"] == "QUEUED"
    assert routes.admin_job(request, created["job_id"])["payload"] == {
        "message": "teste"
    }


def test_admin_cannot_enqueue_task_outside_allowlist(monkeypatch, tmp_path):
    configure_server(monkeypatch, tmp_path)
    request = type("RequestStub", (), {"session": {"local_agent_csrf": "csrf"}})()
    monkeypatch.setattr(routes, "obter_client", lambda value: object())
    monkeypatch.setattr(routes, "is_admin", lambda value: True)
    with pytest.raises(HTTPException) as error:
        routes.admin_enqueue(
            request,
            routes.EnqueueRequest(task="shell.execute", payload={}),
            x_csrf_token="csrf",
        )
    assert error.value.status_code == 422
