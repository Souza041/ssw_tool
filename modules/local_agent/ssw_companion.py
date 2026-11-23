"""Primitivas seguras do Companion interativo para autenticação assistida do SSW.

O operador informa as credenciais somente na página oficial aberta pelo Chromium.
A aplicação observa apenas evidências DOM descritas por um contrato local aprovado;
o runtime integrado reutiliza estas primitivas sem exportar a sessão do navegador.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import urlsplit
from typing import Callable, Protocol

from ssw.settings import normalizar_base_url


OFFICIAL_SSW_HOSTS = frozenset({"tms.ssw.inf.br", "sistema.ssw.inf.br"})
LOGIN_PATH = "/bin/ssw0422"
MENU_PATH = "/bin/menu01"
KNOWN_LOGIN_FORM_SELECTOR = (
    "#frm:has(input[name='f1']):has(input[name='f2'])"
    ":has(input[name='f3']):has(input[name='f4'])"
)
KNOWN_LOGIN_DOMAIN_SELECTOR = "input[name='f1']"
KNOWN_LOGIN_CPF_SELECTOR = "input[name='f2']"
KNOWN_LOGIN_IDENTITY_SELECTOR = "input[name='f3']"
KNOWN_LOGIN_PASSWORD_SELECTOR = "input[name='f4']"
KNOWN_MENU_MARKER_SELECTOR = 'input[name="f3"][maxlength="3"]'
KNOWN_MENU_UNIT_SELECTOR = "input[name='f2']"


class CompanionError(RuntimeError):
    """Falha segura da homologação, sem incorporar dados da página."""


@dataclass(frozen=True)
class EvidenceRule:
    selector: str
    pattern: str


@dataclass(frozen=True)
class ReadonlyProbeRule:
    selector: str
    pattern: str
    authorized: bool


@dataclass(frozen=True)
class CompanionContract:
    authenticated_marker_selector: str
    login_form_selector: str
    identity: EvidenceRule
    unit: EvidenceRule
    readonly_probe: ReadonlyProbeRule | None = None
    version: int = 1
    login_domain_selector: str = ""
    login_identity_selector: str = ""
    menu_unit_selector: str = ""

    @classmethod
    def from_file(cls, path: Path) -> "CompanionContract":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CompanionError("Contrato local ausente ou inválido.") from exc
        return cls.from_mapping(raw)

    @classmethod
    def from_mapping(cls, raw: object) -> "CompanionContract":
        if isinstance(raw, dict) and raw.get("version") == 2:
            if set(raw) != {"version", "login", "menu"}:
                raise CompanionError("Contrato automático possui campos não autorizados.")
            login = raw.get("login")
            menu = raw.get("menu")
            if not isinstance(login, dict) or set(login) != {
                "form_selector", "domain_selector", "cpf_selector",
                "identity_selector", "password_selector",
            }:
                raise CompanionError("Contrato automático de login inválido.")
            if not isinstance(menu, dict) or set(menu) != {
                "path", "marker_selector", "unit_selector",
            } or menu.get("path") != MENU_PATH:
                raise CompanionError("Contrato automático de menu inválido.")
            form = _bounded_text(login.get("form_selector"), "login.form_selector")
            domain = _bounded_text(login.get("domain_selector"), "login.domain_selector")
            identity_selector = _bounded_text(
                login.get("identity_selector"), "login.identity_selector"
            )
            marker = _bounded_text(menu.get("marker_selector"), "menu.marker_selector")
            unit_selector = _bounded_text(menu.get("unit_selector"), "menu.unit_selector")
            _bounded_text(login.get("cpf_selector"), "login.cpf_selector")
            _bounded_text(login.get("password_selector"), "login.password_selector")
            expected = {
                "form_selector": KNOWN_LOGIN_FORM_SELECTOR,
                "domain_selector": KNOWN_LOGIN_DOMAIN_SELECTOR,
                "cpf_selector": KNOWN_LOGIN_CPF_SELECTOR,
                "identity_selector": KNOWN_LOGIN_IDENTITY_SELECTOR,
                "password_selector": KNOWN_LOGIN_PASSWORD_SELECTOR,
                "marker_selector": KNOWN_MENU_MARKER_SELECTOR,
                "unit_selector": KNOWN_MENU_UNIT_SELECTOR,
            }
            actual = {
                "form_selector": form,
                "domain_selector": domain,
                "cpf_selector": str(login.get("cpf_selector")),
                "identity_selector": identity_selector,
                "password_selector": str(login.get("password_selector")),
                "marker_selector": marker,
                "unit_selector": unit_selector,
            }
            if actual != expected:
                raise CompanionError("Contrato automático diverge dos seletores aprovados.")
            return cls(
                marker, form,
                EvidenceRule(identity_selector, r"^(?P<value>.+)$"),
                EvidenceRule(unit_selector, r"^(?P<value>.+)$"),
                None, 2, domain, identity_selector, unit_selector,
            )
        if not isinstance(raw, dict) or set(raw) - {
            "authenticated_marker_selector", "login_form_selector",
            "identity", "unit", "readonly_probe",
        }:
            raise CompanionError("Contrato local possui campos não autorizados.")
        identity = _evidence_rule(raw.get("identity"), "identity")
        unit = _evidence_rule(raw.get("unit"), "unit")
        marker = _bounded_text(raw.get("authenticated_marker_selector"), "marker")
        login_form = _bounded_text(raw.get("login_form_selector"), "login_form")
        probe_raw = raw.get("readonly_probe")
        probe = None
        if probe_raw is not None:
            if not isinstance(probe_raw, dict) or set(probe_raw) != {
                "selector", "pattern", "authorized",
            } or probe_raw.get("authorized") is not True:
                raise CompanionError("Sondagem read-only não possui autorização explícita.")
            rule = _evidence_rule(probe_raw, "readonly_probe", allow_authorized=True)
            probe = ReadonlyProbeRule(rule.selector, rule.pattern, True)
        return cls(marker, login_form, identity, unit, probe)


def _bounded_text(value: object, field: str, *, maximum: int = 512) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum or "\x00" in text:
        raise CompanionError(f"Campo {field} inválido no contrato local.")
    return text


def _evidence_rule(
    raw: object, field: str, *, allow_authorized: bool = False,
) -> EvidenceRule:
    expected = {"selector", "pattern"}
    if allow_authorized:
        expected.add("authorized")
    if not isinstance(raw, dict) or set(raw) != expected:
        raise CompanionError(f"Regra {field} inválida no contrato local.")
    selector = _bounded_text(raw.get("selector"), f"{field}.selector")
    pattern = _bounded_text(raw.get("pattern"), f"{field}.pattern")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise CompanionError(f"Expressão {field} inválida no contrato local.") from exc
    if "value" not in compiled.groupindex:
        raise CompanionError(f"Expressão {field} deve conter o grupo nomeado value.")
    return EvidenceRule(selector, pattern)


def _normalized(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def official_login_url(base_url: str) -> str:
    normalized = normalizar_base_url(base_url)
    parsed = urlsplit(normalized)
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in OFFICIAL_SSW_HOSTS:
        raise CompanionError("A homologação aceita somente a origem HTTPS oficial do SSW.")
    return f"{normalized}{LOGIN_PATH}"


def _same_official_origin(url: str, login_url: str) -> bool:
    target = urlsplit(url)
    origin = urlsplit(login_url)
    return (
        target.scheme == "https"
        and target.hostname == origin.hostname
        and target.port == origin.port
        and target.username is None
        and target.password is None
    )


def _official_https_navigation(url: str) -> bool:
    target = urlsplit(url)
    return (
        target.scheme == "https"
        and (target.hostname or "").casefold() in OFFICIAL_SSW_HOSTS
        and target.username is None
        and target.password is None
    )


def _official_menu_navigation(url: str) -> bool:
    return (
        _official_https_navigation(url)
        and urlsplit(url).path.rstrip("/") == MENU_PATH
    )


def configure_sanitized_log(path: Path) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("nexus.ssw_companion")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    handler = RotatingFileHandler(
        path, maxBytes=1_000_000, backupCount=3, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


class LocatorLike(Protocol):
    def count(self) -> int: ...
    def is_visible(self) -> bool: ...
    def inner_text(self, *, timeout: int) -> str: ...


class PageLike(Protocol):
    url: str
    def goto(self, url: str, *, wait_until: str, timeout: int): ...
    def locator(self, selector: str) -> LocatorLike: ...
    def on(self, event: str, callback: Callable): ...


def _visible(page: PageLike, selector: str) -> bool:
    locator = page.locator(selector)
    return locator.count() > 0 and locator.is_visible()


def _extract(page: PageLike, rule: EvidenceRule) -> str:
    locator = page.locator(rule.selector)
    if locator.count() != 1 or not locator.is_visible():
        raise CompanionError("Evidência autenticada ausente ou ambígua.")
    text = locator.inner_text(timeout=5_000)
    if len(text) > 4_096:
        raise CompanionError("Evidência autenticada excede o limite permitido.")
    match = re.search(rule.pattern, text)
    value = match.group("value").strip() if match else ""
    if not value:
        raise CompanionError("Evidência autenticada não corresponde ao contrato.")
    return value


def _input_value(page: PageLike, selector: str) -> str:
    locator = page.locator(selector)
    if locator.count() != 1 or not locator.is_visible():
        raise CompanionError("Campo autenticado ausente ou ambíguo.")
    try:
        value = locator.input_value(timeout=5_000).strip()
    except Exception as exc:
        raise CompanionError("Não foi possível validar o campo autenticado.") from exc
    if not value or len(value) > 256:
        raise CompanionError("Campo autenticado vazio ou fora do limite.")
    return value


def capture_known_login_identity(
    page: PageLike, contract: CompanionContract,
) -> tuple[str, str] | None:
    """Lê somente domínio e usuário; nunca consulta CPF ou senha."""
    if getattr(contract, "version", 1) != 2 or not _visible(
        page, contract.login_form_selector
    ):
        return None
    try:
        domain = page.locator(contract.login_domain_selector)
        identity = page.locator(contract.login_identity_selector)
        if domain.count() != 1 or identity.count() != 1:
            return None
        domain_value = domain.input_value(timeout=1_000).strip()
        identity_value = identity.input_value(timeout=1_000).strip()
    except Exception:
        return None
    if not 1 <= len(domain_value) <= 16 or not 1 <= len(identity_value) <= 80:
        return None
    return domain_value, identity_value


def known_authenticated_menu_present(
    page: PageLike, contract: CompanionContract,
) -> bool:
    """Confirma somente URL e marcador estrutural; não autentica isoladamente."""
    return _official_menu_navigation(page.url) and _visible(
        page, contract.authenticated_marker_selector
    )


def validate_authenticated_context(
    page: PageLike,
    contract: CompanionContract,
    *,
    login_url: str,
    expected_identity: str,
    expected_unit: str,
    observed_identity: str | None = None,
    expected_domain: str | None = None,
    observed_domain: str | None = None,
) -> None:
    if not _official_https_navigation(page.url):
        raise CompanionError("O navegador saiu da origem oficial permitida.")
    if not _official_menu_navigation(page.url):
        raise CompanionError("O Menu Principal oficial do SSW não foi confirmado.")
    if _visible(page, contract.login_form_selector):
        raise CompanionError("O formulário de login continua visível.")
    if not _visible(page, contract.authenticated_marker_selector):
        raise CompanionError("Marcador autenticado não encontrado.")
    if contract.version == 2:
        identity = str(observed_identity or "").strip()
        unit = _input_value(page, contract.menu_unit_selector)
        if expected_domain is not None:
            if _normalized(observed_domain) != _normalized(expected_domain):
                raise CompanionError("O domínio autenticado diverge do domínio esperado.")
    else:
        identity = _extract(page, contract.identity)
        unit = _extract(page, contract.unit)
    if not 1 <= len(expected_identity.strip()) <= 80 or not 1 <= len(expected_unit.strip()) <= 16:
        raise CompanionError("Identidade esperada e unidade são obrigatórias.")
    if _normalized(identity) != _normalized(expected_identity):
        raise CompanionError("A identidade exibida diverge do operador esperado.")
    if _normalized(unit) != _normalized(expected_unit):
        raise CompanionError("A unidade exibida diverge da unidade esperada.")


def run_readonly_probe(page: PageLike, contract: CompanionContract) -> None:
    probe = contract.readonly_probe
    if probe is None or probe.authorized is not True:
        raise CompanionError("Nenhuma sondagem read-only aprovada no contrato local.")
    value = _extract(page, EvidenceRule(probe.selector, probe.pattern))
    if not value:
        raise CompanionError("A sondagem read-only não produziu evidência válida.")


class MainFrameOriginMonitor:
    """Detecta, após o commit, saída do frame principal da origem oficial.

    Esta é uma proteção detectiva, não um bloqueio preventivo de rede. O callback
    deve descartar o BrowserContext para invalidar integralmente a sessão.
    """

    def __init__(self, page: PageLike, *, on_violation: Callable[[], None], logger=None):
        self.page = page
        self.on_violation = on_violation
        self.logger = logger
        self.violated = False
        page.on("framenavigated", self._frame_navigated)

    def _mark_violated(self) -> None:
        if self.violated:
            return
        self.violated = True
        if self.logger is not None:
            self.logger.warning("navegacao fase=interrompida categoria=policy_origin")
        try:
            self.on_violation()
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning(
                    "navegacao fase=descarte_falhou categoria=%s", type(exc).__name__,
                )

    def _frame_navigated(self, frame) -> None:
        if frame != self.page.main_frame:
            return
        target = str(getattr(frame, "url", "") or getattr(self.page, "url", ""))
        if not _official_https_navigation(target):
            self._mark_violated()

    def ensure_allowed(self) -> None:
        if not self.violated and not _official_https_navigation(self.page.url):
            self._mark_violated()
        if self.violated:
            raise CompanionError(
                "A página principal saiu da origem oficial; a sessão foi descartada."
            )


def attach_official_navigation_monitor(
    page: PageLike, *, on_violation: Callable[[], None], logger=None,
) -> MainFrameOriginMonitor:
    """Observa apenas commits do frame principal, sem interceptar requisições."""
    return MainFrameOriginMonitor(
        page, on_violation=on_violation, logger=logger,
    )


def ensure_official_main_frame(page: PageLike) -> None:
    """Valida a origem atual sem instalar listeners ou interceptar requisições."""
    if not _official_https_navigation(page.url):
        raise CompanionError("A página principal saiu da origem oficial.")


def run_interactive_homologation(
    *,
    base_url: str,
    contract: CompanionContract,
    expected_identity: str,
    expected_unit: str,
    confirmation: Callable[[], bool],
    execute_probe: bool,
    playwright_factory: Callable,
    logger: logging.Logger,
    expected_domain: str | None = None,
    proxy=None,
    navigation_timeout_ms: int = 60_000,
    human_login_timeout_seconds: int = 900,
) -> dict[str, str]:
    """Executa uma sessão efêmera. Nunca recebe senha ou token do operador."""
    login_url = official_login_url(base_url)
    manager = browser = context = origin_monitor = None

    def discard_context() -> None:
        nonlocal context
        resource, context = context, None
        if resource is not None:
            resource.close()

    logger.info("homologacao_iniciada")
    logger.info(
        "inicializacao fase=proxy_configurado aplicado=%s autenticado=%s",
        proxy is not None, bool(proxy is not None and getattr(proxy, "username", "")),
    )
    try:
        manager = playwright_factory().start()
        logger.info("inicializacao fase=playwright_iniciado")
        from modules.local_agent.ssw_companion_proxy import (
            create_isolated_page, launch_options,
        )
        browser = manager.chromium.launch(**launch_options(proxy))
        logger.info("inicializacao fase=browser_iniciado")
        context, page = create_isolated_page(browser)
        logger.info("inicializacao fase=contexto_criado")
        from modules.local_agent.ssw_companion_proxy import (
            navigate_official_login, wait_for_official_dom,
        )
        try:
            navigate_official_login(
                page, login_url, timeout_ms=navigation_timeout_ms, logger=logger,
            )
        except Exception:
            raise
        ensure_official_main_frame(page)
        wait_for_official_dom(
            page, timeout_ms=navigation_timeout_ms, logger=logger,
        )
        page.wait_for_timeout(10_000)
        ensure_official_main_frame(page)
        logger.info("login_oficial_aberto")
        observed_domain = observed_identity = ""
        if contract.version == 2:
            human_timeout = max(30, min(int(human_login_timeout_seconds), 900))
            logger.info(
                "autenticacao estado=AGUARDANDO_LOGIN_HUMANO timeout_s=%d",
                human_timeout,
            )
            deadline = time.monotonic() + human_timeout
            while time.monotonic() < deadline:
                ensure_official_main_frame(page)
                captured = capture_known_login_identity(page, contract)
                if captured is not None:
                    observed_domain, observed_identity = captured
                if known_authenticated_menu_present(page, contract):
                    validate_authenticated_context(
                        page, contract, login_url=login_url,
                        expected_identity=expected_identity, expected_unit=expected_unit,
                        observed_identity=observed_identity or None,
                        expected_domain=expected_domain,
                        observed_domain=observed_domain or None,
                    )
                    break
                page.wait_for_timeout(500)
            else:
                raise CompanionError(
                    "O prazo do login humano expirou; a sessão foi encerrada "
                    "sem recarregar a página."
                )
        else:
            if not confirmation():
                raise CompanionError("Confirmação explícita do operador não recebida.")
            ensure_official_main_frame(page)
            validate_authenticated_context(
                page, contract, login_url=login_url,
                expected_identity=expected_identity, expected_unit=expected_unit,
            )
        origin_monitor = attach_official_navigation_monitor(
            page, on_violation=discard_context, logger=logger,
        )
        origin_monitor.ensure_allowed()
        logger.info("identidade_autenticada_confirmada")
        probe_status = "nao_solicitada"
        if execute_probe:
            origin_monitor.ensure_allowed()
            run_readonly_probe(page, contract)
            origin_monitor.ensure_allowed()
            probe_status = "confirmada"
            logger.info("sondagem_readonly_confirmada")
        logger.info("homologacao_concluida")
        return {"status": "concluida", "readonly_probe": probe_status}
    except CompanionError:
        logger.warning("homologacao_rejeitada")
        raise
    except Exception as exc:
        if origin_monitor is not None and origin_monitor.violated:
            origin_monitor.ensure_allowed()
        from modules.local_agent.ssw_companion_proxy import classify_browser_failure
        failure = classify_browser_failure(exc)
        logger.error("homologacao_falhou categoria=%s", failure.category)
        raise failure.error from None
    finally:
        for resource, method, event in (
            (context, "close", "falha_ao_descartar_contexto"),
            (browser, "close", "falha_ao_encerrar_browser"),
            (manager, "stop", "falha_ao_encerrar_runtime"),
        ):
            if resource is not None:
                try:
                    getattr(resource, method)()
                except Exception as exc:
                    logger.warning("%s categoria=%s", event, type(exc).__name__)
        logger.info("contexto_descartado")

