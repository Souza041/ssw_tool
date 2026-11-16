import threading

from modules.local_agent.executor import (
    HandlerRegistry,
    LocalAgentExecutor,
    register_builtin_handlers,
)


class FakeClient:
    def __init__(self, job=None):
        self.job = job
        self.logs_sent = []
        self.completed = []

    def claim(self, capabilities):
        assert capabilities == ["system.ping"]
        return {"job": self.job, "lease_seconds": 30}

    def heartbeat(self, *args):
        return {"cancellation_requested": False}

    def logs(self, job_id, lease_token, entries):
        self.logs_sent.extend(entries)

    def complete(self, job_id, lease_token, **kwargs):
        self.completed.append(kwargs)
        return {"status": "SUCCEEDED" if kwargs["succeeded"] else "FAILED"}


def registry():
    value = HandlerRegistry()
    register_builtin_handlers(value)
    return value


def test_executor_does_nothing_when_queue_is_empty():
    client = FakeClient()
    assert LocalAgentExecutor(client, registry()).run_once() is False
    assert client.completed == []


def test_executor_runs_only_registered_handler_and_reports_result():
    client = FakeClient({
        "id": "11111111-1111-1111-1111-111111111111",
        "task": "system.ping",
        "payload": {"message": "homologacao"},
        "lease_token": "lease-token-with-at-least-32-characters",
    })
    assert LocalAgentExecutor(client, registry()).run_once() is True
    assert client.completed[0]["succeeded"] is True
    assert client.completed[0]["result"]["message"] == "homologacao"
    assert any("handler permitido" in entry["message"] for entry in client.logs_sent)


def test_unknown_handler_fails_without_shell_fallback():
    client = FakeClient({
        "id": "11111111-1111-1111-1111-111111111111",
        "task": "shell.execute",
        "payload": {"command": "anything"},
        "lease_token": "lease-token-with-at-least-32-characters",
    })
    # O cliente falso simula uma VPS incompatível; não existe fallback de shell.
    assert LocalAgentExecutor(client, registry()).run_once() is True
    assert client.completed[0]["succeeded"] is False
    assert client.completed[0]["result"] == {}
    assert client.completed[0]["error_message"].startswith("LookupError:")


def test_registry_rejects_duplicate_handler():
    value = registry()
    try:
        value.register("system.ping", lambda payload, context: {})
    except ValueError as exc:
        assert "duplicado" in str(exc)
    else:
        raise AssertionError("handler duplicado deveria ser recusado")
