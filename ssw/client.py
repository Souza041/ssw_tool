import requests

from ssw.settings import settings, normalizar_base_url
from ssw.utils import dummy

import time


class SSWClient:
    def __init__(
        self,
        dominio: str | None = None,
        cpf: str | None = None,
        usuario: str | None = None,
        senha: str | None = None,
        unidade: str | None = None,
        auth_mode: str | None = None,
    ) -> None:
        settings.validate(required_login=False)

        self.base_url = normalizar_base_url(settings.base_url)
        self.timeout = settings.timeout
        self.session = requests.Session()
        self.auth_mode = str(auth_mode or settings.auth_mode).strip().lower()

        self.dominio = dominio or settings.dominio
        self.usuario = usuario or settings.usuario
        self.unidade = unidade or settings.unidade
        if self.auth_mode == "browser":
            # Credenciais sao informadas somente na pagina oficial do SSW.
            self.cpf = ""
            self.senha = ""
        else:
            self.cpf = cpf or settings.cpf
            self.senha = senha or settings.senha

        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/bin/ssw0422",
        })

    def post(
            self,
            path: str,
            data: dict | None = None,
            retries: int = 3,
            wait_seconds: float = 2,
        ) -> requests.Response:
            url = f"{self.base_url}{path}"
            last_error = None

            for attempt in range(1, retries + 1):
                try:
                    response = self.session.post(
                        url,
                        data=data or {},
                        timeout=self.timeout,
                    )
                    response.raise_for_status()
                    return response

                except Exception as exc:
                    last_error = exc

                    if attempt < retries:
                        time.sleep(wait_seconds)

            raise last_error

    def get(
        self,
        path: str,
        params: dict | None = None,
        retries: int = 3,
        wait_seconds: float = 2,
    ) -> requests.Response:
        url = f"{self.base_url}{path}"
        last_error = None

        for attempt in range(1, retries + 1):
            try:
                response = self.session.get(
                    url,
                    params=params or {},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return response

            except Exception as exc:
                last_error = exc

                if attempt < retries:
                    time.sleep(wait_seconds)

        raise last_error

    def login(self) -> None:
        if self.auth_mode == "browser":
            from ssw.browser_transport import authenticated_browser_transport

            self.session = authenticated_browser_transport(
                domain=self.dominio,
                user=self.usuario,
                unit=self.unidade,
                base_url=self.base_url,
                timeout_seconds=settings.human_login_timeout,
            )
            self.authenticated_unit = self.session.observed_unit
            return None

        payload = {
            "act": "L",
            "f1": self.dominio,
            "f2": self.cpf,
            "f3": self.usuario,
            "f4": self.senha,
            "f6": "TRUE",
            "dummy": dummy(),
        }

        self.post("/bin/ssw0422", payload)

    def close(self) -> None:
        from ssw.browser_transport import BrowserSessionTransport, discard_browser_transport

        if isinstance(self.session, BrowserSessionTransport):
            discard_browser_transport(self.session)
        else:
            self.session.close()

    def open_menu(self) -> None:
        self.post("/bin/menu01", {"act": ""})

    def open_option(self, option: str, unidade: str | None = None) -> requests.Response:
        payload_menu = {
            "act": "TRO",
            "f2": unidade or self.unidade,
            "f3": str(option),
            "dummy": dummy(),
        }

        self.post("/bin/menu01", payload_menu)

        return self.post(
            f"/bin/ssw0053",
            {
                "sequencia": str(option),
                "dummy": dummy(),
            },
        )
