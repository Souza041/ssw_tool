from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "sim", "yes", "on"}


@dataclass(frozen=True)
class DocumentosSettings:
    gce_base_url: str = os.getenv(
        "GCE_BASE_URL", "https://gce.armazemdedocumentos.com.br/gce-server/api"
    ).rstrip("/")
    gce_login: str = os.getenv("GCE_LOGIN", "")
    gce_password: str = os.getenv("GCE_PASSWORD", "")
    gce_carrier_id: str = os.getenv("GCE_CARRIER_ID", "3")
    gce_timeout: int = int(os.getenv("GCE_TIMEOUT", "180"))
    gce_use_system_ca: bool = _env_bool("GCE_USE_SYSTEM_CA", True)
    gce_ca_bundle: str = os.getenv("GCE_CA_BUNDLE", "").strip()
    gce_proxy_url: str = os.getenv("GCE_PROXY_URL", "").strip()
    gce_proxy_user: str = os.getenv("GCE_PROXY_USER", "").strip()
    gce_proxy_password: str = os.getenv("GCE_PROXY_PASSWORD", "")
    daily_overlap_days: int = int(os.getenv("DOCUMENTOS_DAILY_OVERLAP_DAYS", "7"))
    target_year: int = int(os.getenv("DOCUMENTOS_TARGET_YEAR", "2026"))
    op930_cnpj: str = os.getenv("DOCUMENTOS_OP930_CNPJ", "05117268000806").strip()
    op930_group: str = os.getenv("DOCUMENTOS_OP930_GROUP", "WHIRLPOOL").strip()
    ssw_report_timeout: int = int(os.getenv("DOCUMENTOS_SSW_REPORT_TIMEOUT", "600"))

    def validate_gce(self) -> None:
        missing = []
        if not self.gce_login:
            missing.append("GCE_LOGIN")
        if not self.gce_password:
            missing.append("GCE_PASSWORD")
        if missing:
            raise ValueError("Variáveis GCE ausentes: " + ", ".join(missing))


settings = DocumentosSettings()
