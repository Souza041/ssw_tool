import math
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
SECRET_ENV_NAME = "SSW_SESSION_SECRET_KEY"
ENVIRONMENT_ENV_NAME = "SSW_ENVIRONMENT"
MIN_SECRET_LENGTH = 48
MIN_ESTIMATED_ENTROPY_BITS = 192.0

_VALORES_PREVISIVEIS = frozenset({
    "troque-essa-chave-depois",
    "change-me",
    "changeme",
    "secret",
    "session-secret",
    "password",
    "development",
    "example",
})
_TRECHOS_PREVISIVEIS = (
    "troque-essa-chave",
    "change-me",
    "changeme",
    "password",
    "session-secret",
)


class SessionConfigurationError(RuntimeError):
    """Erro sanitizado de configuração da sessão web."""


@dataclass(frozen=True)
class SessionSecurityConfig:
    secret_key: str = field(repr=False)
    environment: str
    source: str


def _erro_configuracao() -> SessionConfigurationError:
    return SessionConfigurationError(
        "Configuração segura de sessão indisponível."
    )


def _entropia_shannon_estimada(valor: str) -> float:
    total = len(valor)
    if not total:
        return 0.0
    entropia_por_simbolo = -sum(
        (quantidade / total) * math.log2(quantidade / total)
        for quantidade in Counter(valor).values()
    )
    return entropia_por_simbolo * total


def _possui_periodo_curto(valor: str) -> bool:
    for tamanho in range(1, min(17, len(valor))):
        padrao = valor[:tamanho]
        repetido = (padrao * ((len(valor) // tamanho) + 1))[:len(valor)]
        if repetido == valor:
            return True
    return False


def validar_session_secret_key(valor: object) -> str:
    if not isinstance(valor, str):
        raise _erro_configuracao()
    if not valor or valor != valor.strip():
        raise _erro_configuracao()
    if len(valor) < MIN_SECRET_LENGTH:
        raise _erro_configuracao()

    normalizado = valor.casefold()
    if (
        normalizado in _VALORES_PREVISIVEIS
        or any(trecho in normalizado for trecho in _TRECHOS_PREVISIVEIS)
        or len(set(valor)) < 12
        or _possui_periodo_curto(valor)
        or _entropia_shannon_estimada(valor) < MIN_ESTIMATED_ENTROPY_BITS
    ):
        raise _erro_configuracao()
    return valor


def carregar_configuracao_sessao(
    environ: Mapping[str, str] | None = None,
    *,
    env_file: Path = DEFAULT_ENV_FILE,
) -> SessionSecurityConfig:
    if environ is None:
        definida_no_processo = SECRET_ENV_NAME in os.environ
        load_dotenv(env_file, override=False)
        valores = os.environ
        source = (
            "ambiente do processo"
            if definida_no_processo
            else ".env da raiz do projeto"
        )
    else:
        valores = environ
        source = "mapeamento explícito"

    secret_key = validar_session_secret_key(valores.get(SECRET_ENV_NAME))
    environment = str(
        valores.get(ENVIRONMENT_ENV_NAME, "production")
    ).strip().lower() or "production"
    return SessionSecurityConfig(
        secret_key=secret_key,
        environment=environment,
        source=source,
    )
