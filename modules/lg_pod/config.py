from dataclasses import dataclass
from datetime import date, datetime
import os
import math
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ('SC', 'PR', 'SP')


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
    ca_bundle: str = ''
    read_attempts: int = 4
    read_retry_delay_s: float = 5.0
    read_retry_max_delay_s: float = 30.0

    operation: str = 'SC'

    @classmethod
    def from_env(cls, operation='SC'):
        load_env()
        operation = operation.upper().strip()
        if operation not in PROFILES:
            raise ValueError('Operação inválida. Escolha SC, PR ou SP.')
        prefix = 'LG_POD_' + operation + '_'
        # Credenciais formam um par. PR nunca herda o usuário/senha de SC.
        credentials_prefix = prefix if operation != 'SC' or any(
            prefix + key in os.environ for key in ('USER', 'PASSWORD')) else 'LG_POD_'
        def value(key, default=''):
            return os.getenv(prefix + key, os.getenv('LG_POD_' + key, default))
        send_var = prefix + 'ENVIO_HABILITADO'
        if operation == 'SC' and send_var not in os.environ:
            send_var = 'LG_POD_ENVIO_HABILITADO'
        return cls(
            user=os.getenv(credentials_prefix + 'USER', '').strip(),
            password=os.getenv(credentials_prefix + 'PASSWORD', ''),
            carrier=os.getenv(prefix + 'CARRIER', os.getenv('LG_POD_CARRIER', 'ROBR_SC') if operation == 'SC' else 'ROBR_' + operation).strip(),
            invoice_start=date.fromisoformat(value('NF_DATA_INICIAL', '2025-01-01')),
            send_enabled=enabled(send_var),
            timeout_ms=int(value('TIMEOUT_MS', '60000')),
            max_records=int(value('MAX_REGISTROS', '20000')),
            proxy_server=value('PROXY_SERVER').strip(),
            proxy_user=value('PROXY_USER'),
            proxy_password=value('PROXY_PASSWORD'),
            ca_bundle=value('CA_BUNDLE', os.getenv('REQUESTS_CA_BUNDLE', '')).strip(),
            read_attempts=int(value('READ_ATTEMPTS', '4')),
            read_retry_delay_s=float(value('READ_RETRY_DELAY_S', '5')),
            read_retry_max_delay_s=float(value('READ_RETRY_MAX_DELAY_S', '30')),
            operation=operation,
        )

    def validate(self, *, real=False):
        if not self.user or not self.password:
            raise ValueError(f'Configure usuário e senha da operação {self.operation} no .env (LG_POD_{self.operation}_USER/PASSWORD; SC também aceita o par legado LG_POD_USER/PASSWORD).')
        if not self.carrier.startswith('ROBR'):
            raise ValueError('LG_POD_CARRIER deve começar com ROBR.')
        if self.invoice_start > today() or self.max_records < 1 or self.timeout_ms < 1000:
            raise ValueError('Configuração de período, limite ou timeout inválida.')
        if real and not self.send_enabled:
            raise ValueError(f'O envio da operação {self.operation} está desabilitado no .env.')
        if (not 1 <= self.read_attempts <= 6
                or not math.isfinite(self.read_retry_delay_s)
                or not math.isfinite(self.read_retry_max_delay_s)
                or not 1 <= self.read_retry_delay_s <= self.read_retry_max_delay_s <= 60):
            raise ValueError('Retries de leitura inválidos: 1 a 6 tentativas; espera inicial/máxima entre 1 e 60 segundos.')
        if enabled('SSW_QA_MODE'):
            raise ValueError('Execuções do portal LG não são permitidas no processo QA.')
