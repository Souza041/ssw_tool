from dataclasses import dataclass
from datetime import date, datetime
import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]


def load_env():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')


def today() -> date:
    return datetime.now(ZoneInfo('America/Sao_Paulo')).date()


def validate_period(start: date, end: date):
    if start > end:
        raise ValueError('A data inicial deve ser menor ou igual à final.')
    if end > today():
        raise ValueError('A data final não pode estar no futuro.')


def enabled(name):
    return os.getenv(name, '').strip().lower() in {'1', 'true', 'sim', 'yes'}


@dataclass(frozen=True)
class Settings:
    user: str
    password: str
    carrier: str
    invoice_start: date
    send_enabled: bool = False
    timeout_ms: int = 60000
    max_records: int = 20000
    proxy_server: str = ''
    proxy_user: str = ''
    proxy_password: str = ''
    base_url: str = 'https://tms.br.lge.com'

    @classmethod
    def from_env(cls):
        load_env()
        return cls(
            user=os.getenv('LG_POD_USER', '').strip(),
            password=os.getenv('LG_POD_PASSWORD', ''),
            carrier=os.getenv('LG_POD_CARRIER', 'ROBR_SC').strip(),
            invoice_start=date.fromisoformat(os.getenv('LG_POD_NF_DATA_INICIAL', '2025-01-01')),
            send_enabled=enabled('LG_POD_ENVIO_HABILITADO'),
            timeout_ms=int(os.getenv('LG_POD_TIMEOUT_MS', '60000')),
            max_records=int(os.getenv('LG_POD_MAX_REGISTROS', '20000')),
            proxy_server=os.getenv('LG_POD_PROXY_SERVER', '').strip(),
            proxy_user=os.getenv('LG_POD_PROXY_USER', ''),
            proxy_password=os.getenv('LG_POD_PROXY_PASSWORD', ''),
        )

    def validate(self, *, real=False):
        if not self.user or not self.password:
            raise ValueError('Configure LG_POD_USER e LG_POD_PASSWORD no .env.')
        if not self.carrier.startswith('ROBR'):
            raise ValueError('LG_POD_CARRIER deve começar com ROBR.')
        if self.invoice_start > today() or self.max_records < 1 or self.timeout_ms < 1000:
            raise ValueError('Configuração de período, limite ou timeout inválida.')
        if real and not self.send_enabled:
            raise ValueError('O envio está desabilitado no .env (LG_POD_ENVIO_HABILITADO).')
        if enabled('SSW_QA_MODE'):
            raise ValueError('Execuções do portal LG não são permitidas no processo QA.')
