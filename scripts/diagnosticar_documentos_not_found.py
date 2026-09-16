from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.documentos.services.not_found_diagnostic import (
    diagnose_not_found,
    export_diagnostics_csv,
)


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use o formato AAAA-MM-DD.") from exc


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnostica documentos NOT_FOUND sem alterar o banco."
    )
    parser.add_argument("--inicio", required=True, type=parse_iso_date)
    parser.add_argument("--fim", required=True, type=parse_iso_date)
    parser.add_argument("--saida", type=Path)
    args = parser.parse_args()

    output = args.saida or (
        Path("data/documentos/diagnosticos")
        / f"not_found_{args.inicio.isoformat()}_{args.fim.isoformat()}.csv"
    )
    rows = diagnose_not_found(args.inicio, args.fim)
    summary = export_diagnostics_csv(rows, output)
    print(json.dumps({"success": True, **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
