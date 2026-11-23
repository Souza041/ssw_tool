"""Configuração efêmera de proxy para os CLIs locais do SSW Companion."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

from modules.local_agent.ssw_companion import CompanionError


@dataclass(frozen=True)
class CompanionProxy:
    server: str
    username: str = ""
    password: str = field(default="", repr=False)

    def playwright_value(self) -> dict[str, str]:
        value = {"server": self.server}
        if self.username:
            value.update({"username": self.username, "password": self.password})
        return value


def _validate_server(value: str) -> str:
    server = value.strip().rstrip("/")
    try:
        parsed = urlsplit(server)
        _ = parsed.port
    except ValueError:
        raise CompanionError("PROXY_SERVER possui formato inválido.") from None
    if (
        parsed.scheme not in {"http", "https", "socks5"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in server)
    ):
        raise CompanionError("PROXY_SERVER possui formato inválido.")
    return server


def load_companion_proxy(env_file: Path) -> CompanionProxy | None:
    """Lê somente o arquivo indicado, sem alterar ou consultar ``os.environ``."""
    path = Path(env_file)
    if not path.is_file():
        return None
    try:
        values = dotenv_values(
            dotenv_path=path,
            encoding="utf-8",
            interpolate=False,
        )
    except (OSError, UnicodeError) as exc:
        raise CompanionError("Não foi possível ler o arquivo de ambiente local.") from exc
    server_raw = str(values.get("PROXY_SERVER") or "").strip()
    username = str(values.get("PROXY_USER") or "").strip()
    password = str(values.get("PROXY_PASS") or "")
    if not server_raw:
        if username or password:
            raise CompanionError("Proxy incompleto: PROXY_SERVER não foi configurado.")
        return None
    if bool(username) != bool(password):
        raise CompanionError("Proxy autenticado exige PROXY_USER e PROXY_PASS.")
    if len(username) > 256 or len(password) > 1024:
        raise CompanionError("Credenciais do proxy excedem o limite permitido.")
    return CompanionProxy(_validate_server(server_raw), username, password)


def launch_options(
    proxy: CompanionProxy | None,
    *,
    devtools: bool = False,
) -> dict[str, object]:
    options: dict[str, object] = {"headless": False}
    if devtools:
        options["args"] = ["--auto-open-devtools-for-tabs"]
    if proxy is not None:
        options["proxy"] = proxy.playwright_value()
    return options


def create_isolated_page(browser):
    """Cria o mesmo BrowserContext efêmero usado pelo diagnóstico e Companion."""
    context = browser.new_context(accept_downloads=False)
    return context, context.new_page()


def goto_commit(page, url: str, *, timeout_ms: int):
    """Primitiva compartilhada de navegação; não lê conteúdo ou estado da sessão."""
    return page.goto(url, wait_until="commit", timeout=timeout_ms)


def wait_domcontentloaded(page, *, timeout_ms: int) -> None:
    """Primitiva compartilhada de espera estrutural sem observar conteúdo."""
    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)


@dataclass(frozen=True)
class BrowserFailure:
    category: str
    error: CompanionError


def classify_browser_failure(exc: BaseException) -> BrowserFailure:
    """Classifica sem devolver a mensagem potencialmente sensível da exceção."""
    message = str(exc).casefold()
    if "407" in message or "proxy_auth" in message or "proxy auth" in message:
        return BrowserFailure(
            "proxy_authentication",
            CompanionError("O proxy recusou a autenticação (HTTP 407)."),
        )
    if any(marker in message for marker in (
        "err_cert_", "certificate", "ssl_error", "tls", "handshake failed",
    )):
        return BrowserFailure(
            "tls",
            CompanionError("Falha TLS ao estabelecer a conexão com o SSW."),
        )
    if any(marker in message for marker in (
        "timeout", "timed out", "timeout 30000ms", "timeout 60000ms",
    )):
        return BrowserFailure(
            "timeout",
            CompanionError("A navegação do SSW excedeu o tempo limite."),
        )
    if any(marker in message for marker in (
        "err_aborted", "navigation interrupted", "navigation canceled",
        "navigation cancelled", "target page, context or browser has been closed",
    )):
        return BrowserFailure(
            "cancelled",
            CompanionError("A navegação do SSW foi cancelada antes da conclusão."),
        )
    if any(marker in message for marker in (
        "err_proxy_connection_failed", "err_tunnel_connection_failed",
        "err_connection_refused", "err_connection_closed", "err_connection_reset",
        "econnrefused", "econnreset", "name_not_resolved", "dns",
    )):
        return BrowserFailure(
            "connectivity",
            CompanionError("Não foi possível conectar ao SSW por meio da rede configurada."),
        )
    return BrowserFailure(
        "unexpected",
        CompanionError("Falha técnica sanitizada ao abrir o navegador do SSW."),
    )


def sanitized_browser_error(exc: BaseException) -> CompanionError:
    return classify_browser_failure(exc).error


def ensure_navigation_response(response) -> None:
    """Inspeciona somente o status numérico; nunca URL, cabeçalhos ou corpo."""
    status = getattr(response, "status", None) if response is not None else None
    if status == 407:
        raise CompanionError("O proxy recusou a autenticação (HTTP 407).")


def navigate_official_login(
    page,
    login_url: str,
    *,
    timeout_ms: int,
    logger,
):
    """Aguarda somente o commit; a interação humana não depende de DOMContentLoaded."""
    logger.info("navegacao fase=iniciada timeout_ms=%d", timeout_ms)
    try:
        response = goto_commit(page, login_url, timeout_ms=timeout_ms)
    except Exception as exc:
        failure = classify_browser_failure(exc)
        logger.warning("navegacao fase=falhou categoria=%s", failure.category)
        raise failure.error from None
    status = getattr(response, "status", None) if response is not None else None
    logger.info(
        "navegacao fase=commit status=%s",
        status if isinstance(status, int) else "indisponivel",
    )
    if isinstance(status, int) and status >= 400 and status != 407:
        logger.warning(
            "navegacao fase=resposta categoria=http_status status=%s", status,
        )
    try:
        ensure_navigation_response(response)
    except CompanionError:
        category = "proxy_authentication" if status == 407 else "http_status"
        logger.warning("navegacao fase=rejeitada categoria=%s status=%s", category, status)
        raise
    return response


def wait_for_official_dom(page, *, timeout_ms: int, logger) -> None:
    """Replica a espera do diagnóstico funcional sem observar conteúdo da página."""
    logger.info("navegacao fase=domcontentloaded_aguardando timeout_ms=%d", timeout_ms)
    try:
        wait_domcontentloaded(page, timeout_ms=timeout_ms)
    except Exception as exc:
        failure = classify_browser_failure(exc)
        logger.warning(
            "navegacao fase=domcontentloaded_falhou categoria=%s", failure.category,
        )
        raise failure.error from None
    logger.info("navegacao fase=domcontentloaded_concluido")

