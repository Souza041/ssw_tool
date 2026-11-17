import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from modules.local_agent import routes
from modules.local_agent.bootstrap import canonical_ping_body, sign_request
from modules.local_agent.config import load_server_settings
from modules.local_agent.config import LocalAgentConfigurationError
from modules.local_agent.repository import BootstrapReplayError


AGENT_TOKEN = "agent-token-security-with-more-than-32-characters"
BOOTSTRAP_SECRET = "bootstrap-secret-security-more-than-32-characters"
KEY_ID = "bootstrap-admin"


class RequestStub:
    session = {}
    client = type("Client", (), {"host": "10.0.0.10"})()


def configure(monkeypatch, tmp_path, *, bootstrap="true", per_minute="5"):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ENABLED", "true")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_DB", str(tmp_path / "security.sqlite3"))
    monkeypatch.setenv(
        "NEXUS_LOCAL_AGENT_TOKENS_JSON",
        json.dumps({"windows-corporativo-01": AGENT_TOKEN}),
    )
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ALLOWED_TASKS", "system.ping")
    monkeypatch.setenv(
        "NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON",
        json.dumps({"windows-corporativo-01": ["system.ping"]}),
    )
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_BOOTSTRAP_ENABLED", bootstrap)
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_BOOTSTRAP_KEY_ID", KEY_ID)
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_BOOTSTRAP_SECRET", BOOTSTRAP_SECRET)
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_BOOTSTRAP_PER_MINUTE", per_minute)
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_BOOTSTRAP_PER_HOUR", "20")


def headers(path, body, *, nonce=None, secret=BOOTSTRAP_SECRET, timestamp=None):
    nonce = nonce or secrets.token_urlsafe(32)
    timestamp = timestamp or str(int(time.time()))
    return {
        "x_nexus_bootstrap_key_id": KEY_ID,
        "x_nexus_bootstrap_timestamp": timestamp,
        "x_nexus_bootstrap_nonce": nonce,
        "x_nexus_bootstrap_signature": sign_request(
            secret, method="POST", path=path, timestamp=timestamp, nonce=nonce, body=body
        ),
    }


def test_bootstrap_disabled_by_default_even_when_agent_enabled(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, bootstrap="false")
    with pytest.raises(HTTPException) as error:
        routes.bootstrap_system_ping(
            RequestStub(), routes.BootstrapPingRequest(message="teste")
        )
    assert error.value.status_code == 503


def test_bootstrap_rejects_bad_signature_and_audits_without_secret(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    path = "/api/local-agent/v1/bootstrap/system-ping"
    body = canonical_ping_body("teste")
    with pytest.raises(HTTPException) as error:
        routes.bootstrap_system_ping(
            RequestStub(),
            routes.BootstrapPingRequest(message="teste"),
            **headers(path, body, secret="wrong-secret-with-more-than-32-characters"),
        )
    assert error.value.status_code == 401
    audit = routes._repository(load_server_settings()).list_bootstrap_audit()
    assert audit[0]["outcome"] == "INVALID_SIGNATURE"
    assert BOOTSTRAP_SECRET not in repr(audit)


def test_bootstrap_rejects_replay_persistently(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    path = "/api/local-agent/v1/bootstrap/system-ping"
    body = canonical_ping_body("teste")
    nonce = secrets.token_urlsafe(32)
    signed = headers(path, body, nonce=nonce)
    first = routes.bootstrap_system_ping(
        RequestStub(), routes.BootstrapPingRequest(message="teste"), **signed
    )
    with pytest.raises(HTTPException) as error:
        routes.bootstrap_system_ping(
            RequestStub(), routes.BootstrapPingRequest(message="teste"), **signed
        )
    assert first["accepted"] is True
    assert error.value.status_code == 409
    assert len(routes._repository(load_server_settings()).list_jobs()) == 1


def test_bootstrap_rate_limit_is_persistent_and_audited(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, per_minute="1")
    path = "/api/local-agent/v1/bootstrap/system-ping"
    body = canonical_ping_body("teste")
    routes.bootstrap_system_ping(
        RequestStub(), routes.BootstrapPingRequest(message="teste"),
        **headers(path, body),
    )
    with pytest.raises(HTTPException) as error:
        routes.bootstrap_system_ping(
            RequestStub(), routes.BootstrapPingRequest(message="teste-2"),
            **headers(path, canonical_ping_body("teste-2")),
        )
    assert error.value.status_code == 429
    outcomes = {
        item["outcome"]
        for item in routes._repository(load_server_settings()).list_bootstrap_audit()
    }
    assert {"ENQUEUED", "RATE_LIMITED"} <= outcomes


def test_bootstrap_rejects_stale_timestamp_and_arbitrary_fields(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    path = "/api/local-agent/v1/bootstrap/system-ping"
    body = canonical_ping_body("teste")
    with pytest.raises(HTTPException) as error:
        routes.bootstrap_system_ping(
            RequestStub(), routes.BootstrapPingRequest(message="teste"),
            **headers(path, body, timestamp=str(int(time.time()) - 3600)),
        )
    assert error.value.status_code == 401
    with pytest.raises(ValidationError):
        routes.BootstrapPingRequest(message="teste", task="shell.execute")


def test_secrets_are_redacted_from_configuration_repr(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    representation = repr(load_server_settings())
    assert BOOTSTRAP_SECRET not in representation
    assert AGENT_TOKEN not in representation


def test_bootstrap_requires_agent_gate_and_independent_secret(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ENABLED", "false")
    with pytest.raises(LocalAgentConfigurationError, match="Local Agent desativado"):
        load_server_settings()

    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ENABLED", "true")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_BOOTSTRAP_SECRET", AGENT_TOKEN)
    with pytest.raises(LocalAgentConfigurationError, match="independente"):
        load_server_settings()


def test_bootstrap_same_nonce_is_atomic_across_concurrent_workers(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    repository = routes._repository(load_server_settings())
    nonce = secrets.token_urlsafe(32)

    def enqueue():
        try:
            return repository.bootstrap_enqueue_ping(
                key_id=KEY_ID,
                nonce=nonce,
                remote_address="10.0.0.10",
                message="concorrencia",
                per_minute=5,
                per_hour=20,
            )
        except BootstrapReplayError:
            return "REPLAY"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: enqueue(), range(2)))
    assert results.count("REPLAY") == 1
    assert len(repository.list_jobs()) == 1
