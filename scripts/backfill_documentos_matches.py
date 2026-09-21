from __future__ import annotations

import argparse
import json
from datetime import date, datetime

from modules.documentos.services.matching import reprocessar
from modules.documentos.services.alerts import refresh_alerts


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d",
        ).date()

    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Data inválida: {value}. Use YYYY-MM-DD."
        ) from exc


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill de matching histórico "
            "dos documentos do Portal GCE."
        )
    )

    parser.add_argument(
        "--inicio",
        required=True,
        type=parse_date,
    )

    parser.add_argument(
        "--fim",
        required=True,
        type=parse_date,
    )

    args = parser.parse_args()

    if args.inicio > args.fim:
        raise ValueError(
            "A data inicial não pode ser maior que a final."
        )

    print(
        f"\nBACKFILL DOCUMENTAL "
        f"{args.inicio} -> {args.fim}\n"
    )

    resultado = reprocessar(
        inicio=args.inicio,
        fim=args.fim,
        progress=print,
    )

    resumo = {
        key: value
        for key, value in resultado.items()
        if key != "details"
    }

    print("\nMatching concluído:")
    print(
        json.dumps(
            resumo,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )

    if resultado.get("errors", 0):
        print(
            "\nExistem erros no matching. "
            "Alertas não serão recalculados."
        )
        return

    print("\nRecalculando alertas...")

    alertas = refresh_alerts()

    print("\nAlertas recalculados:")
    print(alertas)


if __name__ == "__main__":
    main()