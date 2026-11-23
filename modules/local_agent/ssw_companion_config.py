"""Configurador local do contrato estrutural conhecido do SSW Companion.

O configurador não recebe seletores do operador. Ele valida somente a estrutura
aprovada, não lê CPF, senha, HTML, cookies ou tokens e não automatiza o CAPTCHA.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Callable
from urllib.parse import urlsplit

from modules.local_agent.ssw_companion import (
    CompanionContract,
    CompanionError,
    KNOWN_LOGIN_CPF_SELECTOR,
    KNOWN_LOGIN_DOMAIN_SELECTOR,
    KNOWN_LOGIN_FORM_SELECTOR,
    KNOWN_LOGIN_IDENTITY_SELECTOR,
    KNOWN_LOGIN_PASSWORD_SELECTOR,
    KNOWN_MENU_MARKER_SELECTOR,
    KNOWN_MENU_UNIT_SELECTOR,
    MENU_PATH,
    attach_official_navigation_monitor,
    capture_known_login_identity,
    ensure_official_main_frame,
    official_login_url,
    validate_authenticated_context,
)


ILLUSTRATIVE_MARKERS = (
    "seletor_aprovado", "seletor_do_", "seletor_aqui", "placeholder",
    "exemplo", "example", "todo", "preencher",
)
LOGIN_FIELD_SELECTORS = tuple(f"input[name='f{index}']" for index in range(1, 5))
AUTHENTICATED_MENU_PATH = "/bin/menu01"
CAPTCHA_STRUCTURE_SELECTOR = (
    "iframe[src*='captcha' i], .g-recaptcha, [class*='captcha' i], [id*='captcha' i]"
)


def validate_manual_selector(page, selector: str, *, field: str) -> str:
    value = str(selector or "").strip()
    folded = value.casefold()
    if not value or len(value) > 512 or "\x00" in value:
        raise CompanionError(f"Seletor {field} vazio ou fora do limite.")
    if any(marker in folded for marker in ILLUSTRATIVE_MARKERS):
        raise CompanionError(f"Seletor {field} ainda é ilustrativo.")
    try:
        locator = page.locator(value)
        count = locator.count()
        visible = count == 1 and locator.is_visible()
    except Exception as exc:
        raise CompanionError(f"Sintaxe inválida no seletor {field}.") from exc
    if count != 1:
        raise CompanionError(f"Seletor {field} deve localizar exatamente um elemento.")
    if not visible:
        raise CompanionError(f"Elemento do seletor {field} não está visível.")
    return value


def qualify_login_form_selector(page, selector: str) -> str:
    """Transforma um contêiner genérico em evidência exclusiva do login SSW."""
    container = validate_manual_selector(page, selector, field="login_form")
    suffix = "".join(f":has({field})" for field in LOGIN_FIELD_SELECTORS)
    qualified = f":is({container}){suffix}"
    if len(qualified) > 512:
        raise CompanionError("Seletor qualificado do login excede o limite permitido.")
    try:
        locator = page.locator(qualified)
        count = locator.count()
    except Exception as exc:
        raise CompanionError("Não foi possível qualificar o formulário de login.") from exc
    if count != 1:
        raise CompanionError(
            "O contêiner informado não possui os campos específicos f1, f2, f3 e f4."
        )
    return qualified


def validate_authenticated_menu_state(page, login_form_selector: str) -> None:
    """Exige sinais combinados de menu; confirmação textual isolada não basta."""
    ensure_official_main_frame(page)
    if urlsplit(page.url).path.rstrip("/") != AUTHENTICATED_MENU_PATH:
        raise CompanionError("O navegador ainda não está no Menu Principal oficial do SSW.")
    try:
        login = page.locator(login_form_selector)
        login_visible = login.count() > 0 and login.is_visible()
    except Exception as exc:
        raise CompanionError("Não foi possível validar a transição para o menu.") from exc
    if login_visible:
        raise CompanionError("A estrutura específica do formulário de login continua visível.")


def automatic_contract_data() -> dict[str, object]:
    return {
        "version": 2,
        "login": {
            "form_selector": KNOWN_LOGIN_FORM_SELECTOR,
            "domain_selector": KNOWN_LOGIN_DOMAIN_SELECTOR,
            "cpf_selector": KNOWN_LOGIN_CPF_SELECTOR,
            "identity_selector": KNOWN_LOGIN_IDENTITY_SELECTOR,
            "password_selector": KNOWN_LOGIN_PASSWORD_SELECTOR,
        },
        "menu": {
            "path": MENU_PATH,
            "marker_selector": KNOWN_MENU_MARKER_SELECTOR,
            "unit_selector": KNOWN_MENU_UNIT_SELECTOR,
        },
    }


def _present(page, selector: str, *, visible: bool = False) -> bool:
    try:
        locator = page.locator(selector)
        return locator.count() == 1 and (not visible or locator.is_visible())
    except Exception:
        return False


def _any_present(page, selector: str) -> bool:
    try:
        return page.locator(selector).count() > 0
    except Exception:
        return False


def _known_login_ready(page) -> bool:
    return _present(page, KNOWN_LOGIN_FORM_SELECTOR, visible=True) and all(
        _present(page, selector)
        for selector in (
            KNOWN_LOGIN_DOMAIN_SELECTOR, KNOWN_LOGIN_CPF_SELECTOR,
            KNOWN_LOGIN_IDENTITY_SELECTOR, KNOWN_LOGIN_PASSWORD_SELECTOR,
        )
    )


def _login_or_challenge_started(page) -> bool:
    """Trava definitivamente a recuperação assim que a interação puder começar."""
    return any(
        _any_present(page, selector)
        for selector in (
            "#frm",
            KNOWN_LOGIN_DOMAIN_SELECTOR,
            KNOWN_LOGIN_CPF_SELECTOR,
            KNOWN_LOGIN_IDENTITY_SELECTOR,
            KNOWN_LOGIN_PASSWORD_SELECTOR,
            CAPTCHA_STRUCTURE_SELECTOR,
        )
    )


def _known_menu_ready(page) -> bool:
    return (
        urlsplit(page.url).path.rstrip("/") == MENU_PATH
        and _present(page, KNOWN_MENU_MARKER_SELECTOR, visible=True)
        and _present(page, KNOWN_MENU_UNIT_SELECTOR, visible=True)
    )


def wait_for_known_login(page, *, timeout_seconds: int = 60, reloads: int = 2) -> str:
    """Recupera somente a fase inicial; interação detectada trava navegações."""
    attempts = max(1, timeout_seconds * 2)
    reload_points = {
        max(1, attempts * index // (reloads + 1))
        for index in range(1, reloads + 1)
    }
    performed = 0
    recovery_locked = False
    for attempt in range(attempts):
        ensure_official_main_frame(page)
        if _known_menu_ready(page):
            return "menu"
        if _known_login_ready(page):
            return "login"
        recovery_locked = recovery_locked or _login_or_challenge_started(page)
        if attempt in reload_points and performed < reloads and not recovery_locked:
            performed += 1
            try:
                page.reload(wait_until="domcontentloaded", timeout=30_000)
            except Exception:
                pass
        page.wait_for_timeout(500)
    raise CompanionError("O formulário conhecido do SSW não ficou disponível no prazo.")


def wait_for_authenticated_menu(
    page,
    *,
    contract: CompanionContract,
    expected_domain: str,
    expected_identity: str,
    expected_unit: str,
    login_url: str,
    timeout_seconds: int = 900,
) -> None:
    """Espera passivamente: esta fase não navega, recarrega ou recria contexto."""
    timeout_seconds = max(30, min(int(timeout_seconds), 900))
    observed_domain = observed_identity = ""
    for _attempt in range(max(1, timeout_seconds * 2)):
        ensure_official_main_frame(page)
        captured = capture_known_login_identity(page, contract)
        if captured is not None:
            observed_domain, observed_identity = captured
        if _known_menu_ready(page):
            validate_authenticated_menu_state(page, KNOWN_LOGIN_FORM_SELECTOR)
            validate_authenticated_context(
                page,
                contract,
                login_url=login_url,
                expected_identity=expected_identity,
                expected_unit=expected_unit,
                observed_identity=observed_identity or None,
                expected_domain=expected_domain,
                observed_domain=observed_domain or None,
            )
            return
        page.wait_for_timeout(500)
    raise CompanionError(
        "O prazo do login humano expirou; a sessão foi encerrada sem recarregar a página."
    )


def validate_manual_pattern(pattern: str, *, field: str) -> str:
    value = str(pattern or "").strip()
    if not value or len(value) > 512 or "\x00" in value:
        raise CompanionError(f"Expressão {field} vazia ou fora do limite.")
    if any(marker in value.casefold() for marker in ILLUSTRATIVE_MARKERS):
        raise CompanionError(f"Expressão {field} ainda é ilustrativa.")
    try:
        compiled = re.compile(value)
    except re.error as exc:
        raise CompanionError(f"Sintaxe inválida na expressão {field}.") from exc
    if set(compiled.groupindex) != {"value"}:
        raise CompanionError(
            f"Expressão {field} deve possuir somente o grupo nomeado value."
        )
    return value


def _require_distinct(selectors: dict[str, str]) -> None:
    folded = [value.casefold() for value in selectors.values()]
    if len(folded) != len(set(folded)):
        raise CompanionError("Cada evidência deve utilizar um seletor distinto.")


def build_contract_data(
    *,
    login_form: str,
    authenticated_marker: str,
    identity_selector: str,
    identity_pattern: str,
    unit_selector: str,
    unit_pattern: str,
    readonly_selector: str | None = None,
    readonly_pattern: str | None = None,
) -> dict[str, object]:
    selectors = {
        "login_form": login_form,
        "authenticated_marker": authenticated_marker,
        "identity": identity_selector,
        "unit": unit_selector,
    }
    if readonly_selector:
        selectors["readonly_probe"] = readonly_selector
    _require_distinct(selectors)
    data: dict[str, object] = {
        "authenticated_marker_selector": authenticated_marker,
        "login_form_selector": login_form,
        "identity": {"selector": identity_selector, "pattern": identity_pattern},
        "unit": {"selector": unit_selector, "pattern": unit_pattern},
    }
    if bool(readonly_selector) != bool(readonly_pattern):
        raise CompanionError("Sondagem opcional exige seletor e expressão.")
    if readonly_selector and readonly_pattern:
        data["readonly_probe"] = {
            "selector": readonly_selector,
            "pattern": readonly_pattern,
            "authorized": True,
        }
    return data


def _current_windows_sid() -> str:
    try:
        result = subprocess.run(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"],
            check=True, capture_output=True, text=True,
            timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        row = next(csv.reader(io.StringIO(result.stdout)))
        sid = row[1].strip() if len(row) >= 2 else ""
    except (OSError, subprocess.SubprocessError, StopIteration, csv.Error) as exc:
        raise CompanionError("Não foi possível identificar o usuário Windows atual.") from exc
    if not re.fullmatch(r"S-1-(?:\d+-)+\d+", sid, re.IGNORECASE):
        raise CompanionError("Identificador do usuário Windows inválido.")
    return sid


def _restrict_permissions(path: Path) -> None:
    if os.name != "nt":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return
    sid = _current_windows_sid()
    try:
        subprocess.run(
            [
                "icacls.exe", str(path), "/inheritance:r",
                "/grant:r", f"*{sid}:(R,W)",
            ],
            check=True, capture_output=True, text=True,
            timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CompanionError("Não foi possível restringir as permissões do contrato.") from exc


def secure_write_contract(
    path: Path,
    data: dict[str, object],
    *,
    confirm_overwrite: Callable[[], bool],
) -> None:
    CompanionContract.from_mapping(data)
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not confirm_overwrite():
        raise CompanionError("Contrato existente preservado pelo operador.")
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _restrict_permissions(temp_path)
        temp_path.replace(path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _prompt_validated(
    prompt: str,
    validator: Callable[[str], str],
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> str:
    while True:
        value = input_fn(prompt)
        try:
            return validator(value)
        except CompanionError as exc:
            output_fn(str(exc))


def run_configuration_wizard(
    *,
    base_url: str,
    output_path: Path,
    playwright_factory: Callable,
    expected_domain: str,
    expected_identity: str,
    expected_unit: str,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    proxy=None,
    logger: logging.Logger | None = None,
    navigation_timeout_ms: int = 60_000,
    human_login_timeout_seconds: int = 900,
) -> Path:
    login_url = official_login_url(base_url)
    logger = logger or logging.Logger("nexus.ssw_companion.config.null")
    logger.propagate = False
    manager = browser = context = page = origin_monitor = None
    raw_input_fn = input_fn

    def discard_context() -> None:
        nonlocal context
        resource, context = context, None
        if resource is not None:
            resource.close()

    def monitored_input(prompt: str) -> str:
        if page is not None:
            ensure_official_main_frame(page)
        if origin_monitor is not None:
            origin_monitor.ensure_allowed()
        value = raw_input_fn(prompt)
        if page is not None:
            ensure_official_main_frame(page)
        if origin_monitor is not None:
            origin_monitor.ensure_allowed()
        return value

    output_fn(
        "O Chromium será aberto com a mesma inicialização do diagnóstico. "
        "Abra o DevTools manualmente com F12 após o formulário aparecer."
    )
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
        ensure_official_main_frame(page)
        logger.info("navegacao fase=estabilizacao_iniciada timeout_ms=%d", 10_000)
        page.wait_for_timeout(10_000)
        ensure_official_main_frame(page)
        logger.info("navegacao fase=estabilizacao_concluida")
        input_fn = monitored_input
        contract = CompanionContract.from_mapping(automatic_contract_data())
        state = wait_for_known_login(page)
        if state == "login":
            logger.info(
                "autenticacao estado=AGUARDANDO_LOGIN_HUMANO timeout_s=%d",
                max(30, min(int(human_login_timeout_seconds), 900)),
            )
            output_fn(
                "Formulário oficial reconhecido. Digite as credenciais no navegador, "
                "resolva manualmente o reCAPTCHA e conclua o login."
            )
            wait_for_authenticated_menu(
                page,
                contract=contract,
                expected_domain=expected_domain,
                expected_identity=expected_identity,
                expected_unit=expected_unit,
                login_url=login_url,
                timeout_seconds=human_login_timeout_seconds,
            )
        else:
            raise CompanionError(
                "O contexto temporário iniciou autenticado sem vínculo com o operador."
            )
        output_fn("Menu Principal reconhecido automaticamente.")
        origin_monitor = attach_official_navigation_monitor(
            page, on_violation=discard_context, logger=logger,
        )
        origin_monitor.ensure_allowed()
        data = automatic_contract_data()

        secure_write_contract(
            output_path, data,
            confirm_overwrite=lambda: input_fn(
                "O contrato já existe. Digite SOBRESCREVER para substituí-lo: "
            ).strip() == "SOBRESCREVER",
        )
        output_fn("Contrato validado e gravado com permissões restritas.")
        return output_path
    except CompanionError:
        raise
    except Exception as exc:
        if origin_monitor is not None and origin_monitor.violated:
            origin_monitor.ensure_allowed()
        from modules.local_agent.ssw_companion_proxy import classify_browser_failure
        failure = classify_browser_failure(exc)
        logger.error("configuracao_falhou categoria=%s", failure.category)
        raise failure.error from None
    finally:
        for resource, method in (
            (context, "close"), (browser, "close"), (manager, "stop"),
        ):
            if resource is not None:
                try:
                    getattr(resource, method)()
                except Exception:
                    pass

