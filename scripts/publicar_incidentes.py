
"""
Publicação controlada de importações de incidentes.
"""
import argparse
import json

from modules.incidentes.persistence import (
    ImportacaoInvalida,
    publicar_importacao,
)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--importacao-id",
        type=int,
        required=True,
        help="ID da importação validada",
    )

    parser.add_argument(
        "--aplicar",
        action="store_true",
        help=(
            "Efetiva a publicação. "
            "Sem essa opção, apenas simula."
        ),
    )

    args = parser.parse_args()

    try:
        resultado = publicar_importacao(
            args.importacao_id,
            dry_run=not args.aplicar,
        )

    except ImportacaoInvalida as exc:
        parser.exit(
            2,
            f"Publicação recusada: {exc}\n",
        )

    print(
        json.dumps(
            resultado,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
