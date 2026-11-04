import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class WhirlpoolSettings:
    data_dir: Path
    max_upload_bytes: int = 20 * 1024 * 1024
    smtp_enabled: bool = False
    portal_enabled: bool = False

    @classmethod
    def from_env(cls) -> "WhirlpoolSettings":
        configured = os.getenv("WHIRLPOOL_DATA_DIR", "var/whirlpool").strip()
        data_dir = Path(configured)
        if not data_dir.is_absolute():
            data_dir = PROJECT_ROOT / data_dir
        data_dir = data_dir.resolve()

        public_roots = (
            (PROJECT_ROOT / "downloads").resolve(),
            (PROJECT_ROOT / "web" / "static").resolve(),
        )
        if any(data_dir == root or root in data_dir.parents for root in public_roots):
            raise ValueError("WHIRLPOOL_DATA_DIR não pode apontar para diretório público.")

        return cls(data_dir=data_dir)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "sim", "yes", "on"}


@dataclass(frozen=True)
class WhirlpoolPortalSettings:
    enabled: bool
    dry_run: bool
    first_url: str = field(repr=False)
    second_url: str = field(repr=False)
    first_user: str = field(repr=False)
    first_password: str = field(repr=False)
    second_user: str = field(repr=False)
    second_password: str = field(repr=False)
    authorized_users: frozenset[str] = field(repr=False)
    timeout_ms: int = 45_000
    navigation_timeout_ms: int = 90_000
    max_transports: int = 10
    max_pdf_bytes: int = 25 * 1024 * 1024
    proxy_server: str = field(default="", repr=False)
    proxy_user: str = field(default="", repr=False)
    proxy_password: str = field(default="", repr=False)
    allowed_hosts: frozenset[str] = field(default_factory=frozenset, repr=False)

    @classmethod
    def from_env(cls) -> "WhirlpoolPortalSettings":
        first_url = os.getenv("WHIRLPOOL_PORTAL_URL_1", "").strip()
        second_url = os.getenv("WHIRLPOOL_PORTAL_URL_2", "").strip()
        configured_hosts = {
            item.strip().casefold()
            for item in os.getenv("WHIRLPOOL_PORTAL_ALLOWED_HOSTS", "").split(",")
            if item.strip()
        }
        for url in (first_url, second_url):
            host = (urlparse(url).hostname or "").casefold()
            if host:
                configured_hosts.add(host)
        return cls(
            enabled=_env_bool("WHIRLPOOL_PORTAL_ENABLED"),
            dry_run=_env_bool("WHIRLPOOL_PORTAL_DRY_RUN"),
            first_url=first_url,
            second_url=second_url,
            first_user=os.getenv("WHIRLPOOL_PORTAL_USER_1", "").strip(),
            first_password=os.getenv("WHIRLPOOL_PORTAL_PASSWORD_1", ""),
            second_user=os.getenv("WHIRLPOOL_PORTAL_USER_2", "").strip(),
            second_password=os.getenv("WHIRLPOOL_PORTAL_PASSWORD_2", ""),
            authorized_users=frozenset(
                item.strip().casefold()
                for item in os.getenv(
                    "WHIRLPOOL_PORTAL_AUTHORIZED_USERS", ""
                ).split(",")
                if item.strip()
            ),
            timeout_ms=int(os.getenv("WHIRLPOOL_PORTAL_TIMEOUT_MS", "45000")),
            navigation_timeout_ms=int(
                os.getenv("WHIRLPOOL_PORTAL_NAVIGATION_TIMEOUT_MS", "90000")
            ),
            max_transports=int(
                os.getenv("WHIRLPOOL_PORTAL_MAX_TRANSPORTS", "10")
            ),
            max_pdf_bytes=int(
                os.getenv("WHIRLPOOL_PORTAL_MAX_PDF_BYTES", str(25 * 1024 * 1024))
            ),
            proxy_server=os.getenv("WHIRLPOOL_PROXY_SERVER", "").strip(),
            proxy_user=os.getenv("WHIRLPOOL_PROXY_USER", "").strip(),
            proxy_password=os.getenv("WHIRLPOOL_PROXY_PASSWORD", ""),
            allowed_hosts=frozenset(configured_hosts),
        )

    def validar_homologacao(self, usuario: str) -> None:
        if not self.enabled or not self.dry_run:
            raise ValueError("Homologação do portal Whirlpool está desabilitada.")
        if not usuario or usuario.strip().casefold() not in self.authorized_users:
            raise PermissionError("Usuário não autorizado para homologação Whirlpool.")
        required = (
            self.first_url, self.second_url, self.first_user,
            self.first_password, self.second_user, self.second_password,
        )
        if not all(required):
            raise ValueError("Configuração do portal Whirlpool está incompleta.")
        for url in (self.first_url, self.second_url):
            parsed = urlparse(url)
            if (
                parsed.scheme.casefold() != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("URL do portal Whirlpool deve usar HTTPS válido.")
        if not self.allowed_hosts:
            raise ValueError("Hosts permitidos do portal Whirlpool não configurados.")
        if not 1 <= self.max_transports <= 100:
            raise ValueError("Limite de transportes Whirlpool inválido.")
        if not 5_000 <= self.timeout_ms <= 180_000:
            raise ValueError("Timeout do portal Whirlpool inválido.")
        if not 5_000 <= self.navigation_timeout_ms <= 300_000:
            raise ValueError("Timeout de navegação Whirlpool inválido.")
        if not 1_024 <= self.max_pdf_bytes <= 100 * 1024 * 1024:
            raise ValueError("Limite de PDF Whirlpool inválido.")
        if self.proxy_server:
            proxy = urlparse(self.proxy_server)
            if (
                proxy.scheme.casefold() not in {"http", "https"}
                or not proxy.hostname
                or proxy.username
                or proxy.password
            ):
                raise ValueError("Proxy Whirlpool inválido.")

    def proxy(self) -> dict | None:
        if not self.proxy_server:
            return None
        result = {"server": self.proxy_server}
        if self.proxy_user:
            result["username"] = self.proxy_user
        if self.proxy_password:
            result["password"] = self.proxy_password
        return result
