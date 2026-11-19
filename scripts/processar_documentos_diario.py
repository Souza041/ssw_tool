from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.documentos.execution import execute_daily_documents
from modules.documentos.services.daily_pipeline import dump_result
from modules.local_agent.delegation import (
    delegation_enabled,
    enqueue_ssw_task,
    scheduled_key,
)


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

    private_env = Path(
        os.getenv("NEXUS_LOCAL_AGENT_ENV_FILE", "/etc/nexus/local-agent.env")
    )
    if private_env.is_file():
        from modules.local_agent.private_env import load_private_environment

        load_private_environment(private_env, project_root=PROJECT_ROOT)

    if (args.data_inicial is None) != (args.data_final is None):
        parser.error("Use --data-inicial e --data-final juntas.")

    if delegation_enabled():
        reference = args.data_referencia or datetime.now(
            ZoneInfo("America/Sao_Paulo")
        ).date()
        payload = {
            "origin": "SCHEDULED",
            "reference_date": reference.isoformat(),
            "period_start": args.data_inicial.isoformat() if args.data_inicial else None,
            "period_end": args.data_final.isoformat() if args.data_final else None,
            "send_notifications": args.enviar_notificacoes,
            "preview_notifications": args.prever_notificacoes,
            "notification_limit": args.limite_notificacoes,
        }
        job_id = enqueue_ssw_task(
            "ssw.documentos_gce",
            payload,
            idempotency_key=scheduled_key("ssw.documentos_gce", reference.isoformat()),
            concurrency_key="ssw.documentos_gce",
            priority=50,
            coalesce_active=True,
        )
        print(dump_result({"success": True, "delegated": True, "job_id": job_id}))
        return

    result = execute_daily_documents(
        triggered_by="SCHEDULED",
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
