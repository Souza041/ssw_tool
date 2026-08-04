import argparse

from datetime import datetime
from pathlib import Path
from pprint import pprint

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(PROJECT_ROOT / ".env")


from modules.ocorrencia_73.service import (
    Ocorrencia73Service,
)
from operations.op101.ocorrencias import (
    OP101Ocorrencias,
)


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Consulta um CTRC na OP101 sem lançar ocorrência."
        )
    )

    parser.add_argument(
        "--serie",
        required=True,
        help="Série do CTRC, por exemplo JOI.",
    )

    parser.add_argument(
        "--numero",
        required=True,
        help="Número do CTRC.",
    )

    parser.add_argument(
        "--data",
        required=True,
        help="Data da emissão em DDMMAA.",
    )

    return parser


def main() -> None:
    args = criar_parser().parse_args()

    service = Ocorrencia73Service()
    client = service.criar_client_logado()

    op101 = OP101Ocorrencias(client)

    resultado = op101.consultar_ctrc(
        serie=args.serie,
        numero=args.numero,
        data_referencia=args.data,
    )

    pprint(resultado.to_dict())


if __name__ == "__main__":
    main()