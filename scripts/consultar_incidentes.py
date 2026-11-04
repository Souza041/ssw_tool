
"""Consulta operacional de incidentes por CTRC ou NF."""
import argparse
import json

from modules.incidentes.consulta import (
    ConsultaInvalida,
    consultar_incidentes,
)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--termo",
        required=True,
        help="Número do CTRC ou da nota fiscal",
    )

    parser.add_argument(
        "--tipo",
        choices=("auto", "ctrc", "nf"),
        default="auto",
        help="Tipo de pesquisa (padrão: auto)",
    )

    parser.add_argument(
        "--limite",
        type=int,
        default=100,
        help="Máximo de linhas retornadas",
    )

    args = parser.parse_args()

    try:
        resultado = consultar_incidentes(
            args.termo,
            tipo=args.tipo,
            limite=args.limite,
        )

    except ConsultaInvalida as exc:
        parser.exit(
            2,
            f"Consulta recusada: {exc}\n",
        )

    print(
        json.dumps(
            resultado,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
