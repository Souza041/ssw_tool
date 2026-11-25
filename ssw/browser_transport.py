"""Transporte HTTP do SSW mantido dentro de uma sessao Chromium autenticada.

O CAPTCHA e as credenciais sao tratados exclusivamente na pagina oficial. O estado
autenticado nunca sai do ``BrowserContext``. Um worker dedicado e
necessario porque o Playwright sincrono nao pode ser usado pelas varias threads que
executam as operacoes web do Nexus.
"""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
import atexit
import os
from pathlib import Path
import queue
import re
import threading
from typing import Any
from urllib.parse import urlsplit

from dotenv import dotenv_values
import requests
from requests.structures import CaseInsensitiveDict

from ssw.settings import normalizar_base_url


OFFICIAL_SSW_HOSTS = frozenset({"tms.ssw.inf.br", "sistema.ssw.inf.br"})
LOGIN_PATH = "/bin/ssw0422"
MENU_PATH = "/bin/menu01"
LOGIN_FORM_SELECTOR = (
    "#frm:has(input[name='f1']):has(input[name='f2'])"
    ":has(input[name='f3']):has(input[name='f4'])"
)
MENU_MARKER_SELECTOR = 'input[name="f3"][maxlength="3"]'
MENU_UNIT_SELECTOR = "input[name='f2']"

_CAPTCHA_MARKERS = (
    re.compile(rb"(?:google\.com|recaptcha\.net)/recaptcha/", re.I),
    re.compile(rb"\bg-recaptcha(?:-response)?\b", re.I),
    re.compile(rb"\bdata-sitekey\s*=", re.I),
)
_BIN_PATH = re.compile(r"^/bin/[A-Za-z0-9]+$")


class SSWBrowserAuthenticationError(RuntimeError):
    """A sessao humana nao foi confirmada ou deixou de ser valida."""


class SSWBrowserTransportError(RuntimeError):
    """Falha sanitizada no transporte; nao inclui payload nem conteudo da pagina."""


class SSWUncertainResultError(SSWBrowserTransportError):
    """A requisicao pode ter chegado ao SSW e nao pode ser repetida automaticamente."""


class SSWSessionBusyError(RuntimeError):
    """Outra automacao detem atomicamente o contexto operacional."""


class _OperationLease:
    def __init__(self, coordinator: "_OperationCoordinator", token: object) -> None:
        self._coordinator = coordinator
        self._token = token
        self._released = False

    @property
    def resource_id(self) -> int:
        return id(self._coordinator)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._coordinator.release(self._token)


class _OperationCoordinator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: object | None = None
        self._automation = ""

    def acquire(self, automation: str) -> _OperationLease:
        name = str(automation or "Automacao SSW").strip()[:120] or "Automacao SSW"
        token = object()
        with self._lock:
            if self._owner is not None:
                current = self._automation or "outra automacao SSW"
                raise SSWSessionBusyError(
                    f"A automacao '{current}' esta em andamento nesta sessao SSW. "
                    "Aguarde a conclusao antes de iniciar outra operacao."
                )
            self._owner = token
            self._automation = name
        return _OperationLease(self, token)

    def release(self, token: object) -> None:
        with self._lock:
            if self._owner is token:
                self._owner = None
                self._automation = ""

    def current(self) -> str:
        with self._lock:
            return self._automation if self._owner is not None else ""


@dataclass(frozen=True)
class BrowserIdentity:
    domain: str
    user: str
    session_binding: str = field(default="local", repr=False)

    @classmethod
    def checked(
        cls, domain: str, user: str, session_binding: str | None = None,
    ) -> "BrowserIdentity":
        values = tuple(str(value or "").strip().upper() for value in (domain, user))
        if not all(values):
            raise SSWBrowserAuthenticationError("Dominio e usuario esperados sao obrigatorios.")
        if len(values[0]) > 16 or len(values[1]) > 80:
            raise SSWBrowserAuthenticationError("Identidade SSW fora do limite permitido.")
        binding = str(session_binding or "local").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", binding):
            raise SSWBrowserAuthenticationError("Vinculo da sessao SSW invalido.")
        return cls(*values, binding)


def _checked_requested_unit(unit: str) -> str:
    value = str(unit or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{3,16}", value):
        raise SSWBrowserAuthenticationError("Unidade operacional solicitada invalida.")
    return value


def _load_playwright_proxy(env_file: Path | None = None) -> dict[str, str] | None:
    """Recupera a configuracao homologada sem registrar valores sensiveis."""
    if env_file is None and any(
        name in os.environ for name in ("PROXY_SERVER", "PROXY_USER", "PROXY_PASS")
    ):
        values = {name: os.getenv(name, "") for name in ("PROXY_SERVER", "PROXY_USER", "PROXY_PASS")}
    else:
        path = Path(env_file or Path(__file__).resolve().parents[1] / ".env")
        if not path.is_file():
            return None
        try:
            values = dotenv_values(path, encoding="utf-8", interpolate=False)
        except (OSError, UnicodeError) as exc:
            raise SSWBrowserAuthenticationError(
                "Nao foi possivel ler a configuracao local do proxy."
            ) from exc
    server = str(values.get("PROXY_SERVER") or "").strip().rstrip("/")
    username = str(values.get("PROXY_USER") or "").strip()
    password = str(values.get("PROXY_PASS") or "")
    if not server:
        if username or password:
            raise SSWBrowserAuthenticationError("Configuracao de proxy incompleta.")
        return None
    try:
        parsed = urlsplit(server)
        _ = parsed.port
    except ValueError:
        raise SSWBrowserAuthenticationError("Configuracao de proxy invalida.") from None
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
        raise SSWBrowserAuthenticationError("Configuracao de proxy invalida.")
    if bool(username) != bool(password):
        raise SSWBrowserAuthenticationError("Configuracao de proxy incompleta.")
    if len(username) > 256 or len(password) > 1024:
        raise SSWBrowserAuthenticationError("Credenciais do proxy fora do limite permitido.")
    proxy = {"server": server}
    if username:
        proxy.update({"username": username, "password": password})
    return proxy


def _browser_launch_options(proxy: dict[str, str] | None) -> dict[str, Any]:
    options: dict[str, Any] = {"headless": False}
    if proxy is not None:
        options["proxy"] = proxy
    return options


def _official_origin(base_url: str) -> str:
    normalized = normalizar_base_url(base_url)
    parsed = urlsplit(normalized)
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in OFFICIAL_SSW_HOSTS:
        raise SSWBrowserAuthenticationError(
            "A autenticacao humana aceita somente a origem HTTPS oficial do SSW."
        )
    return normalized


def _same_origin(url: str, base_url: str) -> bool:
    target, base = urlsplit(url), urlsplit(base_url)
    return (
        target.scheme == base.scheme == "https"
        and target.hostname == base.hostname
        and target.port == base.port
        and target.username is None
        and target.password is None
    )


def _captcha_present(content: bytes) -> bool:
    sample = bytes(content or b"")[:1_000_000]
    return any(marker.search(sample) for marker in _CAPTCHA_MARKERS)


class _BrowserWorker:
    def __init__(
        self,
        *,
        identity: BrowserIdentity,
        base_url: str,
        timeout_seconds: int,
    ) -> None:
        self.identity = identity
        self.base_url = _official_origin(base_url)
        self.timeout_seconds = max(30, min(int(timeout_seconds), 900))
        self.proxy = _load_playwright_proxy()
        self.observed_unit = ""
        self.window_minimized = False
        self.operations = _OperationCoordinator()
        self._commands: queue.Queue[tuple[str, Any, Future] | None] = queue.Queue()
        self._ready: Future = Future()
        self._authenticated = False
        self._thread = threading.Thread(
            target=self._run,
            name=f"ssw-browser-{identity.user}",
            daemon=True,
        )
        self._thread.start()

    def wait_ready(self) -> None:
        self._ready.result(timeout=self.timeout_seconds + 90)

    @property
    def valid(self) -> bool:
        return self._authenticated and self._thread.is_alive()

    def call(self, command: str, value: Any = None, *, timeout: float | None = None):
        if not self.valid:
            raise SSWBrowserAuthenticationError(
                "Sessao SSW ausente ou expirada; nova autenticacao humana e necessaria."
            )
        future: Future = Future()
        self._commands.put((command, value, future))
        return future.result(timeout=timeout)

    def close(self) -> None:
        if self._thread.is_alive():
            future: Future = Future()
            self._commands.put(("close", None, future))
            try:
                future.result(timeout=15)
            except Exception:
                pass
        self._authenticated = False

    def _run(self) -> None:
        manager = browser = context = page = None
        try:
            from playwright.sync_api import sync_playwright

            manager = sync_playwright().start()
            browser = manager.chromium.launch(**_browser_launch_options(self.proxy))
            browser.on("disconnected", self._browser_disconnected)
            context = browser.new_context(accept_downloads=False)
            page = context.new_page()
            page.goto(self.base_url + LOGIN_PATH, wait_until="commit", timeout=60_000)
            self._await_human_login(page)
            self.window_minimized = self._minimize_window(context, page)
            self._authenticated = True
            self._ready.set_result(True)

            while True:
                item = self._commands.get()
                if item is None:
                    break
                command, value, future = item
                if command == "close":
                    future.set_result(None)
                    break
                try:
                    if command == "request":
                        future.set_result(self._request(context, value))
                    elif command == "probe":
                        future.set_result(self._probe(context))
                    else:
                        raise SSWBrowserTransportError("Comando de transporte desconhecido.")
                except BaseException as exc:
                    future.set_exception(exc)
        except BaseException as exc:
            self._authenticated = False
            if not self._ready.done():
                if isinstance(exc, SSWBrowserAuthenticationError):
                    self._ready.set_exception(exc)
                else:
                    self._ready.set_exception(
                        SSWBrowserAuthenticationError(
                            "Nao foi possivel iniciar ou confirmar o Chromium do SSW."
                        )
                    )
        finally:
            self._authenticated = False
            for resource, method in ((context, "close"), (browser, "close"), (manager, "stop")):
                if resource is not None:
                    try:
                        getattr(resource, method)()
                    except Exception:
                        pass

    def _browser_disconnected(self, *_args) -> None:
        self._authenticated = False

    @staticmethod
    def _minimize_window(context, page) -> bool:
        session = None
        try:
            session = context.new_cdp_session(page)
            window = session.send("Browser.getWindowForTarget")
            window_id = window.get("windowId") if isinstance(window, dict) else None
            if not isinstance(window_id, int):
                return False
            session.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "minimized"}},
            )
            return True
        except Exception:
            return False
        finally:
            if session is not None:
                try:
                    session.detach()
                except Exception:
                    pass

    def _await_human_login(self, page) -> None:
        deadline_ms = self.timeout_seconds * 1000
        elapsed = 0
        observed_domain = observed_user = ""
        while elapsed < deadline_ms:
            if not _same_origin(page.url, self.base_url):
                raise SSWBrowserAuthenticationError(
                    "O Chromium saiu da origem oficial; a sessao foi descartada."
                )
            path = urlsplit(page.url).path.rstrip("/")
            if path == LOGIN_PATH:
                form = page.locator(LOGIN_FORM_SELECTOR)
                if form.count() == 1:
                    domain = page.locator("input[name='f1']")
                    user = page.locator("input[name='f3']")
                    if domain.count() == user.count() == 1:
                        observed_domain = domain.input_value(timeout=1_000).strip().upper()
                        observed_user = user.input_value(timeout=1_000).strip().upper()
            if path == MENU_PATH:
                marker = page.locator(MENU_MARKER_SELECTOR)
                unit = page.locator(MENU_UNIT_SELECTOR)
                if marker.count() == unit.count() == 1 and marker.is_visible() and unit.is_visible():
                    observed_unit = unit.input_value(timeout=5_000).strip().upper()
                    self._confirm_authenticated_identity(
                        observed_domain, observed_user, observed_unit,
                    )
                    return
            page.wait_for_timeout(250)
            elapsed += 250
        raise SSWBrowserAuthenticationError(
            "O prazo do login humano expirou; conclua credenciais e CAPTCHA no Chromium."
        )

    def _confirm_authenticated_identity(
        self, observed_domain: str, observed_user: str, observed_unit: str,
    ) -> None:
        observed_domain = str(observed_domain or "").strip().upper()
        observed_user = str(observed_user or "").strip().upper()
        observed_unit = str(observed_unit or "").strip().upper()
        if not observed_domain:
            raise SSWBrowserAuthenticationError("Dominio autenticado nao confirmado.")
        if observed_domain != self.identity.domain:
            raise SSWBrowserAuthenticationError("Dominio autenticado divergente.")
        if not observed_user:
            raise SSWBrowserAuthenticationError("Usuario autenticado nao confirmado.")
        if observed_user != self.identity.user:
            raise SSWBrowserAuthenticationError("Usuario autenticado divergente.")
        if not re.fullmatch(r"[A-Z0-9]{3,16}", observed_unit):
            raise SSWBrowserAuthenticationError("Unidade inicial observada invalida.")
        self.observed_unit = observed_unit

    def _probe(self, context) -> bool:
        result = self._request(context, {
            "method": "POST",
            "url": self.base_url + MENU_PATH,
            "data": {"act": ""},
            "params": None,
            "headers": {},
            "timeout": 60,
            "allow_redirects": True,
        })
        return result[0] == 200

    def _request(self, context, request_data):
        method = str(request_data["method"]).upper()
        url = str(request_data["url"])
        parsed = urlsplit(url)
        if method not in {"GET", "POST"} or not _same_origin(url, self.base_url):
            raise SSWBrowserTransportError("Metodo ou origem fora do contrato SSW.")
        if not _BIN_PATH.fullmatch(parsed.path) or parsed.fragment:
            raise SSWBrowserTransportError("Caminho fora do contrato SSW.")
        if parsed.path == LOGIN_PATH:
            raise SSWBrowserTransportError("Login automatizado e proibido no transporte Chromium.")

        timeout = request_data.get("timeout", 60)
        if isinstance(timeout, (tuple, list)):
            timeout = timeout[-1]
        timeout_ms = max(1_000, min(int(float(timeout) * 1000), 900_000))
        headers = {
            str(key): str(value)
            for key, value in dict(request_data.get("headers") or {}).items()
            if str(key).casefold() not in {"cookie", "host", "content-length"}
        }
        kwargs: dict[str, Any] = {
            "method": method,
            "timeout": timeout_ms,
            "max_redirects": 20 if request_data.get("allow_redirects", True) else 0,
            "headers": headers,
        }
        values = request_data.get("params") if method == "GET" else request_data.get("data")
        kwargs["params" if method == "GET" else "form"] = values or {}
        try:
            raw = context.request.fetch(url, **kwargs)
        except Exception as exc:
            message = str(exc).casefold()
            if any(marker in message for marker in (
                "target page, context or browser has been closed",
                "browser has been closed", "context has been closed",
                "target closed", "connection closed",
            )):
                self._authenticated = False
                raise SSWBrowserAuthenticationError(
                    "O navegador SSW foi encerrado; realize nova autenticacao humana."
                ) from None
            raise SSWUncertainResultError(
                "Falha no transporte SSW; o resultado da requisicao pode ser incerto."
            ) from None
        try:
            content = raw.body()
            final_url = raw.url
            if not _same_origin(final_url, self.base_url):
                self._authenticated = False
                raise SSWBrowserAuthenticationError("Resposta fora da origem oficial do SSW.")
            final_path = urlsplit(final_url).path.rstrip("/")
            if final_path == LOGIN_PATH or _captcha_present(content):
                self._authenticated = False
                raise SSWBrowserAuthenticationError(
                    "Sessao expirada ou CAPTCHA solicitado; nova autenticacao humana e necessaria."
                )
            return raw.status, final_url, dict(raw.headers), content
        finally:
            raw.dispose()


class BrowserSessionTransport:
    """Interface HTTP usada de forma transparente pelos consumidores existentes."""

    def __init__(self, worker: _BrowserWorker) -> None:
        self._worker = worker
        self.headers = CaseInsensitiveDict({
            "Origin": worker.base_url,
            "Referer": worker.base_url + MENU_PATH,
        })

    @property
    def identity(self) -> BrowserIdentity:
        return self._worker.identity

    @property
    def observed_unit(self) -> str:
        return self._worker.observed_unit

    @property
    def valid(self) -> bool:
        return self._worker.valid

    @property
    def current_operation(self) -> str:
        return self._worker.operations.current()

    def acquire_operation(self, automation: str) -> _OperationLease:
        if not self.valid:
            raise SSWBrowserAuthenticationError(
                "A sessao SSW nao esta disponivel; realize nova autenticacao humana."
            )
        return self._worker.operations.acquire(automation)

    def session_status(self) -> dict[str, str]:
        operation = self.current_operation
        if not self.valid:
            return {
                "state": "reauthentication_required",
                "message": "Sessao SSW indisponivel. Realize nova autenticacao humana.",
                "operation": "",
                "observed_unit": self.observed_unit,
            }
        if operation:
            return {
                "state": "busy",
                "message": f"Automacao em andamento: {operation}.",
                "operation": operation,
                "observed_unit": self.observed_unit,
            }
        return {
            "state": "authenticated",
            "message": "Sessao SSW autenticada.",
            "operation": "",
            "observed_unit": self.observed_unit,
        }

    def probe(self) -> bool:
        return bool(self._worker.call("probe", timeout=70))

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        timeout = kwargs.get("timeout", 60)
        if isinstance(timeout, (tuple, list)):
            wait_timeout = float(timeout[-1]) + 70
        else:
            wait_timeout = float(timeout or 900) + 70
        status, final_url, headers, content = self._worker.call(
            "request",
            {
                "method": method,
                "url": url,
                "data": kwargs.get("data"),
                "params": kwargs.get("params"),
                "headers": {**self.headers, **dict(kwargs.get("headers") or {})},
                "timeout": timeout,
                "allow_redirects": kwargs.get("allow_redirects", True),
            },
            timeout=wait_timeout,
        )
        response = requests.Response()
        response.status_code = status
        response.url = final_url
        response.headers.update(headers)
        response._content = content
        response.encoding = requests.utils.get_encoding_from_headers(response.headers) or "latin1"
        response.request = requests.Request(method=method, url=url).prepare()
        return response

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        # O ciclo da sessao autenticada e maior que o de um consumidor/relatorio.
        pass

    def shutdown(self) -> None:
        self._worker.close()


_registry: dict[BrowserIdentity, BrowserSessionTransport] = {}
_registry_lock = threading.RLock()


def browser_session_snapshot() -> list[dict[str, str]]:
    """Publica somente estado e vínculo opaco; nunca cookies ou credenciais."""
    with _registry_lock:
        transports = list(_registry.values())
    result: list[dict[str, str]] = []
    for transport in transports:
        binding = transport.identity.session_binding
        if len(binding) < 32:
            continue
        status = transport.session_status()
        result.append({
            "session_binding": binding,
            "state": str(status.get("state") or "reauthentication_required"),
            "operation": str(status.get("operation") or "")[:120],
            "observed_unit": str(status.get("observed_unit") or "")[:16],
        })
    return result


def authenticated_browser_transport(
    *, domain: str, user: str, unit: str, base_url: str, timeout_seconds: int = 900,
    session_binding: str | None = None,
) -> BrowserSessionTransport:
    """Reutiliza somente uma sessao do mesmo usuario e vinculo remoto/local."""
    identity = BrowserIdentity.checked(domain, user, session_binding)
    _checked_requested_unit(unit)
    with _registry_lock:
        current = _registry.get(identity)
        if current is not None:
            if current.valid:
                lease = None
                try:
                    lease = current.acquire_operation("Validacao da sessao SSW")
                    current.probe()
                    return current
                except SSWSessionBusyError:
                    raise
                except Exception:
                    pass
                finally:
                    if lease is not None:
                        lease.release()
            current.shutdown()
            _registry.pop(identity, None)
        worker = _BrowserWorker(
            identity=identity,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
        try:
            worker.wait_ready()
        except BaseException:
            worker.close()
            raise
        transport = BrowserSessionTransport(worker)
        _registry[identity] = transport
        return transport


def discard_browser_transport(transport: BrowserSessionTransport) -> None:
    with _registry_lock:
        if _registry.get(transport.identity) is transport:
            _registry.pop(transport.identity, None)
        transport.shutdown()


def discard_browser_session(*, domain: str, user: str, session_binding: str) -> bool:
    identity = BrowserIdentity.checked(domain, user, session_binding)
    with _registry_lock:
        transport = _registry.pop(identity, None)
    if transport is None:
        return False
    transport.shutdown()
    return True


def discard_all_browser_transports() -> None:
    """Encerra os Chromium locais sem publicar qualquer estado de autenticacao."""
    with _registry_lock:
        transports = list(_registry.values())
        _registry.clear()
    for transport in transports:
        transport.shutdown()


atexit.register(discard_all_browser_transports)
