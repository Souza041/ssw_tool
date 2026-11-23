"""Sessão Chromium interativa, exclusiva do processo Companion Windows."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

from modules.local_agent.ssw_companion import (
    await_human_login,
    CompanionContract,
    CompanionError,
    attach_official_navigation_monitor,
    capture_known_login_identity,
    ensure_official_main_frame,
    known_authenticated_menu_present,
    official_login_url,
    validate_authenticated_context,
)
from modules.local_agent.ssw_companion_proxy import CompanionProxy, launch_options
from ssw.browser_client import BrowserSSWClient


@dataclass(frozen=True)
class CompanionIdentity:
    usuario: str
    unidade: str


class InteractiveSSWSession:
    def __init__(
        self,
        *,
        contract: CompanionContract,
        base_url: str,
        proxy: CompanionProxy | None,
        playwright_factory,
        logger,
    ) -> None:
        self.contract = contract
        self.base_url = base_url
        self.proxy = proxy
        self.playwright_factory = playwright_factory
        self.logger = logger
        self.manager = self.browser = self.context = self.page = None
        self.origin_monitor = None
        self.identity: CompanionIdentity | None = None
        self.observed_domain = ""
        self.observed_identity = ""

    def _discard_context(self) -> None:
        if self.context is not None:
            try:
                self.context.close()
            except Exception:
                pass
        self.context = self.page = None
        self.origin_monitor = None
        self.identity = None
        self.observed_domain = ""
        self.observed_identity = ""

    def _ensure_browser(self) -> None:
        if self.manager is None:
            self.manager = self.playwright_factory().start()
        if self.browser is None:
            self.browser = self.manager.chromium.launch(**launch_options(self.proxy))

    def _new_login_context(self) -> None:
        self._discard_context()
        self._ensure_browser()
        from modules.local_agent.ssw_companion_proxy import (
            create_isolated_page, navigate_official_login, wait_for_official_dom,
        )
        self.context, self.page = create_isolated_page(self.browser)
        login_url = official_login_url(self.base_url)
        try:
            navigate_official_login(
                self.page, login_url, timeout_ms=60_000, logger=self.logger,
            )
            ensure_official_main_frame(self.page)
            self.origin_monitor = attach_official_navigation_monitor(
                self.page, on_violation=self._discard_context, logger=self.logger,
            )
            self.origin_monitor.ensure_allowed()
        except BaseException:
            self._discard_context()
            raise

    def _capture_known_login_identity(self) -> None:
        if self.page is None:
            return
        captured = capture_known_login_identity(self.page, self.contract)
        if captured is None:
            return
        self.observed_domain, self.observed_identity = captured

    def _valid(
        self, usuario: str, unidade: str, dominio: str | None = None,
    ) -> bool:
        if self.page is None:
            return False
        try:
            ensure_official_main_frame(self.page)
        except CompanionError:
            self._discard_context()
            return False
        try:
            if self.origin_monitor is not None:
                self.origin_monitor.ensure_allowed()
            validate_authenticated_context(
                self.page,
                self.contract,
                login_url=official_login_url(self.base_url),
                expected_identity=usuario,
                expected_unit=unidade,
                observed_identity=self.observed_identity or None,
                expected_domain=dominio,
                observed_domain=self.observed_domain or None,
            )
            if self.origin_monitor is None:
                self.origin_monitor = attach_official_navigation_monitor(
                    self.page, on_violation=self._discard_context, logger=self.logger,
                )
                self.origin_monitor.ensure_allowed()
            return True
        except CompanionError:
            if (
                getattr(self.contract, "version", 1) == 2
                and known_authenticated_menu_present(self.page, self.contract)
            ):
                raise
            return False

    def authenticate(
        self,
        *,
        usuario: str,
        unidade: str,
        timeout_seconds: int,
        cancel: Callable[[], None],
        dominio: str | None = None,
    ) -> CompanionIdentity:
        usuario = str(usuario).strip()
        unidade = str(unidade).strip().upper()
        if self.identity and self._valid(usuario, unidade, dominio):
            return self.identity
        try:
            self._new_login_context()
            self.logger.info("Autenticação humana necessária na janela Chromium.")
            self.observed_domain, self.observed_identity = await_human_login(
                self.page, self.contract, login_url=official_login_url(self.base_url),
                expected_identity=usuario, expected_unit=unidade, expected_domain=dominio,
                timeout_seconds=timeout_seconds, cancel=cancel,
            )
            self.origin_monitor.ensure_allowed()
            self.identity = CompanionIdentity(usuario, unidade)
            self.logger.info("Sessão SSW confirmada estruturalmente.")
            return self.identity
        except BaseException:
            self._discard_context()
            raise

    def client(self, *, usuario: str, unidade: str, cancel=None) -> BrowserSSWClient:
        if not self._valid(usuario, unidade):
            self.identity = None
            raise CompanionError("Sessão SSW ausente, expirada ou divergente.")
        def validate():
            if not self._valid(usuario, unidade):
                raise CompanionError("Sessão SSW ausente, expirada ou divergente.")
        return BrowserSSWClient(
            self.page,
            base_url=self.base_url,
            usuario=usuario,
            unidade=unidade,
            cancel=cancel,
            validate=validate,
        )

    def close(self) -> None:
        self._discard_context()
        if self.browser is not None:
            try:
                self.browser.close()
            except Exception:
                pass
        if self.manager is not None:
            try:
                self.manager.stop()
            except Exception:
                pass
        self.browser = self.manager = None

