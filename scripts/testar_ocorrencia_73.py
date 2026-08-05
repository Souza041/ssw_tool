import os
import sys

from pathlib import Path
from pprint import pprint

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(
    PROJECT_ROOT / ".env",
    override=True,
)


from modules.ocorrencia_73.service import (
    Ocorrencia73Service,
)


def env_bool(
    nome: str,
    padrao: bool = False,
) -> bool:
    valor = os.getenv(nome)

    if valor is None:
        return padrao

    return valor.strip().lower() in {
        "1",
        "true",
        "yes",
        "sim",
        "on",
    }


def main() -> int:
    if not env_bool(
        "OCORRENCIA_73_ENABLED",
        False,
    ):
        print(
            "[OCORRENCIA 73] Execução ignorada: "
            "OCORRENCIA_73_ENABLED != true.",
            flush=True,
        )
        return 0

    print(
        "[OCORRENCIA 73] Iniciando execução.",
        flush=True,
    )

    service = Ocorrencia73Service()

    resultado = service.executar(
        triggered_by="cron",
    )

    pprint(resultado)

    print(
        "[OCORRENCIA 73] Execução finalizada.",
        flush=True,
    )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as erro:
        print(
            f"[OCORRENCIA 73] Falha: {erro}",
            file=sys.stderr,
            flush=True,
        )
        raise