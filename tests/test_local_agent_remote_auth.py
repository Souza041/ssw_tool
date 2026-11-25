from __future__ import annotations

from types import SimpleNamespace

import pytest

from modules.local_agent.remote_auth import (
    RemoteAuthBroker,
    RemoteAuthConflict,
    RemoteIdentity,
    WindowsRemoteAuthExecutor,
    identity_fingerprint,
    response_proof,
)


TOKEN = "t" * 48
BINDING = "b" * 48


def identity(binding: str = BINDING) -> RemoteIdentity:
    return RemoteIdentity("ROD", "employee.one", "CWB", binding)


def test_claim_exports_only_expected_identity_and_never_credentials():
    broker = RemoteAuthBroker()
    request_id = broker.create(
        identity(), session_binding=BINDING, ttl_seconds=60,
        per_minute=5, max_pending=5,
    )
    claimed = broker.claim(agent_id="windows-one")
    assert claimed["request_id"] == request_id
    assert claimed["identity"] == {
        "dominio": "ROD",
        "usuario": "employee.one",
        "unidade": "CWB",
        "session_binding": BINDING,
    }
    serialized = repr(claimed).lower()
    assert "senha" not in serialized
    assert "password" not in serialized
    assert "cookie" not in serialized
    assert "cpf" not in serialized


def test_completion_is_single_use_bound_to_agent_and_session():
    broker = RemoteAuthBroker()
    request_id = broker.create(
        identity(), session_binding=BINDING, ttl_seconds=60,
        per_minute=5, max_pending=5,
    )
    claimed = broker.claim(agent_id="windows-one")
    fingerprint = claimed["identity_fingerprint"]
    proof = response_proof(
        TOKEN,
        request_id=request_id,
        challenge=claimed["challenge"],
        identity=fingerprint,
        agent_id="windows-one",
        accepted=True,
    )
    broker.complete(
        request_id=request_id,
        lease_token=claimed["lease_token"],
        agent_id="windows-one",
        accepted=True,
        identity=fingerprint,
        proof=proof,
        secret=TOKEN,
    )
    result = broker.wait_and_consume(
        request_id, session_binding=BINDING, timeout_seconds=0.1,
    )
    assert result.accepted is True
    assert result.session_binding == BINDING
    with pytest.raises(RemoteAuthConflict):
        broker.wait_and_consume(
            request_id, session_binding=BINDING, timeout_seconds=0.1,
        )


def test_fingerprint_isolates_equal_users_in_different_nexus_sessions():
    first = identity_fingerprint(
        dominio="ROD", usuario="employee.one", unidade="CWB",
        session_binding="a" * 48,
    )
    second = identity_fingerprint(
        dominio="ROD", usuario="employee.one", unidade="CWB",
        session_binding="b" * 48,
    )
    assert first != second


def test_windows_executor_uses_browser_transport_without_receiving_secrets(monkeypatch):
    request = {
        "request_id": "r" * 48,
        "challenge": "c" * 48,
        "lease_token": "l" * 48,
        "identity_fingerprint": identity_fingerprint(
            dominio="ROD", usuario="employee.one", unidade="CWB",
            session_binding=BINDING,
        ),
        "identity": identity().as_payload(),
    }
    completed = []
    constructed = []

    class FakeAgentClient:
        def remote_auth_claim(self):
            return {"request": request}

        def remote_auth_complete(self, **kwargs):
            completed.append(kwargs)
            return {"accepted": True}

    class FakeSSWClient:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

        def login(self):
            return None

        def open_menu(self):
            return None

    monkeypatch.setattr("ssw.client.SSWClient", FakeSSWClient)
    assert WindowsRemoteAuthExecutor(FakeAgentClient()).run_once() is True
    assert constructed == [{
        "dominio": "ROD",
        "usuario": "employee.one",
        "unidade": "CWB",
        "auth_mode": "browser",
        "session_binding": BINDING,
    }]
    assert completed[0]["accepted"] is True


def test_windows_executor_rejects_any_credential_field():
    payload = identity().as_payload()
    payload["senha"] = "must-not-cross"
    client = SimpleNamespace(remote_auth_claim=lambda: {
        "request": {
            "request_id": "r" * 48,
            "challenge": "c" * 48,
            "lease_token": "l" * 48,
            "identity_fingerprint": "f" * 64,
            "identity": payload,
        }
    })
    with pytest.raises(Exception, match="identidade"):
        WindowsRemoteAuthExecutor(client).run_once()
