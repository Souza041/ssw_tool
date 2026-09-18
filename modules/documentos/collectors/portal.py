from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
from urllib.parse import quote, urlsplit, urlunsplit

import requests

from modules.documentos.config import DocumentosSettings, settings


@dataclass(frozen=True)
class CarrierDownloads:
    carrier_id: int
    carrier_cnpj: str
    carrier_name: str
    solucionar: Path
    aguardando_solucao: Path


@dataclass(frozen=True)
class PortalDownloads:
    carriers: tuple[CarrierDownloads, ...]

    @property
    def solucionar_files(self) -> list[Path]:
        return [
            carrier.solucionar
            for carrier in self.carriers
        ]

    @property
    def aguardando_solucao_files(self) -> list[Path]:
        return [
            carrier.aguardando_solucao
            for carrier in self.carriers
        ]


class GCEPortalCollector:

    TARGET_CNPJS = {
        "02141029000119",
        "02141029000461",
        "02141029000623",
        "02141029000895",
        "02141029001000",
    }

    def __init__(
        self,
        config: DocumentosSettings = settings,
    ) -> None:
        self.config = config
        if self.config.gce_use_system_ca:
            try:
                import truststore
            except ImportError as exc:
                raise RuntimeError(
                    "GCE_USE_SYSTEM_CA=true exige o pacote truststore. "
                    "Instale com: python -m pip install truststore"
                ) from exc
            truststore.inject_into_ssl()
        self.session = requests.Session()
        self._carrier_group_id: str | None = None
        if self.config.gce_proxy_url:
            self.session.trust_env = False
            proxy_url = self._proxy_url()
            self.session.proxies.update({"http": proxy_url, "https": proxy_url})
        if self.config.gce_ca_bundle:
            ca_bundle = Path(self.config.gce_ca_bundle)
            if not ca_bundle.is_file():
                raise FileNotFoundError(f"GCE_CA_BUNDLE não encontrado: {ca_bundle}")
            self.session.verify = str(ca_bundle)
        self.session.headers.update(
            {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "pt-BR,pt;q=0.9",
                "Content-Type": "application/json; charset=utf-8",
                "Origin": "https://gce.armazemdedocumentos.com.br",
                "Referer": "https://gce.armazemdedocumentos.com.br/",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/152.0.0.0 Safari/537.36"
                ),
            }
        )

    def _proxy_url(self) -> str:
        proxy_url = self.config.gce_proxy_url
        if not self.config.gce_proxy_user:
            return proxy_url

        parsed = urlsplit(proxy_url)
        if not parsed.scheme or not parsed.hostname:
            raise ValueError(
                "GCE_PROXY_URL inválida. Use, por exemplo, http://servidor:3128"
            )

        username = quote(self.config.gce_proxy_user, safe="")
        password = quote(self.config.gce_proxy_password, safe="")
        credentials = username if not password else f"{username}:{password}"
        port = f":{parsed.port}" if parsed.port else ""
        netloc = f"{credentials}@{parsed.hostname}{port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

    @staticmethod
    def _http_error(response: requests.Response, action: str) -> None:
        if response.ok:
            return
        detail = response.text.strip().replace("\r", " ").replace("\n", " ")[:300]
        suffix = f" Resposta: {detail}" if detail else ""
        raise requests.HTTPError(
            f"Portal GCE: HTTP {response.status_code} durante {action}.{suffix}",
            response=response,
        )

    @staticmethod
    def _json_result(response: requests.Response, action: str) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Portal GCE retornou uma resposta inválida durante {action}."
            ) from exc

        if not isinstance(payload, dict) or payload.get("success") is not True:
            message = payload.get("msg") if isinstance(payload, dict) else None
            detail = str(message).strip() if message else "operação recusada pelo portal"
            raise PermissionError(f"Portal GCE: {action} falhou: {detail}")
        return payload

    def login(self) -> None:
        self.config.validate_gce()
        response = self.session.post(
            f"{self.config.gce_base_url}/uccaduser/_login",
            json={"login": self.config.gce_login, "senha": self.config.gce_password},
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=self.config.gce_timeout,
        )
        self._http_error(response, "login")
        payload = self._json_result(response, "login")
        token = str(payload.get("msg") or "").strip()
        if token.count(".") != 2:
            raise PermissionError(
                "Portal GCE: login aceito, mas nenhum token de acesso foi retornado."
            )
        self.session.headers["Authorization"] = f"Bearer {token}"

        users = payload.get("data", {}).get("usuario") or []
        if not users:
            raise PermissionError("Portal GCE: login não retornou o usuário autenticado.")
        group = users[0].get("grupotransportador") or {}
        group_id = group.get("id")
        if group_id is None:
            raise PermissionError("Portal GCE: usuário sem grupo de transportadora.")
        self._carrier_group_id = str(group_id)

    def _search_carrier(self, payload: dict, action: str) -> dict:
        response = self.session.post(
            f"{self.config.gce_base_url}/transportador/_search",
            json=payload,
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=self.config.gce_timeout,
        )
        self._http_error(response, action)
        return self._json_result(response, action)

    def list_carriers(self) -> list[dict]:
        if not self._carrier_group_id:
            raise RuntimeError(
                "Consulta das transportadoras chamada antes do login."
            )

        payload = self._search_carrier(
            {
                "condition": "AND",
                "rules": [
                    {
                        "field": "transportador.grupotransportador.id",
                        "type": "integer",
                        "operator": "equal",
                        "value": int(self._carrier_group_id),
                    }
                ],
                "order": [
                    {
                        "field": "transportador.cnpj",
                        "dir": "asc",
                    }
                ],
            },
            "consulta das transportadoras do grupo",
        )

        carriers = []

        for item in payload.get("data") or []:
            carrier = item.get("transportador") or item

            carrier_id = carrier.get("id")
            cnpj = self._normalize_cnpj(
                carrier.get("cnpj")
            )

            if carrier_id is None or not cnpj:
                continue

            if cnpj not in self.TARGET_CNPJS:
                continue

            carriers.append(
                {
                    "id": int(carrier_id),
                    "cnpj": cnpj,
                    "name": str(
                        carrier.get("razaosocial")
                        or carrier.get("nome")
                        or cnpj
                    ).strip(),
                }
            )

        found = {
            carrier["cnpj"]
            for carrier in carriers
        }

        missing = self.TARGET_CNPJS - found

        if missing:
            raise PermissionError(
                "Portal GCE: transportadoras não encontradas: "
                + ", ".join(sorted(missing))
            )

        return carriers


    def select_carrier(
        self,
        carrier_id: int,
    ) -> None:
        response = self.session.post(
            f"{self.config.gce_base_url}/documento/_notifytransp",
            json={
                "idtransportador": carrier_id,
            },
            headers={
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=self.config.gce_timeout,
        )

        self._http_error(
            response,
            "seleção da transportadora",
        )

        self._json_result(
            response,
            "seleção da transportadora",
        )

    @staticmethod
    def _filename(response: requests.Response, fallback: str) -> str:
        disposition = response.headers.get("Content-Disposition", "")
        match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.I)
        if not match:
            return fallback
        name = Path(match.group(1).strip()).name
        return name if name.lower().endswith((".xlsx", ".xls")) else fallback

    @staticmethod
    def _validate_excel(content: bytes, report_name: str) -> None:
        if len(content) < 100:
            raise ValueError(f"Relatório {report_name} vazio ou incompleto.")
        is_xlsx = content.startswith(b"PK")
        is_xls = content.startswith(bytes.fromhex("D0CF11E0A1B11AE1"))
        if not (is_xlsx or is_xls):
            preview = content[:80].decode("utf-8", errors="ignore").lower()
            if "<html" in preview or "<!doctype" in preview:
                raise ValueError(f"Portal retornou HTML em vez do relatório {report_name}.")

    def _download(self, endpoint: str, output_dir: Path, fallback: str, carrier_id: int) -> Path:
        response = self.session.post(
            f"{self.config.gce_base_url}/documento/{endpoint}",
            json={"idtransportador": carrier_id},
            headers={"Accept": "*/*"},
            timeout=self.config.gce_timeout,
        )
        self._http_error(response, f"download de {endpoint}")
        self._validate_excel(response.content, endpoint)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / self._filename(response, fallback)
        path.write_bytes(response.content)
        return path

    def download_daily(
        self,
        output_root: Path,
        reference_date: date | None = None,
    ) -> PortalDownloads:

        reference_date = reference_date or date.today()

        base_dir = (
            output_root
            / reference_date.strftime("%Y/%m/%d")
            / "portal"
        )

        self.login()

        carriers = self.list_carriers()

        downloads: list[CarrierDownloads] = []

        for carrier in carriers:
            carrier_id = carrier["id"]
            carrier_cnpj = carrier["cnpj"]
            carrier_name = carrier["name"]

            print(
                "[GCE] Coletando "
                f"{carrier_cnpj} - {carrier_name}..."
            )

            self.select_carrier(carrier_id)

            output_dir = (
                base_dir
                / carrier_cnpj
            )

            solucionar = self._download(
                "_printsolucionar",
                output_dir,
                "portal_solucionar.xlsx",
                carrier_id,
            )

            aguardando = self._download(
                "_printaguardandosolucao",
                output_dir,
                "portal_aguarda_solucao.xlsx",
                carrier_id,
            )

            downloads.append(
                CarrierDownloads(
                    carrier_id=carrier_id,
                    carrier_cnpj=carrier_cnpj,
                    carrier_name=carrier_name,
                    solucionar=solucionar,
                    aguardando_solucao=aguardando,
                )
            )

            print(
                "[GCE] "
                f"{carrier_cnpj}: coleta concluída."
            )

        return PortalDownloads(
            carriers=tuple(downloads),
        )

    @staticmethod
    def _normalize_cnpj(value: object) -> str:
        return re.sub(r"\D", "", str(value or ""))