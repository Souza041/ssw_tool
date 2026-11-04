
"""Consulta historica de incidentes por CTRC ou NF."""

import argparse
import json

from modules.incidentes.historico import (
    HistoricoInvalido,
    consultar_historico,
)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--termo",
        required=True,
        help="CTRC ou numero da nota fiscal",
    )

    parser.add_argument(
        "--tipo",
        choices=("auto", "ctrc", "nf"),
        default="auto",
    )

    parser.add_argument(
        "--limite",
        type=int,
        default=500,
        help="Limite de linhas brutas",
    )

    args = parser.parse_args()

    try:
        resultado = consultar_historico(
            args.termo,
            tipo=args.tipo,
            limite=args.limite,
        )

    except HistoricoInvalido as exc:
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
