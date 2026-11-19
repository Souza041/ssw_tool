import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from modules.local_agent import routes as agent_routes
from modules.local_agent.config import LocalAgentConfigurationError, load_server_settings
from modules.local_agent.remote_auth import (
    RemoteAuthBroker,
    RemoteAuthConflict,
    RemoteAuthRateLimited,
    RemoteAuthUnavailable,
    RemoteCredentials,
    WindowsRemoteAuthExecutor,
    broker,
    identity_fingerprint,
    response_proof,
)
from web import routes as web_routes
from web.session_store import CLIENTS, RemoteAuthenticatedClient


TOKEN = "remote-auth-agent-token-synthetic-with-more-than-32-characters"
AGENT_ID = "windows-corporativo-01"
PASSWORD = "synthetic-password-never-persist"


@pytest.fixture(autouse=True)
def clean_runtime_state():
    broker.reset()
    CLIENTS.clear()
    yield
    broker.reset()
    CLIENTS.clear()


def configure(monkeypatch, tmp_path, *, enabled="true", agent_id=AGENT_ID):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ENABLED", "true")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_DB", str(tmp_path / "agent.sqlite3"))
    monkeypatch.setenv(
        "NEXUS_LOCAL_AGENT_TOKENS_JSON", json.dumps({AGENT_ID: TOKEN})
    )
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ALLOWED_TASKS", "system.ping")
    monkeypatch.setenv(
        "NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON",
        json.dumps({AGENT_ID: ["system.ping"]}),
    )
    monkeypatch.setenv("NEXUS_SSW_REMOTE_AUTH_ENABLED", enabled)
    monkeypatch.setenv("NEXUS_SSW_REMOTE_AUTH_AGENT_ID", agent_id)
    monkeypatch.setenv("NEXUS_SSW_REMOTE_AUTH_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("NEXUS_SSW_REMOTE_AUTH_REQUEST_TTL_SECONDS", "5")
    monkeypatch.setenv("NEXUS_SSW_REMOTE_AUTH_PER_MINUTE", "3")
    monkeypatch.setenv("NEXUS_SSW_REMOTE_AUTH_MAX_PENDING", "10")


def request_with_session(**values):
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/login",
        "headers": [],
        "session": dict(values),
    })


def test_remote_auth_flag_is_independent_and_requires_an_authorized_agent(
    monkeypatch, tmp_path
):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("NEXUS_SSW_DELEGATION_ENABLED", "false")
    assert load_server_settings().remote_auth_enabled is True
    monkeypatch.setenv("NEXUS_SSW_REMOTE_AUTH_AGENT_ID", "outro-agente")
    with pytest.raises(LocalAgentConfigurationError, match="Agente autorizado"):
        load_server_settings()


def claim_from_agent():
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        claimed = agent_routes._remote_auth_claim_impl(
            x_nexus_agent_id=AGENT_ID,
            authorization=f"Bearer {TOKEN}",
        )["request"]
        if claimed is not None:
            return claimed
        time.sleep(0.005)
    raise AssertionError("solicitação remota não ficou disponível")


def complete_from_agent(claimed, *, accepted, proof=None):
    fingerprint = claimed["identity_fingerprint"]
    signed = proof or response_proof(
        TOKEN,
        request_id=claimed["request_id"],
        challenge=claimed["challenge"],
        identity=fingerprint,
        agent_id=AGENT_ID,
        accepted=accepted,
    )
    return agent_routes._remote_auth_complete_impl(
        agent_routes.RemoteAuthCompleteRequest(
            request_id=claimed["request_id"],
            lease_token=claimed["lease_token"],
            accepted=accepted,
            identity_fingerprint=fingerprint,
            proof=signed,
        ),
        x_nexus_agent_id=AGENT_ID,
        authorization=f"Bearer {TOKEN}",
    )


def test_remote_login_valid_creates_web_session_without_vps_ssw_or_sqlite(
    monkeypatch, tmp_path
):
    configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        web_routes.SSWClient,
        "login",
        lambda self: pytest.fail("a VPS não pode autenticar diretamente no SSW"),
    )
    request = request_with_session(login_csrf="csrf-safe", remote_auth_binding="b" * 48)
    output = {}

    def login():
        output["response"] = web_routes.login_run(
            request,
            dominio="ROD",
            cpf="123.456.789-01",
            usuario="synthetic.user",
            senha=PASSWORD,
            unidade="CWB",
            login_csrf="csrf-safe",
        )

    thread = threading.Thread(target=login)
    thread.start()
    claimed = claim_from_agent()
    assert claimed["credentials"]["senha"] == PASSWORD
    assert complete_from_agent(claimed, accepted=True) == {"accepted": True}
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert output["response"].status_code == 303
    session_id = request.session["ssw_session_id"]
    client = CLIENTS[session_id]
    assert isinstance(client, RemoteAuthenticatedClient)
    assert client.usuario == "synthetic.user"
    assert client.senha == ""
    assert PASSWORD not in repr(client)
    assert not (tmp_path / "agent.sqlite3").exists()


def test_remote_login_invalid_does_not_create_session(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    request = request_with_session(login_csrf="csrf-safe", remote_auth_binding="c" * 48)
    output = {}

    thread = threading.Thread(target=lambda: output.setdefault(
        "response",
        web_routes.login_run(
            request, dominio="ROD", cpf="12345678901",
            usuario="invalid.user", senha=PASSWORD, unidade="CWB",
            login_csrf="csrf-safe",
        ),
    ))
    thread.start()
    claimed = claim_from_agent()
    complete_from_agent(claimed, accepted=False)
    thread.join(timeout=2)

    assert "ssw_session_id" not in request.session
    assert PASSWORD not in output["response"].body.decode("utf-8")
    assert "Não foi possível autenticar" in output["response"].body.decode("utf-8")


def test_remote_auth_rejects_wrong_agent_and_wrong_proof(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as unauthorized:
        agent_routes._remote_auth_claim_impl(
            x_nexus_agent_id="windows-nao-autorizado",
            authorization=f"Bearer {TOKEN}",
        )
    assert unauthorized.value.status_code == 401

    second_token = "second-authorized-agent-token-with-more-than-32-characters"
    monkeypatch.setenv(
        "NEXUS_LOCAL_AGENT_TOKENS_JSON",
        json.dumps({AGENT_ID: TOKEN, "windows-secundario-01": second_token}),
    )
    monkeypatch.setenv(
        "NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON",
        json.dumps({
            AGENT_ID: ["system.ping"],
            "windows-secundario-01": ["system.ping"],
        }),
    )
    with pytest.raises(HTTPException) as forbidden:
        agent_routes._remote_auth_claim_impl(
            x_nexus_agent_id="windows-secundario-01",
            authorization=f"Bearer {second_token}",
        )
    assert forbidden.value.status_code == 403

    request_id = broker.create(
        RemoteCredentials("ROD", "12345678901", "user", PASSWORD, "CWB"),
        session_binding="d" * 48,
        ttl_seconds=5,
        per_minute=3,
        max_pending=10,
    )
    claimed = claim_from_agent()
    assert request_id == claimed["request_id"]
    with pytest.raises(HTTPException) as invalid_proof:
        complete_from_agent(claimed, accepted=True, proof="0" * 64)
    assert invalid_proof.value.status_code == 409


def test_remote_auth_completion_is_single_use_and_session_isolated():
    local = RemoteAuthBroker()
    first = local.create(
        RemoteCredentials("ROD", "12345678901", "first", PASSWORD, "CWB"),
        session_binding="e" * 48, ttl_seconds=5, per_minute=3, max_pending=10,
    )
    claimed = local.claim(agent_id=AGENT_ID)
    proof = response_proof(
        TOKEN, request_id=first, challenge=claimed["challenge"],
        identity=claimed["identity_fingerprint"], agent_id=AGENT_ID, accepted=True,
    )
    options = dict(
        request_id=first, lease_token=claimed["lease_token"], agent_id=AGENT_ID,
        accepted=True, identity=claimed["identity_fingerprint"], proof=proof,
        secret=TOKEN,
    )
    local.complete(**options)
    with pytest.raises(RemoteAuthConflict):
        local.complete(**options)
    with pytest.raises(RemoteAuthConflict):
        local.wait_and_consume(first, session_binding="f" * 48, timeout_seconds=0.01)
    result = local.wait_and_consume(
        first, session_binding="e" * 48, timeout_seconds=0.01
    )
    assert result.accepted is True


def test_concurrent_remote_logins_keep_identity_and_session_isolated():
    local = RemoteAuthBroker()
    request_ids = [
        local.create(
            RemoteCredentials("ROD", "12345678901", user, PASSWORD, "CWB"),
            session_binding=binding,
            ttl_seconds=5,
            per_minute=3,
            max_pending=10,
        )
        for user, binding in (("first.user", "i" * 48), ("second.user", "j" * 48))
    ]
    claims = [local.claim(agent_id=AGENT_ID), local.claim(agent_id=AGENT_ID)]
    assert [item["request_id"] for item in claims] == request_ids
    for claimed in reversed(claims):
        proof = response_proof(
            TOKEN,
            request_id=claimed["request_id"],
            challenge=claimed["challenge"],
            identity=claimed["identity_fingerprint"],
            agent_id=AGENT_ID,
            accepted=True,
        )
        local.complete(
            request_id=claimed["request_id"],
            lease_token=claimed["lease_token"],
            agent_id=AGENT_ID,
            accepted=True,
            identity=claimed["identity_fingerprint"],
            proof=proof,
            secret=TOKEN,
        )
    first = local.wait_and_consume(
        request_ids[0], session_binding="i" * 48, timeout_seconds=0.01
    )
    second = local.wait_and_consume(
        request_ids[1], session_binding="j" * 48, timeout_seconds=0.01
    )
    assert (first.usuario, second.usuario) == ("first.user", "second.user")


def test_remote_auth_timeout_expiration_rate_limit_and_concurrency():
    local = RemoteAuthBroker()
    binding = "g" * 48
    first = local.create(
        RemoteCredentials("ROD", "12345678901", "user", PASSWORD, "CWB"),
        session_binding=binding, ttl_seconds=5, per_minute=1, max_pending=2,
    )
    with pytest.raises(RemoteAuthConflict):
        local.create(
            RemoteCredentials("ROD", "12345678901", "user", PASSWORD, "CWB"),
            session_binding=binding, ttl_seconds=5, per_minute=1, max_pending=2,
        )
    with pytest.raises(RemoteAuthUnavailable):
        local.wait_and_consume(first, session_binding=binding, timeout_seconds=0.001)
    with pytest.raises(RemoteAuthRateLimited):
        local.create(
            RemoteCredentials("ROD", "12345678901", "user", PASSWORD, "CWB"),
            session_binding=binding, ttl_seconds=5, per_minute=1, max_pending=2,
        )

    expiring = RemoteAuthBroker()
    expiring.create(
        RemoteCredentials("ROD", "12345678901", "user", PASSWORD, "CWB"),
        session_binding="h" * 48, ttl_seconds=0, per_minute=2, max_pending=2,
    )
    assert expiring.claim(agent_id=AGENT_ID) is None


def test_windows_executor_binds_identity_and_erases_credential_mapping(monkeypatch):
    credentials = {
        "dominio": "ROD", "cpf": "12345678901", "usuario": "user",
        "senha": PASSWORD, "unidade": "CWB",
    }
    fingerprint = identity_fingerprint(
        dominio="ROD", cpf="12345678901", usuario="user", unidade="CWB"
    )
    claimed = {
        "request_id": "r" * 43,
        "challenge": "c" * 43,
        "lease_token": "l" * 43,
        "identity_fingerprint": fingerprint,
        "credentials": credentials,
    }

    class FakeSSW:
        def __init__(self, **values):
            assert values["senha"] == PASSWORD
            self.senha = values["senha"]
            self.session = SimpleNamespace(close=lambda: None)

        def login(self):
            return SimpleNamespace(text="frmlogin menu01")

        def open_menu(self):
            return SimpleNamespace(text='<input name="f2"><input name="f3">')

    monkeypatch.setattr("ssw.client.SSWClient", FakeSSW)

    class FakeClient:
        def __init__(self):
            self.completed = []

        def remote_auth_claim(self):
            return {"request": claimed}

        def remote_auth_complete(self, **values):
            self.completed.append(values)
            return {"accepted": True}

    client = FakeClient()
    assert WindowsRemoteAuthExecutor(client).run_once() is True
    assert client.completed[0]["accepted"] is True
    assert credentials == {}
    assert PASSWORD not in repr(client.completed)


def test_windows_executor_rejects_generic_http_success(monkeypatch):
    credentials = {
        "dominio": "ROD", "cpf": "12345678901", "usuario": "user",
        "senha": PASSWORD, "unidade": "CWB",
    }
    fingerprint = identity_fingerprint(
        dominio="ROD", cpf="12345678901", usuario="user", unidade="CWB"
    )

    class FakeSSW:
        def __init__(self, **_values):
            self.senha = PASSWORD
            self.session = SimpleNamespace(close=lambda: None)

        def login(self):
            return SimpleNamespace(text="página genérica com HTTP 200")

        def open_menu(self):
            return SimpleNamespace(text="página de login")

    monkeypatch.setattr("ssw.client.SSWClient", FakeSSW)

    class FakeClient:
        completed = []

        def remote_auth_claim(self):
            return {"request": {
                "request_id": "r" * 43,
                "challenge": "c" * 43,
                "lease_token": "l" * 43,
                "identity_fingerprint": fingerprint,
                "credentials": credentials,
            }}

        def remote_auth_complete(self, **values):
            self.completed.append(values)
            return {"accepted": True}

    client = FakeClient()
    assert WindowsRemoteAuthExecutor(client).run_once() is True
    assert client.completed[0]["accepted"] is False
    assert credentials == {}


def test_local_login_remains_compatible_when_remote_flag_is_disabled(
    monkeypatch, tmp_path
):
    configure(monkeypatch, tmp_path, enabled="false")
    calls = []

    class FakeSSW:
        def __init__(self, **values):
            self.usuario = values["usuario"]
            calls.append(values)

        def login(self):
            calls.append("login")

        def open_menu(self):
            calls.append("menu")

    monkeypatch.setattr(web_routes, "SSWClient", FakeSSW)
    request = request_with_session()
    response = web_routes.login_run(
        request, dominio="ROD", cpf="12345678901", usuario="local.user",
        senha=PASSWORD, unidade="CWB",
    )
    assert response.status_code == 303
    assert calls[-2:] == ["login", "menu"]

