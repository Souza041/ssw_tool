import json
import logging
import os
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.local_agent.ssw_companion import CompanionError
from modules.local_agent import ssw_companion_config as config
from modules.local_agent.ssw_companion_proxy import CompanionProxy


class PresenceOnlyLocator:
    def __init__(self, *, count=1, visible=True, value=""):
        self.number = count
        self.visible = visible
        self.value = value

    def count(self):
        return self.number

    def is_visible(self):
        return self.visible

    def inner_text(self, **_kwargs):
        raise AssertionError("O configurador não pode ler conteúdo do elemento")

    def input_value(self, **_kwargs):
        return self.value


REAL_LOGIN_HTML = """
<form id="frm">
  <input name="f1">
  <input name="f2">
  <input name="f3">
  <input name="f4">
</form>
"""


class LoginHtmlParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_form = False
        self.form_found = False
        self.input_names = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form" and attributes.get("id") == "frm":
            self.in_form = self.form_found = True
        elif self.in_form and tag == "input" and attributes.get("name"):
            self.input_names.add(attributes["name"])

    def handle_endtag(self, tag):
        if tag == "form" and self.in_form:
            self.in_form = False


class RealLoginHtmlPage:
    def __init__(self, html):
        self.parsed = LoginHtmlParser()
        self.parsed.feed(html)
        self.qualified_visibility_checked = False

    def locator(self, selector):
        if selector == "#frm" and self.parsed.form_found:
            return PresenceOnlyLocator(count=1, visible=True)
        required = {"f1", "f2", "f3", "f4"}
        if selector == config.KNOWN_LOGIN_FORM_SELECTOR:
            return PresenceOnlyLocator(
                count=1 if required <= self.parsed.input_names else 0,
                visible=required <= self.parsed.input_names,
            )
        field_names = {
            config.KNOWN_LOGIN_DOMAIN_SELECTOR: "f1",
            config.KNOWN_LOGIN_CPF_SELECTOR: "f2",
            config.KNOWN_LOGIN_IDENTITY_SELECTOR: "f3",
            config.KNOWN_LOGIN_PASSWORD_SELECTOR: "f4",
        }
        if selector in field_names:
            present = field_names[selector] in self.parsed.input_names
            return PresenceOnlyLocator(count=1 if present else 0, visible=present)
        if selector.startswith(":is(#frm)") and required <= self.parsed.input_names:
            page = self

            class StructuralLocator(PresenceOnlyLocator):
                def is_visible(self):
                    page.qualified_visibility_checked = True
                    raise AssertionError("A qualificação estrutural não deve repetir is_visible()")

            return StructuralLocator(count=1, visible=False)
        return PresenceOnlyLocator(count=0, visible=False)


class WizardPage:
    def __init__(self, *, auto_authenticate=True):
        self.url = "https://tms.ssw.inf.br/bin/ssw0422"
        self.main_frame = SimpleNamespace(url=self.url)
        self.login_visible = True
        self.events = {}
        self.goto_calls = []
        self.wait_calls = []
        self.timeout_calls = []
        self.lifecycle_calls = []
        self.auto_authenticate = auto_authenticate
        self.redirect_on_wait = False
        self.reload_calls = []

    def locator(self, selector):
        if selector == config.KNOWN_LOGIN_FORM_SELECTOR:
            return PresenceOnlyLocator(
                count=1 if self.login_visible else 0,
                visible=self.login_visible,
            )
        if selector == config.KNOWN_MENU_MARKER_SELECTOR:
            return PresenceOnlyLocator(
                count=0 if self.login_visible else 1,
                visible=not self.login_visible,
            )
        if selector == config.KNOWN_MENU_UNIT_SELECTOR:
            present = self.login_visible or self.url.endswith("/bin/menu01")
            return PresenceOnlyLocator(
                count=1 if present else 0, visible=present, value="CWB",
            )
        if selector in {
            config.KNOWN_LOGIN_DOMAIN_SELECTOR,
            config.KNOWN_LOGIN_CPF_SELECTOR,
            config.KNOWN_LOGIN_IDENTITY_SELECTOR,
            config.KNOWN_LOGIN_PASSWORD_SELECTOR,
        }:
            values = {
                config.KNOWN_LOGIN_DOMAIN_SELECTOR: "ROD",
                config.KNOWN_LOGIN_IDENTITY_SELECTOR: "employee.one",
            }
            return PresenceOnlyLocator(
                count=1 if self.login_visible else 0,
                visible=self.login_visible,
                value=values.get(selector, ""),
            )
        if selector.startswith(":is(#login)") or selector.startswith(":is(#frm)"):
            return PresenceOnlyLocator(visible=self.login_visible)
        known = {"#login", "#frm", "#menu", "#identity", "#unit", "#probe"}
        if selector not in known:
            return PresenceOnlyLocator(count=0, visible=False)
        if selector == "#login":
            return PresenceOnlyLocator(visible=self.login_visible)
        if selector == "#frm":
            return PresenceOnlyLocator(
                count=1 if self.login_visible else 0,
                visible=self.login_visible,
            )
        return PresenceOnlyLocator()

    def on(self, event, callback):
        self.lifecycle_calls.append(("listener", event))
        self.events.setdefault(event, []).append(callback)

    def navigate(self, url):
        self.url = self.main_frame.url = url
        for callback in self.events.get("framenavigated", []):
            callback(self.main_frame)

    def goto(self, url, *, wait_until, timeout):
        self.lifecycle_calls.append(("goto", wait_until))
        self.goto_calls.append((url, wait_until, timeout))
        self.navigate(url)

    def wait_for_load_state(self, state, *, timeout):
        self.lifecycle_calls.append(("load_state", state))
        self.wait_calls.append((state, timeout))

    def wait_for_timeout(self, timeout):
        self.lifecycle_calls.append(("stabilize", timeout))
        self.timeout_calls.append(timeout)
        if timeout == 500 and self.redirect_on_wait:
            self.navigate("https://external.example/login")
        elif timeout == 500 and self.auto_authenticate and self.login_visible:
            self.login_visible = False
            self.navigate("https://tms.ssw.inf.br/bin/menu01")

    def reload(self, *, wait_until, timeout):
        self.reload_calls.append((wait_until, timeout))
        self.login_visible = True


class WizardRuntime:
    def __init__(self, page):
        self.context = SimpleNamespace(new_page=lambda: page, close=Mock())
        self.browser = SimpleNamespace(
            new_context=Mock(return_value=self.context), close=Mock(),
        )
        self.manager = SimpleNamespace(
            chromium=SimpleNamespace(launch=Mock(return_value=self.browser)),
            stop=Mock(),
        )

    def factory(self):
        return SimpleNamespace(start=Mock(return_value=self.manager))


def valid_data():
    return config.build_contract_data(
        login_form="#login",
        authenticated_marker="#menu",
        identity_selector="#identity",
        identity_pattern=r"^Usuário: (?P<value>.+)$",
        unit_selector="#unit",
        unit_pattern=r"^Unidade: (?P<value>[A-Z0-9]{3})$",
    )


def expected_operator():
    return {
        "expected_domain": "ROD",
        "expected_identity": "employee.one",
        "expected_unit": "CWB",
    }


def test_selector_validation_checks_syntax_presence_visibility_and_uniqueness():
    page = WizardPage()
    assert config.validate_manual_selector(page, "#login", field="login") == "#login"
    with pytest.raises(CompanionError, match="exatamente um"):
        config.validate_manual_selector(page, "#missing", field="missing")
    page.locator = lambda _selector: PresenceOnlyLocator(count=2)
    with pytest.raises(CompanionError, match="exatamente um"):
        config.validate_manual_selector(page, ".duplicate", field="duplicate")
    page.locator = Mock(side_effect=RuntimeError("invalid selector"))
    with pytest.raises(CompanionError, match="Sintaxe inválida"):
        config.validate_manual_selector(page, "[", field="invalid")


def test_login_container_requires_all_specific_authentication_fields():
    page = WizardPage()
    original_locator = page.locator

    def without_login_fields(selector):
        if selector.startswith(":is(#frm)"):
            return PresenceOnlyLocator(count=0, visible=False)
        return original_locator(selector)

    page.locator = without_login_fields
    with pytest.raises(CompanionError, match="f1, f2, f3 e f4"):
        config.qualify_login_form_selector(page, "#frm")


def test_real_frm_html_is_accepted_by_structural_count_without_second_visibility_check():
    page = RealLoginHtmlPage(REAL_LOGIN_HTML)

    qualified = config.qualify_login_form_selector(page, "#frm")

    assert qualified == (
        ":is(#frm):has(input[name='f1']):has(input[name='f2'])"
        ":has(input[name='f3']):has(input[name='f4'])"
    )
    assert page.qualified_visibility_checked is False


def test_real_frm_html_is_recognized_by_automatic_contract():
    assert config._known_login_ready(RealLoginHtmlPage(REAL_LOGIN_HTML)) is True


@pytest.mark.parametrize("value", [
    "SELETOR_APROVADO_DO_MENU", "TODO", "placeholder", "seletor_aqui",
])
def test_illustrative_selectors_are_rejected(value):
    with pytest.raises(CompanionError, match="ilustrativo"):
        config.validate_manual_selector(WizardPage(), value, field="menu")


def test_patterns_are_manual_and_require_only_named_value_group():
    valid = r"^Usuário: (?P<value>.+)$"
    assert config.validate_manual_pattern(valid, field="identity") == valid
    for invalid in (r"^(.+)$", r"(?P<value>.+)(?P<extra>.+)", "TODO"):
        with pytest.raises(CompanionError):
            config.validate_manual_pattern(invalid, field="identity")


def test_contract_requires_distinct_selectors_and_complete_optional_probe():
    with pytest.raises(CompanionError, match="distinto"):
        config.build_contract_data(
            login_form="#same", authenticated_marker="#same",
            identity_selector="#identity", identity_pattern=r"(?P<value>.+)",
            unit_selector="#unit", unit_pattern=r"(?P<value>.+)",
        )
    with pytest.raises(CompanionError, match="seletor e expressão"):
        config.build_contract_data(
            login_form="#login", authenticated_marker="#menu",
            identity_selector="#identity", identity_pattern=r"(?P<value>.+)",
            unit_selector="#unit", unit_pattern=r"(?P<value>.+)",
            readonly_selector="#probe",
        )


def test_wizard_never_reads_element_content_and_builds_valid_contract(monkeypatch, tmp_path):
    page = WizardPage()
    runtime = WizardRuntime(page)
    captured = {}
    input_fn = Mock(side_effect=AssertionError("Seletores não devem ser solicitados"))

    def fake_write(path, data, *, confirm_overwrite):
        captured.update(data)

    monkeypatch.setattr(config, "secure_write_contract", fake_write)
    proxy = CompanionProxy("http://proxy.corporativo:3128", "user", "proxy-secret")
    result = config.run_configuration_wizard(
        base_url="https://tms.ssw.inf.br",
        output_path=tmp_path / "contract.json",
        playwright_factory=runtime.factory,
        input_fn=input_fn,
        output_fn=lambda _message: None,
        proxy=proxy,
        **expected_operator(),
    )
    assert result == tmp_path / "contract.json"
    assert captured == config.automatic_contract_data()
    input_fn.assert_not_called()
    runtime.manager.chromium.launch.assert_called_once_with(
        headless=False,
        proxy={
            "server": "http://proxy.corporativo:3128",
            "username": "user",
            "password": "proxy-secret",
        },
    )
    runtime.browser.new_context.assert_called_once_with(accept_downloads=False)
    assert page.goto_calls == [
        ("https://tms.ssw.inf.br/bin/ssw0422", "commit", 60_000),
    ]
    assert page.reload_calls == []
    assert page.wait_calls == [("domcontentloaded", 60_000)]
    assert page.timeout_calls == [10_000, 500]
    assert page.lifecycle_calls[:3] == [
        ("goto", "commit"),
        ("load_state", "domcontentloaded"),
        ("stabilize", 10_000),
    ]
    assert ("listener", "framenavigated") in page.lifecycle_calls
    runtime.context.close.assert_called_once_with()
    runtime.browser.close.assert_called_once_with()
    runtime.manager.stop.assert_called_once_with()


def test_generic_frm_is_qualified_and_does_not_reject_authenticated_menu(monkeypatch, tmp_path):
    page = WizardPage()
    runtime = WizardRuntime(page)
    captured = {}

    monkeypatch.setattr(
        config, "secure_write_contract",
        lambda _path, data, *, confirm_overwrite: captured.update(data),
    )

    config.run_configuration_wizard(
        base_url="https://tms.ssw.inf.br",
        output_path=tmp_path / "contract.json",
        playwright_factory=runtime.factory,
        input_fn=Mock(side_effect=AssertionError("Wizard manual removido")),
        output_fn=lambda _message: None,
        **expected_operator(),
    )

    assert captured["version"] == 2
    assert captured["login"]["form_selector"] == config.KNOWN_LOGIN_FORM_SELECTOR
    assert captured["menu"]["marker_selector"] == config.KNOWN_MENU_MARKER_SELECTOR


def test_login_form_without_menu_never_authenticates():
    page = WizardPage(auto_authenticate=False)
    assert config.wait_for_known_login(page, timeout_seconds=1, reloads=0) == "login"
    with pytest.raises(CompanionError, match="prazo do login humano expirou"):
        config.wait_for_authenticated_menu(
            page,
            contract=config.CompanionContract.from_mapping(
                config.automatic_contract_data()
            ),
            login_url="https://tms.ssw.inf.br/bin/ssw0422",
            timeout_seconds=1,
            **expected_operator(),
        )
    assert page.goto_calls == []
    assert page.reload_calls == []


def test_human_wait_rejects_divergent_identity_after_natural_menu_navigation():
    page = WizardPage()
    contract = config.CompanionContract.from_mapping(config.automatic_contract_data())

    with pytest.raises(CompanionError, match="identidade exibida diverge"):
        config.wait_for_authenticated_menu(
            page,
            contract=contract,
            login_url="https://tms.ssw.inf.br/bin/ssw0422",
            expected_domain="ROD",
            expected_identity="another.employee",
            expected_unit="CWB",
            timeout_seconds=30,
        )

    assert page.goto_calls == []
    assert page.reload_calls == []


def test_partial_login_structure_permanently_blocks_initial_recovery():
    page = WizardPage(auto_authenticate=False)

    def partial_login(selector):
        if selector == config.KNOWN_LOGIN_DOMAIN_SELECTOR:
            return PresenceOnlyLocator(count=1, visible=True)
        return PresenceOnlyLocator(count=0, visible=False)

    page.locator = partial_login
    with pytest.raises(CompanionError, match="não ficou disponível"):
        config.wait_for_known_login(page, timeout_seconds=2, reloads=2)
    assert page.goto_calls == []
    assert page.reload_calls == []


def test_visible_challenge_permanently_blocks_initial_recovery():
    page = WizardPage(auto_authenticate=False)

    def challenge_only(selector):
        if selector == config.CAPTCHA_STRUCTURE_SELECTOR:
            return PresenceOnlyLocator(count=2, visible=True)
        return PresenceOnlyLocator(count=0, visible=False)

    page.locator = challenge_only
    with pytest.raises(CompanionError, match="não ficou disponível"):
        config.wait_for_known_login(page, timeout_seconds=2, reloads=2)
    assert page.reload_calls == []


def test_blank_page_recovery_is_bounded_and_rechecks_known_form():
    page = WizardPage(auto_authenticate=False)
    page.login_visible = False

    assert config.wait_for_known_login(
        page, timeout_seconds=2, reloads=1,
    ) == "login"
    assert page.reload_calls == [("domcontentloaded", 30_000)]


def test_wizard_external_redirect_discards_context_and_rejects_session(tmp_path):
    page = WizardPage()
    page.redirect_on_wait = True
    runtime = WizardRuntime(page)

    with pytest.raises(CompanionError, match="saiu da origem oficial"):
        config.run_configuration_wizard(
            base_url="https://tms.ssw.inf.br",
            output_path=tmp_path / "contract.json",
            playwright_factory=runtime.factory,
            input_fn=Mock(),
            output_fn=lambda _message: None,
            **expected_operator(),
        )

    runtime.context.close.assert_called_once_with()
    runtime.browser.close.assert_called_once_with()
    runtime.manager.stop.assert_called_once_with()


def test_existing_contract_is_not_overwritten_without_confirmation(tmp_path):
    path = tmp_path / "contract.json"
    path.write_text("original", encoding="utf-8")
    with pytest.raises(CompanionError, match="preservado"):
        config.secure_write_contract(
            path, valid_data(), confirm_overwrite=lambda: False,
        )
    assert path.read_text(encoding="utf-8") == "original"


def test_atomic_permission_failure_preserves_existing_contract(monkeypatch, tmp_path):
    path = tmp_path / "contract.json"
    path.write_text("original", encoding="utf-8")
    monkeypatch.setattr(
        config, "_restrict_permissions",
        Mock(side_effect=CompanionError("permissões recusadas")),
    )
    with pytest.raises(CompanionError, match="permissões recusadas"):
        config.secure_write_contract(path, valid_data(), confirm_overwrite=lambda: True)
    assert path.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob("*.tmp")) == []


def test_secure_write_uses_restricted_permission_step(monkeypatch, tmp_path):
    path = tmp_path / "contract.json"
    restricted = []

    def restrict(candidate):
        restricted.append(candidate)
        candidate.chmod(0o600)

    monkeypatch.setattr(config, "_restrict_permissions", restrict)
    config.secure_write_contract(path, valid_data(), confirm_overwrite=lambda: False)
    assert len(restricted) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["login_form_selector"] == "#login"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_windows_acl_uses_current_sid_without_shell(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "whoami.exe":
            return SimpleNamespace(stdout='"DOMAIN\\employee","S-1-5-21-1-2-3-1001"\n')
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(config.subprocess, "run", fake_run)
    sid = config._current_windows_sid()
    assert sid == "S-1-5-21-1-2-3-1001"
    monkeypatch.setattr(config.os, "name", "nt")
    config._restrict_permissions(tmp_path / "contract.json")
    icacls_args, icacls_kwargs = calls[-1]
    assert icacls_args[0] == "icacls.exe"
    assert "/inheritance:r" in icacls_args
    assert "*S-1-5-21-1-2-3-1001:(R,W)" in icacls_args
    assert icacls_kwargs["check"] is True
    assert "shell" not in icacls_kwargs


def test_configurator_source_does_not_collect_page_content_or_secrets():
    source = Path("modules/local_agent/ssw_companion_config.py").read_text(
        encoding="utf-8"
    ).casefold()
    for forbidden in (
        "inner_text", "text_content", "page.content", "storage_state",
        "add_cookies", "document.cookie", "g-recaptcha-response", ".fill(", ".type(",
    ):
        assert forbidden not in source
    assert 'page.route(' not in source


def test_configuration_proxy_failure_is_sanitized_and_closes_runtime(monkeypatch, tmp_path):
    page = WizardPage()
    page.goto = Mock(side_effect=RuntimeError(
        "net::ERR_PROXY_AUTH_REQUESTED HTTP 407 password=proxy-secret"
    ))
    runtime = WizardRuntime(page)
    with pytest.raises(CompanionError, match="HTTP 407") as captured:
        config.run_configuration_wizard(
            base_url="https://tms.ssw.inf.br",
            output_path=tmp_path / "contract.json",
            playwright_factory=runtime.factory,
            input_fn=lambda _prompt: "",
            output_fn=lambda _message: None,
            proxy=CompanionProxy("http://proxy:3128", "user", "proxy-secret"),
            **expected_operator(),
        )
    assert "proxy-secret" not in str(captured.value)
    runtime.context.close.assert_called_once_with()
    runtime.browser.close.assert_called_once_with()
    runtime.manager.stop.assert_called_once_with()

