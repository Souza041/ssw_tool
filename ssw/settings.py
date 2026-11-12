import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()


DEFAULT_SSW_BASE_URL = "https://tms.ssw.inf.br"


def normalizar_base_url(valor: str) -> str:
    """A configuração representa somente a origem, sem caminhos ou credenciais."""
    valor = str(valor or "").strip().rstrip("/")
    try:
        partes = urlsplit(valor)
        _ = partes.port  # Valida também a faixa da porta.
        if (
            partes.scheme not in {"http", "https"} or not partes.hostname
            or partes.username is not None or partes.password is not None
            or partes.path or partes.query or partes.fragment
            or any(c.isspace() for c in valor)
        ):
            raise ValueError
    except ValueError:
        raise ValueError("SSW_BASE_URL inválida: informe somente http(s)://servidor[:porta].") from None
    return valor


@dataclass
class SSWSettings:
    base_url: str = field(default_factory=lambda: os.getenv("SSW_BASE_URL", DEFAULT_SSW_BASE_URL))
    dominio: str = os.getenv("SSW_DOMINIO", "")
    cpf: str = os.getenv("SSW_CPF", "")
    usuario: str = os.getenv("SSW_USUARIO", "")
    senha: str = os.getenv("SSW_SENHA", "")
    unidade: str = os.getenv("SSW_UNIDADE", "CWB")
    timeout: int = int(os.getenv("SSW_TIMEOUT", "60"))

    def validate(self, required_login, bool = True) -> None:
        required = {
            "SSW_BASE_URL": self.base_url,
        }

        if required_login:
            required.update({
            "SSW_DOMINIO": self.dominio,
            "SSW_CPF": self.cpf,
            "SSW_USUARIO": self.usuario,
            "SSW_SENHA": self.senha,
            "SSW_UNIDADE": self.unidade,
        })

        missing = [key for key, value in required.items() if not value]

        if missing:
            raise ValueError(f"Variáveis obrigatórias ausentes: {', '.join(missing)}")


settings = SSWSettings()
