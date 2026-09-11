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
        from modules.documentos.services.notifications import process_notifications

        reference_date = reference_date or date.today()
        period = resolve_period(
            reference_date,
            self.config.daily_overlap_days,
            period_start,
            period_end,
        )
        day_dir = Path(output_root) / reference_date.strftime("%Y/%m/%d")

        with execution_lock():
            self._log("[1/5] Baixando relatórios do Portal GCE...")
            portal = GCEPortalCollector(self.config).download_daily(
                Path(output_root), reference_date
            )

            self._log(
                f"[2/5] Baixando OP455 e OP930 de "
                f"{period.start.isoformat()} até {period.end.isoformat()}..."
            )
            ssw = SSWDocumentsCollector(self.config).download(
                day_dir / "ssw", period.start, period.end
            )

            self._log("[3/5] Importando bases e atualizando cruzamentos...")
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

            self._log("[4/5] Recalculando alertas...")
            alerts = refresh_alerts(reference_date)

            notifications = {
                "mode": "SKIPPED",
                "candidates": 0,
                "sent": 0,
                "preview": 0,
                "skipped": 0,
                "errors": 0,
            }
            if send_notifications or preview_notifications:
                mode = "SEND" if send_notifications else "PREVIEW"
                self._log(f"[5/5] Processando notificações em modo {mode}...")
                notification_stats = process_notifications(
                    send=send_notifications,
                    limit=notification_limit,
                )
                notifications = {"mode": mode, **notification_stats}
            else:
                self._log("[5/5] Notificações não solicitadas; etapa ignorada.")

            result = {
                "success": True,
                "reference_date": reference_date.isoformat(),
                "period": {
                    "start": period.start.isoformat(),
                    "end": period.end.isoformat(),
                },
                "files": {
                    "portal_solucionar": str(portal.solucionar),
                    "portal_aguardando_solucao": str(portal.aguardando_solucao),
                    "op455": str(ssw.op455),
                    "op930": str(ssw.op930),
                },
                "import": imported,
                "alerts": asdict(alerts),
                "notifications": notifications,
            }
            self._log("Rotina diária de Documentos GCE concluída com sucesso.")
            return result


def dump_result(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)
