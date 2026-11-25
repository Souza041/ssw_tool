"""Compatibilidade offline do SSWClient com o transporte Chromium."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from operations.op001.coleta import OP001Coleta
from modules.documentos.config import DocumentosSettings
from ssw.browser_transport import (
    BrowserIdentity,
    BrowserSessionTransport,
    SSWBrowserAuthenticationError,
    SSWBrowserTransportError,
    SSWSessionBusyError,
    _BrowserWorker,
    _OperationCoordinator,
    _browser_launch_options,
    _checked_requested_unit,
    _load_playwright_proxy,
)
from ssw.client import SSWClient


class RecordingWorker:
    def __init__(self):
        self.base_url = "https://tms.ssw.inf.br"
        self.identity = BrowserIdentity.checked("ROD", "employee.one")
        self.observed_unit = "CWB"
        self.valid = True
        self.operations = _OperationCoordinator()
        self.calls = []
        self.closed = False

    def call(self, command, value=None, timeout=None):
        self.calls.append((command, value, timeout))
        if command == "probe":
            return True
        return (
            200,
            value["url"],
            {"Content-Type": "application/octet-stream"},
            b"\x00payload",
        )

    def close(self):
        self.closed = True
        self.valid = False


def test_requests_compatible_response_preserves_method_params_payload_and_binary():
    worker = RecordingWorker()
    transport = BrowserSessionTransport(worker)

    response = transport.post(
        "https://tms.ssw.inf.br/bin/ssw0017",
        data={"act": "PES", "f2": ""},
        timeout=(10, 45),
        allow_redirects=False,
    )

    assert response.status_code == 200
    assert response.content == b"\x00payload"
    assert response.headers["Content-Type"] == "application/octet-stream"
    sent = worker.calls[0][1]
    assert sent["method"] == "POST"
    assert sent["data"] == {"act": "PES", "f2": ""}
    assert sent["params"] is None
    assert sent["timeout"] == (10, 45)
    assert sent["allow_redirects"] is False


def test_context_request_keeps_payload_and_never_forwards_cookie_header():
    raw = SimpleNamespace(
        status=200,
        url="https://tms.ssw.inf.br/bin/ssw0230",
        headers={"content-type": "text/html; charset=latin1"},
        body=Mock(return_value=b"ok"),
        dispose=Mock(),
    )
    request = SimpleNamespace(fetch=Mock(return_value=raw))
    worker = object.__new__(_BrowserWorker)
    worker.base_url = "https://tms.ssw.inf.br"
    worker._authenticated = True

    result = worker._request(SimpleNamespace(request=request), {
        "method": "POST",
        "url": "https://tms.ssw.inf.br/bin/ssw0230",
        "data": {"act": "E1", "f2": ""},
        "params": None,
        "headers": {"Cookie": "must-not-leave-context", "Referer": "safe"},
        "timeout": 60,
        "allow_redirects": True,
    })

    assert result[3] == b"ok"
    kwargs = request.fetch.call_args.kwargs
    assert kwargs["form"] == {"act": "E1", "f2": ""}
    assert kwargs["headers"] == {"Referer": "safe"}
    assert "cookie" not in repr(kwargs).casefold()
    raw.dispose.assert_called_once_with()


def test_expired_session_or_captcha_fails_closed():
    raw = SimpleNamespace(
        status=200,
        url="https://tms.ssw.inf.br/bin/ssw0422",
        headers={},
        body=Mock(return_value=b'<div class="g-recaptcha"></div>'),
        dispose=Mock(),
    )
    worker = object.__new__(_BrowserWorker)
    worker.base_url = "https://tms.ssw.inf.br"
    worker._authenticated = True
    context = SimpleNamespace(request=SimpleNamespace(fetch=Mock(return_value=raw)))

    with pytest.raises(SSWBrowserAuthenticationError, match="nova autenticacao"):
        worker._request(context, {
            "method": "GET",
            "url": "https://tms.ssw.inf.br/bin/menu01",
            "params": {},
            "data": None,
            "headers": {},
            "timeout": 60,
            "allow_redirects": True,
        })
    assert worker._authenticated is False


def test_legitimate_unit_switch_payload_does_not_invalidate_session():
    raw = SimpleNamespace(
        status=200,
        url="https://tms.ssw.inf.br/bin/menu01",
        headers={"content-type": "text/html"},
        body=Mock(return_value=b"menu updated"),
        dispose=Mock(),
    )
    worker = object.__new__(_BrowserWorker)
    worker.base_url = "https://tms.ssw.inf.br"
    worker._authenticated = True
    context = SimpleNamespace(request=SimpleNamespace(fetch=Mock(return_value=raw)))

    worker._request(context, {
        "method": "POST",
        "url": "https://tms.ssw.inf.br/bin/menu01",
        "data": {"act": "TRO", "f2": "MTZ", "f3": "455"},
        "params": None,
        "headers": {},
        "timeout": 60,
        "allow_redirects": True,
    })

    assert worker._authenticated is True
    assert context.request.fetch.call_args.kwargs["form"] == {
        "act": "TRO", "f2": "MTZ", "f3": "455",
    }


def test_identity_is_domain_and_user_while_operational_unit_is_independent():
    manual = BrowserIdentity.checked("ROD", "real.user")
    scheduled = BrowserIdentity.checked("ROD", "robot.user")
    assert manual == BrowserIdentity.checked("ROD", "real.user")
    assert manual != scheduled
    assert _checked_requested_unit("MTZ") == "MTZ"
    assert _checked_requested_unit("CWB") == "CWB"
    with pytest.raises(SSWBrowserAuthenticationError):
        _checked_requested_unit("")


def test_initial_unit_is_recorded_but_not_compared_with_requested_unit():
    worker = object.__new__(_BrowserWorker)
    worker.identity = BrowserIdentity.checked("ROD", "real.user")
    worker.observed_unit = ""

    worker._confirm_authenticated_identity("ROD", "REAL.USER", "CWB")

    assert worker.observed_unit == "CWB"
    assert _checked_requested_unit("MTZ") == "MTZ"


@pytest.mark.parametrize(
    ("domain", "user", "message"),
    [
        ("OTHER", "REAL.USER", "Dominio autenticado divergente"),
        ("ROD", "OTHER.USER", "Usuario autenticado divergente"),
    ],
)
def test_identity_diagnostics_distinguish_domain_and_user_without_values(
    domain, user, message,
):
    worker = object.__new__(_BrowserWorker)
    worker.identity = BrowserIdentity.checked("ROD", "real.user")
    worker.observed_unit = ""

    with pytest.raises(SSWBrowserAuthenticationError, match=message) as captured:
        worker._confirm_authenticated_identity(domain, user, "CWB")

    assert "OTHER" not in str(captured.value)
    assert "REAL.USER" not in str(captured.value)


def test_proxy_from_existing_env_is_applied_to_chromium_without_logging(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PROXY_SERVER=http://proxy.corp:3128\n"
        "PROXY_USER=corporate-user\n"
        "PROXY_PASS=proxy-secret\n",
        encoding="utf-8",
    )

    proxy = _load_playwright_proxy(env_file)

    assert proxy == {
        "server": "http://proxy.corp:3128",
        "username": "corporate-user",
        "password": "proxy-secret",
    }
    assert _browser_launch_options(proxy) == {"headless": False, "proxy": proxy}


def test_proxy_errors_are_sanitized_and_incomplete_credentials_fail_closed(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PROXY_SERVER=http://proxy.corp:3128\nPROXY_USER=user\nPROXY_PASS=\n",
        encoding="utf-8",
    )

    with pytest.raises(SSWBrowserAuthenticationError, match="incompleta") as captured:
        _load_playwright_proxy(env_file)

    diagnostic = str(captured.value)
    assert "proxy.corp" not in diagnostic
    assert "user" not in diagnostic


def test_same_session_has_atomic_operation_lock_and_friendly_diagnostic():
    transport = BrowserSessionTransport(RecordingWorker())
    first = transport.acquire_operation("OP001 · Coletas")

    with pytest.raises(SSWSessionBusyError, match="OP001 · Coletas"):
        transport.acquire_operation("OP455 · CTRCs")

    first.release()
    second = transport.acquire_operation("OP455 · CTRCs")
    second.release()


def test_independent_sessions_can_run_in_parallel():
    first_worker = RecordingWorker()
    second_worker = RecordingWorker()
    second_worker.identity = BrowserIdentity.checked("ROD", "other.user")
    first = BrowserSessionTransport(first_worker).acquire_operation("OP001")
    second = BrowserSessionTransport(second_worker).acquire_operation("OP455")
    first.release()
    second.release()


def test_closed_browser_requires_new_human_authentication():
    worker = RecordingWorker()
    transport = BrowserSessionTransport(worker)
    worker.valid = False

    assert transport.session_status()["state"] == "reauthentication_required"
    with pytest.raises(SSWBrowserAuthenticationError, match="nova autenticacao"):
        transport.acquire_operation("OP001")


def test_browser_disconnect_and_closed_context_invalidate_transport():
    worker = object.__new__(_BrowserWorker)
    worker.base_url = "https://tms.ssw.inf.br"
    worker._authenticated = True
    worker._browser_disconnected()
    assert worker._authenticated is False

    worker._authenticated = True
    request = Mock()
    request.fetch.side_effect = RuntimeError(
        "Target page, context or browser has been closed secret-value"
    )
    with pytest.raises(SSWBrowserAuthenticationError, match="nova autenticacao") as error:
        worker._request(SimpleNamespace(request=request), {
            "method": "GET", "url": "https://tms.ssw.inf.br/bin/menu01",
            "params": {}, "data": None, "headers": {}, "timeout": 60,
            "allow_redirects": True,
        })
    assert "secret-value" not in str(error.value)
    assert worker._authenticated is False


def test_browser_post_is_not_retried_when_result_is_uncertain():
    worker = RecordingWorker()
    worker.call = Mock(side_effect=SSWBrowserTransportError("resultado incerto"))
    client = SSWClient(
        dominio="ROD", usuario="employee.one", unidade="CWB", auth_mode="http",
    )
    client.session = BrowserSessionTransport(worker)

    with pytest.raises(SSWBrowserTransportError, match="incerto"):
        client.post("/bin/ssw0017", {"act": "SAVECOL"}, retries=3)

    assert worker.call.call_count == 1


def test_chromium_window_is_minimized_best_effort_after_authentication():
    cdp = Mock()
    cdp.send.side_effect = [{"windowId": 42}, {}]
    context = SimpleNamespace(new_cdp_session=Mock(return_value=cdp))

    assert _BrowserWorker._minimize_window(context, object()) is True
    assert cdp.send.call_args_list[1].args == (
        "Browser.setWindowBounds",
        {"windowId": 42, "bounds": {"windowState": "minimized"}},
    )
    cdp.detach.assert_called_once_with()


def test_minimize_failure_preserves_authenticated_flow():
    context = SimpleNamespace(
        new_cdp_session=Mock(side_effect=RuntimeError("unsupported")),
    )
    assert _BrowserWorker._minimize_window(context, object()) is False


def test_ssw_client_switches_only_transport_during_browser_login(monkeypatch):
    transport = SimpleNamespace(headers={}, close=Mock(), observed_unit="CWB")
    factory = Mock(return_value=transport)
    monkeypatch.setattr(
        "ssw.browser_transport.authenticated_browser_transport", factory,
    )
    client = SSWClient(
        dominio="ROD", cpf="", usuario="real.user", senha="",
        unidade="CWB", auth_mode="browser",
    )

    assert client.login() is None
    assert client.session is transport
    assert client.authenticated_unit == "CWB"
    assert client.cpf == ""
    assert client.senha == ""
    factory.assert_called_once_with(
        domain="ROD",
        user="real.user",
        unit="CWB",
        base_url="https://tms.ssw.inf.br",
        timeout_seconds=900,
    )


def test_scheduled_identity_does_not_require_or_fallback_to_http_secrets(monkeypatch):
    monkeypatch.setenv("SSW_AUTH_MODE", "browser")
    config = DocumentosSettings(
        ssw_dominio="ROD", ssw_usuario="robot.dedicated", ssw_unidade="MTZ",
        ssw_cpf="", ssw_senha="",
    )
    config.validate_ssw()

    client = SSWClient(
        dominio=config.ssw_dominio,
        cpf="master-must-not-be-used",
        usuario=config.ssw_usuario,
        senha="master-must-not-be-used",
        unidade=config.ssw_unidade,
        auth_mode="browser",
    )
    assert (client.usuario, client.unidade) == ("robot.dedicated", "MTZ")
    assert (client.cpf, client.senha) == ("", "")


def test_source_never_exports_browser_authentication_state():
    from pathlib import Path

    source = Path("ssw/browser_transport.py").read_text(encoding="utf-8").casefold()
    for forbidden in (
        "storage_state", "add_cookies", "document.cookie", "requests.session",
        "navigator.webdriver",
    ):
        assert forbidden not in source


def test_op001_original_open_payload_is_unchanged_on_browser_transport():
    worker = RecordingWorker()
    client = SSWClient(
        dominio="ROD", usuario="employee.one", unidade="CWB", auth_mode="http",
    )
    client.session = BrowserSessionTransport(worker)

    OP001Coleta(client).open(unidade="CWB")

    sent = [call[1] for call in worker.calls]
    assert sent[0]["url"].endswith("/bin/menu01")
    assert sent[0]["data"]["act"] == "TRO"
    assert sent[0]["data"]["f2"] == "CWB"
    assert sent[0]["data"]["f3"] == "001"
    assert sent[1]["url"].endswith("/bin/ssw0017")
    assert sent[1]["data"]["sequencia"] == "1"
    assert set(sent[1]["data"]) == {"sequencia", "dummy"}

