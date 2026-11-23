import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.local_agent.ssw_companion import (
    CompanionContract,
    CompanionError,
    EvidenceRule,
    KNOWN_LOGIN_CPF_SELECTOR,
    KNOWN_LOGIN_DOMAIN_SELECTOR,
    KNOWN_LOGIN_FORM_SELECTOR,
    KNOWN_LOGIN_IDENTITY_SELECTOR,
    KNOWN_LOGIN_PASSWORD_SELECTOR,
    KNOWN_MENU_MARKER_SELECTOR,
    KNOWN_MENU_UNIT_SELECTOR,
    ReadonlyProbeRule,
    attach_official_navigation_monitor,
    official_login_url,
    run_interactive_homologation,
    validate_authenticated_context,
)
from modules.local_agent.ssw_companion_proxy import CompanionProxy


LOGIN_URL = "https://tms.ssw.inf.br/bin/ssw0422"


class FakeLocator:
    def __init__(self, text="", *, visible=True, count=1, value=None):
        self.text = text
        self.visible = visible
        self.number = count
        self.value = text if value is None else value

    def count(self):
        return self.number

    def is_visible(self):
        return self.visible

    def inner_text(self, *, timeout):
        assert timeout == 5_000
        return self.text

    def input_value(self, *, timeout):
        assert timeout == 5_000
        return self.value


class FakePage:
    def __init__(self, locators=None, *, url=LOGIN_URL):
        self.url = url
        self.locators = locators or {}
        self.main_frame = SimpleNamespace(url=url)
        self.events = {}
        self.goto_calls = []
        self.wait_calls = []
        self.timeout_calls = []

    def locator(self, selector):
        return self.locators.get(selector, FakeLocator(count=0))

    def on(self, event, callback):
        self.events.setdefault(event, []).append(callback)

    def navigate(self, url, *, frame=None):
        target = frame or self.main_frame
        target.url = url
        if target is self.main_frame:
            self.url = url
        for callback in self.events.get("framenavigated", []):
            callback(target)

    def goto(self, url, *, wait_until, timeout):
        self.goto_calls.append((url, wait_until, timeout))
        self.navigate("https://tms.ssw.inf.br/bin/menu01")

    def wait_for_load_state(self, state, *, timeout):
        self.wait_calls.append((state, timeout))

    def wait_for_timeout(self, timeout):
        self.timeout_calls.append(timeout)


class FakeRuntime:
    def __init__(self, page):
        self.page = page
        self.context = SimpleNamespace(
            new_page=lambda: page,
            close=Mock(),
        )
        self.browser = SimpleNamespace(
            new_context=Mock(return_value=self.context),
            close=Mock(),
        )
        self.manager = SimpleNamespace(
            chromium=SimpleNamespace(launch=Mock(return_value=self.browser)),
            stop=Mock(),
        )

    def factory(self):
        return SimpleNamespace(start=Mock(return_value=self.manager))


def contract(*, with_probe=True):
    probe = None
    if with_probe:
        probe = ReadonlyProbeRule("#readonly", r"^(?P<value>Disponível)$", True)
    return CompanionContract(
        authenticated_marker_selector="#menu",
        login_form_selector="#login",
        identity=EvidenceRule("#user", r"^Usuário: (?P<value>.+)$"),
        unit=EvidenceRule("#unit", r"^Unidade: (?P<value>[A-Z0-9]{3})$"),
        readonly_probe=probe,
    )


def authenticated_page():
    return FakePage({
        "#menu": FakeLocator("Menu"),
        "#login": FakeLocator(visible=False),
        "#user": FakeLocator("Usuário: employee.one"),
        "#unit": FakeLocator("Unidade: CWB"),
        "#readonly": FakeLocator("Disponível"),
    })


def execute(
    page=None, *, agreement=lambda: True, probe=True, selected_contract=None, proxy=None,
):
    runtime = FakeRuntime(page or authenticated_page())
    logger = Mock(spec=logging.Logger)
    result = run_interactive_homologation(
        base_url="https://tms.ssw.inf.br",
        contract=selected_contract or contract(),
        expected_identity="employee.one",
        expected_unit="CWB",
        confirmation=agreement,
        execute_probe=probe,
        playwright_factory=runtime.factory,
        logger=logger,
        proxy=proxy,
    )
    return result, runtime, logger


def test_visible_ephemeral_context_and_readonly_probe_use_same_page():
    result, runtime, logger = execute()
    assert result == {"status": "concluida", "readonly_probe": "confirmada"}
    runtime.manager.chromium.launch.assert_called_once_with(headless=False)
    runtime.browser.new_context.assert_called_once_with(accept_downloads=False)
    assert runtime.page.goto_calls == [(LOGIN_URL, "commit", 60_000)]
    assert runtime.page.wait_calls == [("domcontentloaded", 60_000)]
    assert runtime.page.timeout_calls == [10_000]
    runtime.context.close.assert_called_once_with()
    runtime.browser.close.assert_called_once_with()
    runtime.manager.stop.assert_called_once_with()
    logger.info.assert_any_call("contexto_descartado")


def test_authenticated_proxy_is_passed_only_to_chromium_launch():
    proxy = CompanionProxy("http://proxy.corporativo:3128", "user", "proxy-secret")
    _, runtime, logger = execute(probe=False, proxy=proxy)
    runtime.manager.chromium.launch.assert_called_once_with(
        headless=False,
        proxy={
            "server": "http://proxy.corporativo:3128",
            "username": "user",
            "password": "proxy-secret",
        },
    )
    assert "proxy-secret" not in repr(logger.method_calls)


def test_origin_monitor_does_not_intercept_network_and_accepts_official_redirects():
    page = authenticated_page()
    discarded = Mock()
    monitor = attach_official_navigation_monitor(
        page, on_violation=discarded, logger=Mock(spec=logging.Logger),
    )

    assert not hasattr(page, "route")
    page.navigate("https://sistema.ssw.inf.br/bin/menu01")
    monitor.ensure_allowed()
    discarded.assert_not_called()

    # Navegações de frames auxiliares (como reCAPTCHA) não mudam a origem da
    # página principal e não invalidam a sessão.
    page.navigate(
        "https://www.recaptcha.net/recaptcha/challenge",
        frame=SimpleNamespace(url="about:blank"),
    )
    monitor.ensure_allowed()
    discarded.assert_not_called()


@pytest.mark.parametrize("url", [
    "http://tms.ssw.inf.br/bin/ssw0422",
    "https://evil.example/bin/menu01",
    "data:text/html,blocked",
])
def test_origin_monitor_discards_non_official_main_frame_destinations(url):
    page = authenticated_page()
    discarded = Mock()
    monitor = attach_official_navigation_monitor(
        page, on_violation=discarded, logger=Mock(spec=logging.Logger),
    )

    page.navigate(url)

    discarded.assert_called_once_with()
    with pytest.raises(CompanionError, match="sessão foi descartada"):
        monitor.ensure_allowed()


def test_operator_confirmation_happens_after_official_page_is_opened():
    page = authenticated_page()
    observed = []

    def confirm():
        observed.extend(page.goto_calls)
        return True

    execute(page, agreement=confirm, probe=False)
    assert observed == [(LOGIN_URL, "commit", 60_000)]


def test_url_change_alone_does_not_authenticate():
    page = FakePage({
        "#menu": FakeLocator(count=0),
        "#login": FakeLocator(visible=False),
    }, url="https://tms.ssw.inf.br/bin/menu01")
    with pytest.raises(CompanionError, match="Marcador autenticado"):
        validate_authenticated_context(
            page, contract(), login_url=LOGIN_URL,
            expected_identity="employee.one", expected_unit="CWB",
        )


def test_login_url_never_authenticates_even_with_menu_markers():
    page = authenticated_page()
    page.url = LOGIN_URL
    with pytest.raises(CompanionError, match="Menu Principal oficial"):
        validate_authenticated_context(
            page, contract(), login_url=LOGIN_URL,
            expected_identity="employee.one", expected_unit="CWB",
        )


def test_authenticated_evidence_accepts_redirect_between_official_hosts():
    page = authenticated_page()
    page.url = "https://sistema.ssw.inf.br/bin/menu01"
    validate_authenticated_context(
        page, contract(), login_url=LOGIN_URL,
        expected_identity="employee.one", expected_unit="CWB",
    )


def test_qualified_login_selector_ignores_generic_frm_reused_by_menu():
    qualified = (
        ":is(#frm):has(input[name='f1']):has(input[name='f2'])"
        ":has(input[name='f3']):has(input[name='f4'])"
    )
    page = FakePage({
        "#frm": FakeLocator("Menu"),
        qualified: FakeLocator(count=0, visible=False),
        "#user": FakeLocator("Usuário: employee.one"),
        "#unit": FakeLocator("Unidade: CWB"),
    }, url="https://tms.ssw.inf.br/bin/menu01")
    selected_contract = CompanionContract(
        authenticated_marker_selector="#frm",
        login_form_selector=qualified,
        identity=EvidenceRule("#user", r"^Usuário: (?P<value>.+)$"),
        unit=EvidenceRule("#unit", r"^Unidade: (?P<value>[A-Z0-9]{3})$"),
    )

    validate_authenticated_context(
        page, selected_contract, login_url=LOGIN_URL,
        expected_identity="employee.one", expected_unit="CWB",
    )


def test_automatic_contract_validates_observed_identity_domain_and_menu_unit():
    automatic = CompanionContract.from_mapping({
        "version": 2,
        "login": {
            "form_selector": KNOWN_LOGIN_FORM_SELECTOR,
            "domain_selector": KNOWN_LOGIN_DOMAIN_SELECTOR,
            "cpf_selector": KNOWN_LOGIN_CPF_SELECTOR,
            "identity_selector": KNOWN_LOGIN_IDENTITY_SELECTOR,
            "password_selector": KNOWN_LOGIN_PASSWORD_SELECTOR,
        },
        "menu": {
            "path": "/bin/menu01",
            "marker_selector": KNOWN_MENU_MARKER_SELECTOR,
            "unit_selector": KNOWN_MENU_UNIT_SELECTOR,
        },
    })
    page = FakePage({
        KNOWN_LOGIN_FORM_SELECTOR: FakeLocator(count=0, visible=False),
        KNOWN_MENU_MARKER_SELECTOR: FakeLocator(),
        KNOWN_MENU_UNIT_SELECTOR: FakeLocator(value="CWB"),
    }, url="https://tms.ssw.inf.br/bin/menu01")

    validate_authenticated_context(
        page, automatic, login_url=LOGIN_URL,
        expected_identity="employee.one", expected_unit="CWB",
        observed_identity="employee.one", expected_domain="ROD",
        observed_domain="ROD",
    )

    with pytest.raises(CompanionError, match="domínio autenticado"):
        validate_authenticated_context(
            page, automatic, login_url=LOGIN_URL,
            expected_identity="employee.one", expected_unit="CWB",
            observed_identity="employee.one", expected_domain="OUTRO",
            observed_domain="ROD",
        )


@pytest.mark.parametrize("selector,text,message", [
    ("#user", "Usuário: employee.two", "identidade exibida"),
    ("#unit", "Unidade: BIG", "unidade exibida"),
])
def test_identity_and_unit_mismatch_fail_closed(selector, text, message):
    page = authenticated_page()
    page.locators[selector] = FakeLocator(text)
    with pytest.raises(CompanionError, match=message):
        execute(page)


def test_probe_needs_both_cli_request_and_explicit_contract_authorization():
    result, _, _ = execute(probe=False, selected_contract=contract(with_probe=False))
    assert result["readonly_probe"] == "nao_solicitada"
    with pytest.raises(CompanionError, match="Nenhuma sondagem"):
        execute(probe=True, selected_contract=contract(with_probe=False))


def test_rejected_confirmation_still_discards_context():
    runtime = FakeRuntime(authenticated_page())
    with pytest.raises(CompanionError, match="Confirmação explícita"):
        run_interactive_homologation(
            base_url="https://tms.ssw.inf.br", contract=contract(),
            expected_identity="employee.one", expected_unit="CWB",
            confirmation=lambda: False, execute_probe=False,
            playwright_factory=runtime.factory, logger=Mock(spec=logging.Logger),
        )
    runtime.context.close.assert_called_once_with()
    runtime.browser.close.assert_called_once_with()
    runtime.manager.stop.assert_called_once_with()


@pytest.mark.parametrize("url", [
    "http://tms.ssw.inf.br", "https://evil.example", "https://tms.ssw.inf.br.evil.test",
    "https://user:password@tms.ssw.inf.br",
])
def test_only_official_https_origin_is_accepted(url):
    with pytest.raises((CompanionError, ValueError)):
        official_login_url(url)


def test_contract_requires_named_identity_and_explicit_readonly_authorization(tmp_path):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps({
        "authenticated_marker_selector": "#menu",
        "login_form_selector": "#login",
        "identity": {"selector": "#user", "pattern": "(.+)"},
        "unit": {"selector": "#unit", "pattern": "(?P<value>.+)"},
    }), encoding="utf-8")
    with pytest.raises(CompanionError, match="grupo nomeado"):
        CompanionContract.from_file(path)


def test_technical_error_log_contains_only_category_not_exception_details():
    sensitive = "senha=super-secret cpf=12345678900"
    runtime = FakeRuntime(authenticated_page())
    runtime.manager.chromium.launch.side_effect = RuntimeError(sensitive)
    logger = Mock(spec=logging.Logger)
    with pytest.raises(CompanionError, match="Falha técnica"):
        run_interactive_homologation(
            base_url="https://tms.ssw.inf.br", contract=contract(),
            expected_identity="employee.one", expected_unit="CWB",
            confirmation=lambda: True, execute_probe=False,
            playwright_factory=runtime.factory, logger=logger,
        )
    serialized = repr(logger.method_calls)
    assert "super-secret" not in serialized
    assert "12345678900" not in serialized
    assert "'unexpected'" in serialized


@pytest.mark.parametrize("detail,message", [
    ("HTTP 407 password=proxy-secret", "HTTP 407"),
    ("net::ERR_PROXY_CONNECTION_FAILED password=proxy-secret", "conectar ao SSW"),
])
def test_proxy_navigation_errors_are_sanitized(detail, message):
    page = authenticated_page()
    page.goto = Mock(side_effect=RuntimeError(detail))
    runtime = FakeRuntime(page)
    logger = Mock(spec=logging.Logger)
    with pytest.raises(CompanionError, match=message) as captured:
        run_interactive_homologation(
            base_url="https://tms.ssw.inf.br", contract=contract(),
            expected_identity="employee.one", expected_unit="CWB",
            confirmation=lambda: True, execute_probe=False,
            playwright_factory=runtime.factory, logger=logger,
            proxy=CompanionProxy("http://proxy:3128", "user", "proxy-secret"),
        )
    assert "proxy-secret" not in str(captured.value)
    assert "proxy-secret" not in repr(logger.method_calls)


def test_runtime_source_has_no_session_export_or_credential_automation():
    source = Path("modules/local_agent/ssw_companion.py").read_text(
        encoding="utf-8"
    ).casefold()
    for forbidden in (
        "storage_state", "add_cookies", "document.cookie", "g-recaptcha-response",
        ".fill(", ".type(", "password=", "requests.session",
    ):
        assert forbidden not in source
    assert 'page.route(' not in source

