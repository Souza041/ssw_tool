from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import json
import os
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Callable, Iterator

if TYPE_CHECKING:
    from modules.documentos.config import DocumentosSettings


@dataclass(frozen=True)
class PipelinePeriod:
    start: date
    end: date


def resolve_period(
    reference_date: date,
    overlap_days: int,
    period_start: date | None = None,
    period_end: date | None = None,
) -> PipelinePeriod:
    if (period_start is None) != (period_end is None):
        raise ValueError("Informe data inicial e final juntas.")
    if period_start is not None and period_end is not None:
        if period_end < period_start:
            raise ValueError("A data final não pode ser anterior à inicial.")
        return PipelinePeriod(period_start, period_end)

    days = max(1, overlap_days)
    return PipelinePeriod(reference_date - timedelta(days=days - 1), reference_date)


@contextmanager
def execution_lock(name: str = "documentos_gce_diario.lock") -> Iterator[None]:
    """Impede duas execuções simultâneas no Windows e no Linux."""
    lock_path = Path(tempfile.gettempdir()) / name
    handle = lock_path.open("a+b")
    handle.seek(0)
    if handle.read(1) == b"":
        handle.seek(0)
        handle.write(b"0")
    handle.flush()
    handle.seek(0)
    locked = False

    try:
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("A rotina diária de documentos já está em execução.") from exc
            locked = True
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("A rotina diária de documentos já está em execução.") from exc
            locked = True

        yield
    finally:
        try:
            if locked:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class DailyDocumentsPipeline:
    def __init__(
        self,
        config: "DocumentosSettings | None" = None,
        progress: Callable[[str], None] = print,
    ) -> None:
        if config is None:
            from modules.documentos.config import settings

            config = settings
        self.config = config
        self.progress = progress

    def _log(self, message: str) -> None:
        self.progress(message)

    def run(
        self,
        *,
        reference_date: date | None = None,
        period_start: date | None = None,
        period_end: date | None = None,
        output_root: Path = Path("data/documentos"),
        send_notifications: bool = False,
        preview_notifications: bool = False,
        notification_limit: int | None = None,
    ) -> dict:
        from modules.documentos.collectors.portal import GCEPortalCollector
        from modules.documentos.collectors.ssw import SSWDocumentsCollector
        from modules.documentos.service import DocumentImportService
        from modules.documentos.services.alerts import refresh_alerts
        from modules.documentos.services.matching import reprocessar
        from modules.documentos.services.notifications import process_notifications
        from modules.documentos.metrics_repository import save_daily_snapshot
        from modules.documentos.pipeline_repository import (
            create_pipeline_run,
            update_step,
            finish_pipeline_run,
            fail_pipeline_run,
        )

        reference_date = reference_date or date.today()
        period = resolve_period(
            reference_date,
            self.config.daily_overlap_days,
            period_start,
            period_end,
        )

        day_dir = Path(output_root) / reference_date.strftime("%Y/%m/%d")

        run_id: int | None = None
        current_step = "INITIALIZATION"

        with execution_lock():
            run_id = create_pipeline_run(
                reference_date=reference_date,
                period_start=period.start,
                period_end=period.end,
                triggered_by="MANUAL",
            )

            self._log(f"[MONITORAMENTO] Execução #{run_id} registrada.")

            try:
                # ---------------------------------------------------------
                # 1. PORTAL GCE
                # ---------------------------------------------------------
                current_step = "PORTAL"
                update_step(run_id, "portal", "RUNNING")

                self._log("[1/6] Baixando relatórios do Portal GCE...")

                portal = GCEPortalCollector(self.config).download_daily(
                    Path(output_root),
                    reference_date,
                )

                update_step(run_id, "portal", "SUCCESS")

                # ---------------------------------------------------------
                # 2. SSW - OP455 / OP930
                # ---------------------------------------------------------
                current_step = "SSW"
                update_step(run_id, "ssw", "RUNNING")

                self._log(
                    f"[2/6] Baixando OP455 e OP930 de "
                    f"{period.start.isoformat()} até {period.end.isoformat()}..."
                )

                ssw = SSWDocumentsCollector(self.config).download(
                    day_dir / "ssw",
                    period.start,
                    period.end,
                )

                update_step(run_id, "ssw", "SUCCESS")

                # ---------------------------------------------------------
                # 3. IMPORTAÇÃO
                # ---------------------------------------------------------
                current_step = "IMPORT"
                update_step(run_id, "import", "RUNNING")

                self._log("[3/6] Importando bases e atualizando cruzamentos...")

                imported = DocumentImportService(
                    commit_every=500,
                    target_year=self.config.target_year,
                    database_retries=4,
                ).import_month(
                    portal_solucionar=portal.solucionar,
                    portal_pendencias=portal.aguardando_solucao,
                    op455=ssw.op455,
                    op930=ssw.op930,
                    period_start=period.start,
                    period_end=period.end,
                    progress=self._log,
                )

                update_step(run_id, "import", "SUCCESS")

                # ---------------------------------------------------------
                # 4. MATCHING
                # ---------------------------------------------------------
                current_step = "MATCHING"
                update_step(run_id, "matching", "RUNNING")

                self._log(
                    "[4/6] Consolidando vínculos NOT_FOUND/AMBIGUOUS "
                    "com o histórico completo..."
                )

                matching = reprocessar(
                    period.start,
                    period.end,
                    progress=self._log,
                )

                matching_summary = {
                    "success": matching.get("success", True),
                    "period": matching.get(
                        "period",
                        {
                            "start": period.start.isoformat(),
                            "end": period.end.isoformat(),
                        },
                    ),
                    "total": matching.get("total", 0),
                    "matched": matching.get("matched", 0),
                    "ambiguous": matching.get("ambiguous", 0),
                    "not_found": matching.get("not_found", 0),
                    "errors": matching.get("errors", 0),
                }

                self._log(
                    "[MATCHING] "
                    f"{matching_summary['total']} analisados | "
                    f"{matching_summary['matched']} MATCHED | "
                    f"{matching_summary['ambiguous']} AMBIGUOUS | "
                    f"{matching_summary['not_found']} NOT_FOUND | "
                    f"{matching_summary['errors']} ERROS"
                )

                if matching_summary["errors"] > 0:
                    raise RuntimeError(
                        f"Matching finalizado com "
                        f"{matching_summary['errors']} erro(s)."
                    )

                update_step(run_id, "matching", "SUCCESS")

                # ---------------------------------------------------------
                # 5. ALERTAS
                # ---------------------------------------------------------
                current_step = "ALERTS"
                update_step(run_id, "alerts", "RUNNING")

                self._log("[5/6] Recalculando alertas...")

                alerts = refresh_alerts(reference_date)

                # ---------------------------------------------------------
                # SNAPSHOT DIÁRIO DE MÉTRICAS
                # ---------------------------------------------------------

                self._log(
                    f"[MÉTRICAS] Gravando snapshot diário de "
                    f"{reference_date.isoformat()}..."
                )

                metrics = save_daily_snapshot(reference_date)

                self._log(
                    "[MÉTRICAS] "
                    f"{metrics['monitored']} monitorados | "
                    f"{metrics['overdue']} vencidos | "
                    f"{metrics['alert_5']} até 5 dias | "
                    f"{metrics['alert_10']} até 10 dias | "
                    f"{metrics['alert_20']} até 20 dias | "
                    f"{metrics['without_delivery']} sem entrega"
                )

                update_step(run_id, "alerts", "SUCCESS")

                # ---------------------------------------------------------
                # 6. NOTIFICAÇÕES
                # ---------------------------------------------------------
                current_step = "NOTIFICATIONS"

                notifications = {
                    "mode": "SKIPPED",
                    "candidates": 0,
                    "sent": 0,
                    "preview": 0,
                    "skipped": 0,
                    "errors": 0,
                }

                if send_notifications or preview_notifications:
                    update_step(run_id, "notifications", "RUNNING")

                    mode = "SEND" if send_notifications else "PREVIEW"

                    self._log(
                        f"[6/6] Processando notificações em modo {mode}..."
                    )

                    notification_stats = process_notifications(
                        send=send_notifications,
                        limit=notification_limit,
                    )

                    notifications = {
                        "mode": mode,
                        **notification_stats,
                    }

                    if notifications.get("errors", 0) > 0:
                        raise RuntimeError(
                            f"Processamento de notificações finalizado com "
                            f"{notifications['errors']} erro(s)."
                        )

                    update_step(run_id, "notifications", "SUCCESS")

                else:
                    self._log(
                        "[6/6] Notificações não solicitadas; etapa ignorada."
                    )

                    update_step(
                        run_id,
                        "notifications",
                        "SKIPPED",
                    )

                # ---------------------------------------------------------
                # RESULTADO
                # ---------------------------------------------------------
                result = {
                    "success": True,
                    "reference_date": reference_date.isoformat(),
                    "period": {
                        "start": period.start.isoformat(),
                        "end": period.end.isoformat(),
                    },
                    "files": {
                        "portal_solucionar": str(portal.solucionar),
                        "portal_aguardando_solucao": str(
                            portal.aguardando_solucao
                        ),
                        "op455": str(ssw.op455),
                        "op930": str(ssw.op930),
                    },
                    "import": imported,
                    "matching": matching_summary,
                    "alerts": asdict(alerts),
                    "metrics": {
                        "snapshot_date": reference_date.isoformat(),
                        **metrics,
                    },
                    "notifications": notifications,
                    "pipeline_run_id": run_id,
                }

                # ---------------------------------------------------------
                # FINALIZA EXECUÇÃO
                # ---------------------------------------------------------
                finish_pipeline_run(
                    run_id,
                    matches={
                        "MATCHED": matching_summary["matched"],
                        "AMBIGUOUS": matching_summary["ambiguous"],
                        "NOT_FOUND": matching_summary["not_found"],
                    },
                    alerts=asdict(alerts),
                    notifications=notifications,
                )

                self._log(
                    f"[MONITORAMENTO] Execução #{run_id} finalizada com sucesso."
                )

                self._log(
                    "Rotina diária de Documentos GCE concluída com sucesso."
                )

                return result

            except Exception as exc:
                # Marca também a etapa específica como ERROR.
                step_map = {
                    "PORTAL": "portal",
                    "SSW": "ssw",
                    "IMPORT": "import",
                    "MATCHING": "matching",
                    "ALERTS": "alerts",
                    "NOTIFICATIONS": "notifications",
                }

                repository_step = step_map.get(current_step)

                if repository_step is not None:
                    try:
                        update_step(
                            run_id,
                            repository_step,
                            "ERROR",
                        )
                    except Exception:
                        pass

                # Registra o erro geral da execução.
                try:
                    fail_pipeline_run(
                        run_id,
                        step=current_step,
                        error=exc,
                    )
                except Exception:
                    pass

                self._log(
                    f"[MONITORAMENTO] Execução #{run_id} falhou "
                    f"na etapa {current_step}: {exc}"
                )

                # IMPORTANTÍSSIMO:
                # não engolimos o erro.
                raise


def dump_result(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)
