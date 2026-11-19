from types import SimpleNamespace

import pytest
import requests

from modules.local_agent.executor import AgentClient, AgentProtocolError
from modules.local_agent.windows_runtime import configure_runtime_logging
from scripts.nexus_local_agent_windows import run_continuously


TOKEN = "synthetic-agent-token-that-must-never-appear"
SENSITIVE_VALUES = (
    TOKEN,
    "secret-password",
    "session-cookie-value",
    "response-secret-body",
    "nexus.example.test",
    "debug=true",
)


def settings():
    return SimpleNamespace(
        server_url="https://nexus.example.test",
        agent_id="windows-corporativo-01",
        token=TOKEN,
        request_timeout_seconds=5,
        https_proxy=None,
        ca_bundle=None,
    )


class Response:
    def __init__(self, status_code=200, *, json_value=None, json_error=None):
        self.status_code = status_code
        self.url = "https://nexus.example.test/private?debug=true&token=leak"
        self.headers = {
            "Set-Cookie": "session=session-cookie-value",
            "Authorization": f"Bearer {TOKEN}",
        }
        self.text = "response-secret-body password=secret-password"
        self._json_value = {} if json_value is None else json_value
        self._json_error = json_error

    def raise_for_status(self):
        if self.status_code >= 400:
            request = requests.Request(
                "POST",
                self.url,
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={"password": "secret-password"},
            ).prepare()
            raise requests.HTTPError(
                "sensitive upstream error",
                request=request,
                response=self,
            )

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._json_value


class Session:
    def __init__(self, *, response=None, error=None):
        self.response = response
        self.error = error
        self.proxies = {}

    def post(self, *_args, **_kwargs):
        if self.error is not None:
            raise self.error
        return self.response


def assert_safe(message: str):
    for sensitive in SENSITIVE_VALUES:
        assert sensitive not in message
    assert "Authorization" not in message
    assert "Bearer" not in message
    assert "Set-Cookie" not in message
    assert "payload" not in message.casefold()


@pytest.mark.parametrize("status", [401, 403, 404, 422, 429, 500])
def test_http_error_reports_only_method_relative_path_and_status(status):
    client = AgentClient(settings(), Session(response=Response(status)))
    with pytest.raises(AgentProtocolError) as captured:
        client._post(
            "/api/local-agent/v1/remote-auth/claim?debug=true",
            {"password": "secret-password", "token": TOKEN},
        )
    message = str(captured.value)
    assert message == (
        "Erro HTTP na comunicação com a VPS: método=POST "
        f"caminho=/api/local-agent/v1/remote-auth/claim status={status}."
    )
    assert_safe(message)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            requests.ConnectionError(
                "https://nexus.example.test?token=leak password=secret-password"
            ),
            "Falha de conexão com a VPS",
        ),
        (
            requests.Timeout("session-cookie-value"),
            "Timeout na comunicação com a VPS",
        ),
    ],
)
def test_transport_failures_are_classified_without_exception_details(error, expected):
    client = AgentClient(settings(), Session(error=error))
    with pytest.raises(AgentProtocolError) as captured:
        client._post("/api/local-agent/v1/claim", {"token": TOKEN})
    message = str(captured.value)
    assert expected in message
    assert "método=POST" in message
    assert "caminho=/api/local-agent/v1/claim" in message
    assert_safe(message)


def test_invalid_json_is_distinct_and_does_not_include_body():
    response = Response(
        200,
        json_error=requests.exceptions.JSONDecodeError(
            "response-secret-body", "response-secret-body", 0
        ),
    )
    client = AgentClient(settings(), Session(response=response))
    with pytest.raises(AgentProtocolError) as captured:
        client._post("/api/local-agent/v1/claim", {"password": "secret-password"})
    message = str(captured.value)
    assert message == (
        "Resposta JSON inválida da VPS: método=POST "
        "caminho=/api/local-agent/v1/claim."
    )
    assert_safe(message)


def test_valid_non_object_json_has_separate_contract_error():
    client = AgentClient(settings(), Session(response=Response(200, json_value=[])))
    with pytest.raises(AgentProtocolError) as captured:
        client._post("/api/local-agent/v1/heartbeat", {"cookie": "session-cookie-value"})
    message = str(captured.value)
    assert "Contrato JSON inválido" in message
    assert "caminho=/api/local-agent/v1/heartbeat" in message
    assert_safe(message)


def test_http_diagnostic_reaches_persistent_log_without_sensitive_data(tmp_path):
    class StopAfterFirstBackoff:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, _seconds):
            self.stopped = True
            return True

    client = AgentClient(settings(), Session(response=Response(403)))
    log_file = tmp_path / "local-agent.log"
    logger = configure_runtime_logging(log_file)
    run_continuously(
        lambda: client._post(
            "/api/local-agent/v1/remote-auth/claim?debug=true",
            {"password": "secret-password", "token": TOKEN},
        ),
        stop_event=StopAfterFirstBackoff(),
        poll_seconds=1,
        logger=logger,
    )
    for handler in logger.handlers:
        handler.flush()
    content = log_file.read_text(encoding="utf-8")
    assert "método=POST" in content
    assert "caminho=/api/local-agent/v1/remote-auth/claim" in content
    assert "status=403" in content
    assert_safe(content)

