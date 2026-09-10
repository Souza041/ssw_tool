from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.documentos.service import DocumentImportService


def parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use o formato AAAA-MM-DD.") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa um mês do acompanhamento documental.")
    parser.add_argument("--portal-solucionar", required=True, type=Path)
    parser.add_argument("--portal-pendencias", required=True, type=Path)
    parser.add_argument("--op455", required=True, type=Path)
    parser.add_argument("--op930", required=True, type=Path)
    parser.add_argument("--data-inicial", required=True, type=parse_date)
    parser.add_argument("--data-final", required=True, type=parse_date)
    parser.add_argument("--ano-portal", type=int, default=2026)
    parser.add_argument("--commit-every", type=int, default=500)
    parser.add_argument("--database-retries", type=int, default=4)
    args = parser.parse_args()

    if args.data_final < args.data_inicial:
        parser.error("A data final não pode ser anterior à data inicial.")

    service = DocumentImportService(
        commit_every=args.commit_every,
        target_year=args.ano_portal,
        database_retries=args.database_retries,
    )
    result = service.import_month(
        portal_solucionar=args.portal_solucionar,
        portal_pendencias=args.portal_pendencias,
        op455=args.op455,
        op930=args.op930,
        period_start=args.data_inicial,
        period_end=args.data_final,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
