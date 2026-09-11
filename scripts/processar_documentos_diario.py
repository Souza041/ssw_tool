from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.documentos.services.daily_pipeline import DailyDocumentsPipeline, dump_result


def parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use o formato AAAA-MM-DD.") from exc


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Baixa, importa e cruza diariamente as bases do Documentos GCE."
    )
    parser.add_argument("--data-referencia", type=parse_date)
    parser.add_argument("--data-inicial", type=parse_date)
    parser.add_argument("--data-final", type=parse_date)
    parser.add_argument("--output-root", type=Path, default=Path("data/documentos"))
    notification_mode = parser.add_mutually_exclusive_group()
    notification_mode.add_argument(
        "--enviar-notificacoes",
        action="store_true",
        help="Envia o lote de e-mails após atualizar os alertas.",
    )
    notification_mode.add_argument(
        "--prever-notificacoes",
        action="store_true",
        help="Grava somente a prévia do lote de notificações.",
    )
    parser.add_argument("--limite-notificacoes", type=int)
    args = parser.parse_args()

    if (args.data_inicial is None) != (args.data_final is None):
        parser.error("Use --data-inicial e --data-final juntas.")

    result = DailyDocumentsPipeline().run(
        reference_date=args.data_referencia,
        period_start=args.data_inicial,
        period_end=args.data_final,
        output_root=args.output_root,
        send_notifications=args.enviar_notificacoes,
        preview_notifications=args.prever_notificacoes,
        notification_limit=args.limite_notificacoes,
    )
    print(dump_result(result))


if __name__ == "__main__":
    main()
