import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class SSWSettings:
    base_url: str = os.getenv("SSW_BASE_URL", "https://sistema.ssw.inf.br")
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