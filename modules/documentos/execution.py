"""Ponto único de entrada operacional para execuções manuais e agendadas."""

from datetime import date
from pathlib import Path
from typing import Callable

from modules.documentos.health import BOT, classify_result
from modules.documentos.services.daily_pipeline import (
    DailyDocumentsPipeline,
    ExecutionLease,
)
from modules.health_center.monitor import run_monitored


def execute_daily_documents(
    *,
    triggered_by: str,
    progress: Callable[[str], None] = print,
    reference_date: date | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    output_root: Path = Path("data/documentos"),
    send_notifications: bool = False,
    preview_notifications: bool = False,
    notification_limit: int | None = None,
    execution_lease: ExecutionLease | None = None,
    ssw_download=None,
) -> dict:
    """Executa o pipeline existente uma única vez, com auditoria opcional."""
    origin = str(triggered_by or "MANUAL").strip().upper()
    if origin not in {"MANUAL", "SCHEDULED"}:
        raise ValueError("Origem da execução inválida.")

    pipeline_options = {"progress": progress}
    if ssw_download is not None:
        pipeline_options["ssw_download"] = ssw_download
    pipeline = DailyDocumentsPipeline(**pipeline_options)
    return run_monitored(
        bot=BOT,
        origin=origin.lower(),
        operation=lambda: pipeline.run(
            reference_date=reference_date,
            period_start=period_start,
            period_end=period_end,
            output_root=output_root,
            send_notifications=send_notifications,
            preview_notifications=preview_notifications,
            notification_limit=notification_limit,
            triggered_by=origin,
            execution_lease=execution_lease,
        ),
        classify=classify_result,
    )
