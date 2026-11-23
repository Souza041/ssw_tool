from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.local_agent import companion_runtime as runtime
from modules.local_agent import ssw_companion
from modules.local_agent.ssw_companion import CompanionContract, CompanionError
from modules.local_agent.ssw_companion_config import automatic_contract_data


class RuntimePage:
    def __init__(self):
        self.url = "about:blank"
        self.main_frame = SimpleNamespace(url=self.url)
        self.events = {}
        self.goto = Mock(side_effect=self._goto)
        self.wait_for_load_state = Mock()
        self.wait_for_timeout = Mock()

    def on(self, event, callback):
        self.events.setdefault(event, []).append(callback)

    def _goto(self, url, **_kwargs):
        self.navigate(url)

    def navigate(self, url):
        self.url = self.main_frame.url = url
        for callback in self.events.get("framenavigated", []):
            callback(self.main_frame)


class FakeBrowser:
    def __init__(self):
        self.pages = []
        self.contexts = []
        self.close = Mock()

    def new_context(self, *, accept_downloads):
        page = RuntimePage()
        context = SimpleNamespace(new_page=lambda: page, close=Mock())
        self.pages.append(page)
        self.contexts.append(context)
        return context


def build(monkeypatch, validator):
    browser = FakeBrowser()
    manager = SimpleNamespace(
        chromium=SimpleNamespace(launch=Mock(return_value=browser)), stop=Mock(),
    )
    monkeypatch.setattr(runtime, "validate_authenticated_context", validator)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    session = runtime.InteractiveSSWSession(
        contract=object(), base_url="https://tms.ssw.inf.br", proxy=None,
        playwright_factory=lambda: SimpleNamespace(start=lambda: manager),
        logger=Mock(),
    )
    return session, browser, manager


def test_human_authentication_keeps_context_and_reuses_matching_identity(monkeypatch):
    validator = Mock(side_effect=[CompanionError("pending"), None, None])
    session, browser, _ = build(monkeypatch, validator)
    identity = session.authenticate(
        usuario="employee.one", unidade="CWB", timeout_seconds=60,
        cancel=lambda: None,
    )
    assert (identity.usuario, identity.unidade) == ("employee.one", "CWB")
    assert len(browser.contexts) == 1
    again = session.authenticate(
        usuario="employee.one", unidade="CWB", timeout_seconds=60,
        cancel=lambda: None,
    )
    assert again == identity
    assert len(browser.contexts) == 1


def test_expired_session_is_rejected_before_operation(monkeypatch):
    session, _, _ = build(monkeypatch, Mock(side_effect=CompanionError("expired")))
    with pytest.raises(CompanionError, match="expirada"):
        session.client(usuario="employee.one", unidade="CWB")


def test_runtime_captures_only_domain_and_identity_from_known_login(monkeypatch):
    session, _, _ = build(monkeypatch, Mock(return_value=None))
    session.contract = CompanionContract.from_mapping(automatic_contract_data())
    page = RuntimePage()

    def field(value="", *, visible=True):
        return SimpleNamespace(
            count=lambda: 1,
            is_visible=lambda: visible,
            input_value=lambda **_kwargs: value,
        )

    allowed = {
        session.contract.login_form_selector: field(visible=True),
        session.contract.login_domain_selector: field("ROD"),
        session.contract.login_identity_selector: field("employee.one"),
    }

    def locator(selector):
        if selector not in allowed:
            raise AssertionError("CPF e senha não podem ser consultados")
        return allowed[selector]

    page.locator = locator
    session.page = page

    session._capture_known_login_identity()

    assert session.observed_domain == "ROD"
    assert session.observed_identity == "employee.one"


def test_v2_runtime_detects_menu_without_manual_confirmation(monkeypatch):
    session, browser, _ = build(monkeypatch, Mock())
    session.contract = CompanionContract.from_mapping(automatic_contract_data())
    monkeypatch.setattr(
        runtime, "validate_authenticated_context",
        ssw_companion.validate_authenticated_context,
    )
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        page = browser.pages[0]

        def field(value="", *, present=True):
            return SimpleNamespace(
                count=lambda: 1 if present else 0,
                is_visible=lambda: present,
                input_value=lambda **_kwargs: value,
            )

        is_menu = page.url.endswith("/bin/menu01")
        mapping = {
            session.contract.login_form_selector: field(present=not is_menu),
            session.contract.login_domain_selector: field("ROD", present=not is_menu),
            session.contract.login_identity_selector: field(
                "employee.one", present=not is_menu,
            ),
            session.contract.authenticated_marker_selector: field(present=is_menu),
            session.contract.menu_unit_selector: field("CWB", present=True),
        }
        page.locator = lambda selector: mapping.get(selector, field(present=False))
        if calls == 2:
            page.navigate("https://tms.ssw.inf.br/bin/menu01")

    identity = session.authenticate(
        usuario="employee.one", unidade="CWB", dominio="ROD",
        timeout_seconds=60, cancel=cancel,
    )

    assert identity.usuario == "employee.one"
    assert identity.unidade == "CWB"
    assert session.observed_domain == "ROD"
    assert session.observed_identity == "employee.one"
    assert browser.pages[0].goto.call_count == 1
    assert len(browser.contexts) == 1


def test_v2_runtime_rejects_divergent_identity_as_soon_as_menu_appears(monkeypatch):
    session, browser, _ = build(monkeypatch, Mock())
    session.contract = CompanionContract.from_mapping(automatic_contract_data())
    monkeypatch.setattr(
        runtime, "validate_authenticated_context",
        ssw_companion.validate_authenticated_context,
    )

    def cancel():
        page = browser.pages[0]

        def field(value="", *, present=True):
            return SimpleNamespace(
                count=lambda: 1 if present else 0,
                is_visible=lambda: present,
                input_value=lambda **_kwargs: value,
            )

        is_menu = page.url.endswith("/bin/menu01")
        mapping = {
            session.contract.login_form_selector: field(present=not is_menu),
            session.contract.login_domain_selector: field("ROD", present=not is_menu),
            session.contract.login_identity_selector: field(
                "another.employee", present=not is_menu,
            ),
            session.contract.authenticated_marker_selector: field(present=is_menu),
            session.contract.menu_unit_selector: field("CWB", present=True),
        }
        page.locator = lambda selector: mapping.get(selector, field(present=False))
        if not is_menu:
            session._capture_known_login_identity()
            page.navigate("https://tms.ssw.inf.br/bin/menu01")

    with pytest.raises(CompanionError, match="identidade exibida diverge"):
        session.authenticate(
            usuario="employee.one", unidade="CWB", dominio="ROD",
            timeout_seconds=60, cancel=cancel,
        )
    assert browser.contexts[0].close.called


def test_external_main_frame_redirect_discards_runtime_session(monkeypatch):
    session, browser, _ = build(monkeypatch, Mock(return_value=None))
    session._new_login_context()
    context = browser.contexts[0]

    browser.pages[0].navigate("https://external.example/redirect")

    with pytest.raises(CompanionError, match="expirada"):
        session.client(usuario="employee.one", unidade="CWB")
    context.close.assert_called_once_with()
    assert session.context is None
    assert session.page is None
    assert session.identity is None


def test_external_redirect_during_initial_goto_reports_policy_and_discards(monkeypatch):
    session, browser, _ = build(monkeypatch, Mock(return_value=None))
    original_new_context = browser.new_context

    def redirecting_context(*, accept_downloads):
        context = original_new_context(accept_downloads=accept_downloads)
        page = context.new_page()
        page.goto.side_effect = lambda *_args, **_kwargs: page.navigate(
            "https://external.example/redirect"
        )
        return context

    browser.new_context = redirecting_context
    with pytest.raises(CompanionError, match="saiu da origem oficial"):
        session._new_login_context()

    browser.contexts[0].close.assert_called_once_with()
    assert session.context is None


def test_close_discards_context_browser_and_runtime(monkeypatch):
    session, browser, manager = build(monkeypatch, Mock(return_value=None))
    session.authenticate(
        usuario="employee.one", unidade="CWB", timeout_seconds=60,
        cancel=lambda: None,
    )
    context = browser.contexts[0]
    session.close()
    context.close.assert_called_once_with()
    browser.close.assert_called_once_with()
    manager.stop.assert_called_once_with()


def test_cancellation_interrupts_human_wait_and_discards_context_immediately(monkeypatch):
    session, browser, _ = build(monkeypatch, Mock(side_effect=CompanionError("pending")))

    def cancel():
        raise RuntimeError("Execução cancelada pelo servidor.")

    with pytest.raises(RuntimeError, match="cancelada"):
        session.authenticate(
            usuario="employee.one", unidade="CWB", timeout_seconds=60,
            cancel=cancel,
        )
    assert len(browser.contexts) == 1
    browser.contexts[0].close.assert_called_once_with()
    assert session.context is None


def test_navigation_failure_discards_partial_context(monkeypatch):
    session, browser, _ = build(monkeypatch, Mock(return_value=None))
    original_new_context = browser.new_context

    def failing_context(*, accept_downloads):
        context = original_new_context(accept_downloads=accept_downloads)
        context.new_page().goto.side_effect = RuntimeError("network secret=hidden")
        return context

    browser.new_context = failing_context
    with pytest.raises(CompanionError, match="Falha técnica"):
        session.authenticate(
            usuario="employee.one", unidade="CWB", timeout_seconds=60,
            cancel=lambda: None,
        )
    browser.contexts[0].close.assert_called_once_with()
    assert session.context is None


def test_installer_registers_interactive_at_logon_without_starting_task():
    from pathlib import Path
    source = Path("deploy/local_agent/install_windows_companion.ps1").read_text(
        encoding="utf-8"
    )
    assert "-AtLogOn" in source
    assert "-LogonType Interactive" in source
    assert "-MultipleInstances IgnoreNew" in source
    assert "Start-ScheduledTask" not in source
    assert "LOCAL SERVICE" not in source
    assert "NEXUS_LOCAL_AGENT_SERVER_URL" in source
    assert "NEXUS_LOCAL_AGENT_TOKEN" in source
    assert "NEXUS_LOCAL_AGENT_ALLOW_INSECURE_HTTP" in source
    assert "NEXUS_SSW_REMOTE_AUTH_ENABLED" in source

