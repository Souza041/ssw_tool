from pprint import pprint

from modules.ocorrencia_73.service import (
    Ocorrencia73Service,
)


def main():
    service = Ocorrencia73Service()

    resultado = service.executar(
        triggered_by="cli",
    )

    pprint(resultado)


if __name__ == "__main__":
    main()