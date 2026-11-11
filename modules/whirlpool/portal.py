import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urljoin, urlparse

from modules.whirlpool.config import WhirlpoolPortalSettings


MENU_CONSULTAS = "#subTabIndex4"
MENU_CARREGAMENTOS = "xpath=//*[@id='L2N0']/span[2]"
MENU_LOGISTICA_REVERSA = "xpath=//*[@id='0L3N2']/span[2]"
CAMPO_TRANSPORTE = (
    "#aaaa\\.PublishShipmentsView\\.Table\\:2147483641",
    "input[id*='PublishShipmentsView.Table']",
    "input[name*='PublishShipmentsView.Table']",
    "input.lsField__input",
    "xpath=//input[@type='text']",
)
LINK_DOCUMENTOS = (
    ("pre_calculo", "#aaaa\\.PublishShipmentsView\\.LinkToAction4\\.0", "PreCalculoDevolucao"),
    ("declaracao", "#aaaa\\.PublishShipmentsView\\.LinkToAction5\\.0", "DeclaracaoDevolucao"),
)


class PortalWhirlpoolErro(RuntimeError):
    pass


class PortalWhirlpoolDesabilitado(PortalWhirlpoolErro):
    pass


class PortalAutenticacaoRecusada(PortalWhirlpoolErro):
    pass


class PortalTimeout(PortalWhirlpoolErro):
    pass


class TransporteNaoEncontrado(PortalWhirlpoolErro):
    pass


class DocumentoIndisponivel(PortalWhirlpoolErro):
    pass


class DownloadPortalFalhou(PortalWhirlpoolErro):
    pass


class ConcorrenciaPortalBloqueada(PortalWhirlpoolErro):
    pass


@dataclass(frozen=True)
class TransportePortal:
    planta: str
    transporte: str

    @property
    def chave(self) -> str:
        return f"{self.planta.strip().casefold()}:{self.transporte.strip().casefold()}"


@dataclass
class ResultadoTransportePortal:
    planta: str
    transporte: str
    status: str
    documentos: list[Path] = field(default_factory=list)
    erro: str | None = None


def _safe_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
    normalized = normalized.strip("._")
    if not normalized or normalized in {".", ".."}:
        raise ValueError("Identificador de transporte inválido.")
    return normalized[:80]


class PortalTransportLocks:
    """Travas locais multiprocesso; não coordenam hosts diferentes."""

    def __init__(self, root: Path, keys: Iterable[str]) -> None:
        self.root = Path(root).resolve()
        self.keys = sorted(set(keys))
        self._handles = []

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            for key in self.keys:
                name = uuid.uuid5(uuid.NAMESPACE_URL, key).hex + ".lock"
                handle = (self.root / name).open("a+b")
                self._handles.append(handle)
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(
                            handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB,
                        )
                except (OSError, BlockingIOError) as exc:
                    raise ConcorrenciaPortalBloqueada(
                        "Já existe homologação ativa para um dos transportes."
                    ) from exc
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type, exc, traceback):
        for handle in reversed(self._handles):
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            finally:
                handle.close()
        self._handles.clear()
        return False


class PlaywrightPortalSession:
    """Adaptação headless dos seletores comprovados na automação legada."""

    def __init__(
        self,
        settings: WhirlpoolPortalSettings,
        log: Callable[[str], None],
        playwright_factory=None,
    ) -> None:
        self.settings = settings
        self.log = log
        self.playwright_factory = playwright_factory
        self._manager = None
        self._playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.area = None
        self.planta_atual = None

    def __enter__(self):
        if self.playwright_factory is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise PortalWhirlpoolErro(
                    "Playwright não está instalado no ambiente."
                ) from exc
            self.playwright_factory = sync_playwright
        try:
            self._manager = self.playwright_factory()
            self._playwright = self._manager.start()
            self.browser = self._playwright.chromium.launch(
                headless=True,
                proxy=self.settings.proxy(),
            )
            self.context = self.browser.new_context(accept_downloads=True)
            self.page = self.context.new_page()
            self.page.set_default_timeout(self.settings.timeout_ms)
            self.page.set_default_navigation_timeout(
                self.settings.navigation_timeout_ms
            )
            return self
        except Exception as exc:
            self.__exit__(None, None, None)
            raise PortalWhirlpoolErro(
                "Não foi possível iniciar o Chromium headless."
            ) from exc

    def __exit__(self, exc_type, exc, traceback):
        for resource in (self.context, self.browser):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
        if self._manager is not None:
            try:
                self._manager.stop()
            except Exception:
                pass
        return False

    def autenticar(self) -> None:
        try:
            self.log("Iniciando primeira autenticação Whirlpool.")
            self.page.goto(
                self.settings.first_url,
                wait_until="domcontentloaded",
                timeout=self.settings.navigation_timeout_ms,
            )
            self.page.locator("#username").fill(self.settings.first_user)
            self.page.locator("#password").fill(self.settings.first_password)
            self.page.locator("#button").click()

            self.log("Iniciando segunda autenticação Whirlpool.")
            self.page.goto(
                self.settings.second_url,
                wait_until="domcontentloaded",
                timeout=self.settings.navigation_timeout_ms,
            )
            second_login = self.page.locator("#logonuidfield")
            login_or_portal = (
                second_login
                .or_(self.page.locator(MENU_CONSULTAS))
                .or_(self.page.get_by_text("Consultas", exact=False).first)
                .first
            )
            login_or_portal.wait_for(
                state="visible", timeout=self.settings.timeout_ms,
            )
            if second_login.is_visible():
                second_login.fill(
                    self.settings.second_user
                )
                self.page.locator("#logonpassfield").fill(
                    self.settings.second_password
                )
                self.page.locator(
                    "#certLogonForm input[name='uidPasswordLogon']"
                ).click()
            if not self._portal_carregado():
                raise PortalAutenticacaoRecusada(
                    "Portal não apresentou os elementos esperados após o login."
                )
            self.log("Autenticação Whirlpool concluída.")
        except PortalAutenticacaoRecusada:
            raise
        except Exception as exc:
            if exc.__class__.__name__ == "TimeoutError":
                raise PortalTimeout("Timeout durante autenticação Whirlpool.") from exc
            raise PortalAutenticacaoRecusada(
                "Não foi possível autenticar no portal Whirlpool."
            ) from exc

    def _portal_carregado(self) -> bool:
        expected = (
            self.page.locator(MENU_CONSULTAS)
            .or_(self.page.get_by_text("Consultas", exact=False).first)
            .or_(self.page.get_by_text("Bem-Vindo", exact=False).first)
            .first
        )
        try:
            expected.wait_for(state="visible", timeout=self.settings.timeout_ms)
            return True
        except Exception:
            return False

    def _abrir_consultas(self) -> None:
        try:
            if self.page.locator(MENU_CONSULTAS).count() > 0:
                self.page.locator(MENU_CONSULTAS).click()
            else:
                self.page.get_by_text("Consultas", exact=False).first.click()
            if self.page.locator(MENU_CARREGAMENTOS).count() > 0:
                self.page.locator(MENU_CARREGAMENTOS).click()
            else:
                self.page.get_by_text(
                    "Carregamentos Agendados", exact=False
                ).click()
            if self.page.locator(MENU_LOGISTICA_REVERSA).count() > 0:
                self.page.locator(MENU_LOGISTICA_REVERSA).click()
            else:
                self.page.get_by_text("Logística Reversa", exact=False).click()
            self.area = self._obter_area_trabalho()
        except Exception as exc:
            raise PortalWhirlpoolErro(
                "Não foi possível abrir a consulta de logística reversa."
            ) from exc

    def _obter_area_trabalho(self):
        candidates = []
        for frame in self.page.frames:
            try:
                score = 0
                if frame.locator("text=Transportador").count() > 0:
                    score += 5
                if frame.locator("text=Transportes Agendados").count() > 0:
                    score += 4
                if frame.locator("input[id*='PublishShipmentsView']").count() > 0:
                    score += 4
                if frame.locator("text=Logística Reversa").count() > 0:
                    score += 2
                if "webdynpro" in frame.url.casefold():
                    score += 3
                if score:
                    candidates.append((score, frame))
            except Exception:
                continue
        return max(candidates, key=lambda item: item[0])[1] if candidates else self.page

    def _selecionar_planta(self, planta: str) -> None:
        selectors = (
            f"xpath=//span[normalize-space(text())='{planta}']",
            f"xpath=//td[normalize-space(text())='{planta}']",
            f"text={planta}",
        )
        for selector in selectors:
            locator = self.area.locator(selector).first
            try:
                locator.wait_for(
                    state="visible", timeout=max(1_000, self.settings.timeout_ms // 3)
                )
                locator.click()
                self.planta_atual = planta
                return
            except Exception:
                continue
        raise TransporteNaoEncontrado("Planta não encontrada no portal Whirlpool.")

    def _preencher_transporte(self, transporte: str) -> None:
        for selector in CAMPO_TRANSPORTE:
            locator = self.area.locator(selector).first
            try:
                locator.wait_for(
                    state="visible", timeout=max(1_000, self.settings.timeout_ms // 5)
                )
                transport_value = str(transporte)
                locator.fill(transport_value)
                locator.press("Enter")
                locator.press("Tab")
                self.log(f"Transporte {transport_value}: consulta preenchida.")
                return
            except Exception:
                continue
        raise TransporteNaoEncontrado("Campo de transporte não encontrado.")

    def consultar_e_baixar(
        self, item: TransportePortal, output_dir: Path,
    ) -> list[Path]:
        if self.area is None:
            self._abrir_consultas()
        if item.planta != self.planta_atual:
            if self.planta_atual is not None:
                expand = self.area.locator("#aaaaPHNM\\.MainView\\.Tray-exp")
                if expand.count() > 0:
                    expand.click()
            self._selecionar_planta(item.planta)
        self._preencher_transporte(item.transporte)

        document_links = (
            self.area.locator(LINK_DOCUMENTOS[0][1])
            .or_(self.area.locator(LINK_DOCUMENTOS[1][1]))
            .first
        )
        try:
            document_links.wait_for(
                state="visible", timeout=self.settings.timeout_ms,
            )
        except Exception as exc:
            raise TransporteNaoEncontrado(
                f"Transporte {item.transporte}: nenhum documento localizado."
            ) from exc

        available = [
            document for document in LINK_DOCUMENTOS
            if self.area.locator(document[1]).count() > 0
        ]
        if not available:
            raise TransporteNaoEncontrado(
                f"Transporte {item.transporte}: nenhum documento localizado."
            )
        if len(available) != len(LINK_DOCUMENTOS):
            raise DocumentoIndisponivel(
                f"Transporte {item.transporte}: documento obrigatório indisponível."
            )
        return [
            self._baixar_pdf(item, selector, prefix, output_dir)
            for _kind, selector, prefix in available
        ]

    def _baixar_pdf(
        self,
        item: TransportePortal,
        selector: str,
        prefix: str,
        output_dir: Path,
    ) -> Path:
        popup = None
        same_page = False
        try:
            with self.page.expect_popup(timeout=self.settings.timeout_ms) as info:
                self.area.locator(selector).click()
            popup = info.value
            popup.wait_for_load_state(
                "domcontentloaded", timeout=self.settings.navigation_timeout_ms
            )
            pdf_url = popup.url
        except Exception as exc:
            pdf_url = self.page.url
            same_page = True
            if not pdf_url:
                raise DownloadPortalFalhou(
                    f"Transporte {item.transporte}: portal não abriu o PDF esperado."
                ) from exc
        try:
            body = self._obter_pdf(pdf_url, item)
            output_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{prefix}_{_safe_component(item.transporte)}.pdf"
            destination = (output_dir / filename).resolve()
            if output_dir.resolve() not in destination.parents:
                raise DownloadPortalFalhou("Destino de download inválido.")
            temporary = output_dir / f".{uuid.uuid4().hex}.tmp"
            try:
                with temporary.open("xb") as handle:
                    handle.write(body)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            self.log(f"Transporte {item.transporte}: {prefix} baixado.")
            return destination
        finally:
            if popup is not None:
                try:
                    popup.close()
                except Exception:
                    pass
            elif same_page:
                try:
                    self.page.go_back(
                        wait_until="domcontentloaded",
                        timeout=self.settings.navigation_timeout_ms,
                    )
                    self.area = self._obter_area_trabalho()
                except Exception:
                    self.area = None

    def _obter_pdf(self, pdf_url: str, item: TransportePortal) -> bytes:
        current_url = pdf_url
        for redirect_count in range(6):
            parsed = urlparse(current_url)
            if (
                parsed.scheme.casefold() != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.hostname.casefold() not in self.settings.allowed_hosts
            ):
                raise DownloadPortalFalhou(
                    f"Transporte {item.transporte}: origem do PDF não autorizada."
                )
            response = self.context.request.get(
                current_url,
                timeout=self.settings.navigation_timeout_ms,
                max_redirects=0,
                fail_on_status_code=False,
            )
            try:
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location", "").strip()
                    if not location or redirect_count == 5:
                        raise DownloadPortalFalhou(
                            f"Transporte {item.transporte}: redirecionamento inválido."
                        )
                    current_url = urljoin(current_url, location)
                    continue
                if not response.ok:
                    raise DownloadPortalFalhou(
                        f"Transporte {item.transporte}: download recusado pelo portal."
                    )
                content_length = response.headers.get("content-length", "").strip()
                if content_length:
                    try:
                        if int(content_length) > self.settings.max_pdf_bytes:
                            raise DownloadPortalFalhou(
                                f"Transporte {item.transporte}: PDF excede o limite."
                            )
                    except ValueError as exc:
                        raise DownloadPortalFalhou(
                            f"Transporte {item.transporte}: tamanho de PDF inválido."
                        ) from exc
                body = response.body()
                if (
                    not body.startswith(b"%PDF-")
                    or len(body) > self.settings.max_pdf_bytes
                ):
                    raise DownloadPortalFalhou(
                        f"Transporte {item.transporte}: resposta não é um PDF válido."
                    )
                return body
            finally:
                dispose = getattr(response, "dispose", None)
                if dispose is not None:
                    try:
                        dispose()
                    except Exception:
                        pass
        raise DownloadPortalFalhou(
            f"Transporte {item.transporte}: excesso de redirecionamentos."
        )


class WhirlpoolPortal:
    def __init__(
        self,
        settings: WhirlpoolPortalSettings | None = None,
        session_factory=None,
    ) -> None:
        self.settings = settings or WhirlpoolPortalSettings.from_env()
        self.session_factory = session_factory or PlaywrightPortalSession

    def baixar_documentos(
        self,
        transportes: list[TransportePortal] | None = None,
        output_dir: Path | None = None,
        log: Callable[[str], None] | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[ResultadoTransportePortal]:
        if not self.settings.enabled or not self.settings.dry_run:
            raise PortalWhirlpoolDesabilitado(
                "Homologação do portal Whirlpool está desabilitada."
            )
        if transportes is None or output_dir is None:
            raise ValueError("Transportes e diretório de saída são obrigatórios.")
        logger = log or (lambda _message: None)
        progress_callback = progress or (lambda _done, _total: None)
        results = []
        try:
            with self.session_factory(self.settings, logger) as session:
                session.autenticar()
                progress_callback(0, len(transportes))
                for index, item in enumerate(transportes, start=1):
                    logger(f"Transporte {item.transporte}: iniciando consulta.")
                    try:
                        documents = session.consultar_e_baixar(item, output_dir)
                        results.append(ResultadoTransportePortal(
                            planta=item.planta,
                            transporte=item.transporte,
                            status="DOCUMENTOS_BAIXADOS",
                            documentos=documents,
                        ))
                    except PortalWhirlpoolErro as exc:
                        logger(f"Transporte {item.transporte}: consulta falhou.")
                        results.append(ResultadoTransportePortal(
                            planta=item.planta,
                            transporte=item.transporte,
                            status="ERRO",
                            erro=str(exc),
                        ))
                    progress_callback(index, len(transportes))
        except (PortalAutenticacaoRecusada, PortalTimeout):
            raise
        except PortalWhirlpoolErro:
            raise
        except Exception as exc:
            raise PortalWhirlpoolErro(
                "Falha inesperada na homologação do portal Whirlpool."
            ) from exc
        return results
