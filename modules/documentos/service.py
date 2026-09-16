from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
import time
from typing import Callable, Iterable, TypeVar

from modules.documentos.database import get_connection, transaction
from modules.documentos.parsers.op455 import iter_op455
from modules.documentos.parsers.op930 import iter_op930
from modules.documentos.parsers.portal import iter_portal
from modules.documentos.repository import DocumentRepository
from modules.documentos.schemas import OP455Document, OP930Occurrence, PortalDocument
from modules.documentos.services.matcher import DocumentMatcher


T = TypeVar("T")


@dataclass
class ImportStats:
    source: str
    import_id: int
    total: int = 0
    inserted: int = 0
    updated: int = 0
    rejected: int = 0


class DocumentImportService:
    def __init__(
        self,
        commit_every: int = 500,
        target_year: int = 2026,
        database_retries: int = 4,
    ) -> None:
        self.commit_every = max(1, commit_every)
        self.target_year = target_year
        self.database_retries = max(1, database_retries)
        self.repository = DocumentRepository()

    @staticmethod
    def _close_connection(connection) -> None:
        if connection is None:
            return
        try:
            connection.close()
        except Exception:
            pass

    @staticmethod
    def _rollback_connection(connection) -> None:
        if connection is None:
            return
        try:
            connection.rollback()
        except Exception:
            # A conexão pode já ter sido derrubada pelo servidor.
            pass

    @staticmethod
    def _retryable_database_error(error: Exception) -> bool:
        code = error.args[0] if getattr(error, "args", None) else None
        return code in {0, 2006, 2013} or error.__class__.__name__ in {
            "InterfaceError",
        }

    def _save_batch(
        self,
        *,
        source: str,
        records: list[T],
        save: Callable,
        import_id: int,
        progress: Callable[[str], None] | None,
    ) -> tuple[int, int, dict[int, int]]:
        last_error: Exception | None = None

        for attempt in range(1, self.database_retries + 1):
            connection = None
            try:
                connection = get_connection(autocommit=False)
                inserted = 0
                updated = 0
                database_ids: dict[int, int] = {}

                for record in records:
                    row_id, was_inserted = save(connection, record, import_id)
                    database_ids[id(record)] = row_id
                    if was_inserted:
                        inserted += 1
                    else:
                        updated += 1

                connection.commit()
                return inserted, updated, database_ids

            except Exception as exc:
                last_error = exc
                self._rollback_connection(connection)

                if (
                    not self._retryable_database_error(exc)
                    or attempt >= self.database_retries
                ):
                    raise

                wait_seconds = min(2 ** (attempt - 1), 8)
                if progress:
                    progress(
                        f"{source}: conexão perdida no lote. "
                        f"Nova tentativa {attempt + 1}/{self.database_retries} "
                        f"em {wait_seconds}s..."
                    )
                time.sleep(wait_seconds)

            finally:
                self._close_connection(connection)

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{source}: não foi possível salvar o lote.")

    def _start_import(
        self,
        source: str,
        file_path: Path,
        period_start: date | None,
        period_end: date | None,
    ) -> int:
        with transaction() as connection:
            return self.repository.create_import(
                connection,
                source=source,
                file_path=file_path,
                period_start=period_start,
                period_end=period_end,
            )

    def _finish_import(self, stats: ImportStats) -> None:
        with transaction() as connection:
            self.repository.finish_import(
                connection,
                stats.import_id,
                total_rows=stats.total,
                inserted_rows=stats.inserted,
                updated_rows=stats.updated,
                rejected_rows=stats.rejected,
            )

    def _fail_import(self, import_id: int, error: Exception) -> None:
        with transaction() as connection:
            self.repository.fail_import(connection, import_id, error)

    def _persist_stream(
        self,
        *,
        source: str,
        file_path: Path,
        records: Iterable[T],
        save: Callable,
        period_start: date | None,
        period_end: date | None,
        accept: Callable[[T], bool] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> tuple[ImportStats, list[T], dict[int, int]]:
        import_id = self._start_import(source, file_path, period_start, period_end)
        stats = ImportStats(source=source, import_id=import_id)
        accepted: list[T] = []
        database_ids: dict[int, int] = {}
        batch: list[T] = []

        try:
            for record in records:
                stats.total += 1
                if accept is not None and not accept(record):
                    stats.rejected += 1
                    continue

                accepted.append(record)
                batch.append(record)

                if len(batch) >= self.commit_every:
                    inserted, updated, batch_ids = self._save_batch(
                        source=source,
                        records=batch,
                        save=save,
                        import_id=import_id,
                        progress=progress,
                    )
                    stats.inserted += inserted
                    stats.updated += updated
                    database_ids.update(batch_ids)
                    batch.clear()
                    if progress:
                        progress(
                            f"{source}: {stats.total:,} lidos | "
                            f"{stats.inserted:,} inseridos | {stats.updated:,} atualizados"
                        )

            if batch:
                inserted, updated, batch_ids = self._save_batch(
                    source=source,
                    records=batch,
                    save=save,
                    import_id=import_id,
                    progress=progress,
                )
                stats.inserted += inserted
                stats.updated += updated
                database_ids.update(batch_ids)
                batch.clear()

            self._finish_import(stats)
            return stats, accepted, database_ids

        except Exception as exc:
            try:
                self._fail_import(import_id, exc)
            except Exception as status_error:
                if progress:
                    progress(
                        f"{source}: falha ao registrar o erro da importação: "
                        f"{status_error}"
                    )
            raise exc

    def import_month(
        self,
        *,
        portal_solucionar: Path,
        portal_pendencias: Path,
        op455: Path,
        op930: Path,
        period_start: date,
        period_end: date,
        progress: Callable[[str], None] | None = print,
    ) -> dict:
        files = [portal_solucionar, portal_pendencias, op455, op930]
        missing = [str(path) for path in files if not Path(path).is_file()]
        if missing:
            raise FileNotFoundError("Arquivos não encontrados: " + ", ".join(missing))

        stats_455, documents, document_ids = self._persist_stream(
            source="OP455",
            file_path=Path(op455),
            records=iter_op455(Path(op455)),
            save=self.repository.upsert_ssw_document,
            period_start=period_start,
            period_end=period_end,
            progress=progress,
        )

        stats_930, occurrences, _ = self._persist_stream(
            source="OP930",
            file_path=Path(op930),
            records=iter_op930(Path(op930)),
            save=self.repository.upsert_occurrence,
            period_start=period_start,
            period_end=period_end,
            progress=progress,
        )

        def portal_2026(record: PortalDocument) -> bool:
            return record.issue_date is not None and record.issue_date.year == self.target_year

        stats_solucionar, solucionar, solucionar_ids = self._persist_stream(
            source="PORTAL_SOLUCIONAR",
            file_path=Path(portal_solucionar),
            records=iter_portal(Path(portal_solucionar), "SOLUCIONAR"),
            save=self.repository.upsert_portal_document,
            period_start=date(self.target_year, 1, 1),
            period_end=date(self.target_year, 12, 31),
            accept=portal_2026,
            progress=progress,
        )

        stats_pendencias, pendencias, pendencias_ids = self._persist_stream(
            source="PORTAL_AGUARDANDO_SOLUCAO",
            file_path=Path(portal_pendencias),
            records=iter_portal(Path(portal_pendencias), "AGUARDANDO_SOLUCAO"),
            save=self.repository.upsert_portal_document,
            period_start=date(self.target_year, 1, 1),
            period_end=date(self.target_year, 12, 31),
            accept=portal_2026,
            progress=progress,
        )

        matcher = DocumentMatcher(documents, occurrences)
        match_counts = {"MATCHED": 0, "AMBIGUOUS": 0, "NOT_FOUND": 0}
        portal_records = [
            record
            for record in solucionar + pendencias
            if record.issue_date is not None
            and period_start <= record.issue_date <= period_end
        ]
        portal_ids = {**solucionar_ids, **pendencias_ids}

        with transaction() as connection:
            for portal_record in portal_records:
                result = matcher.match(portal_record)

                status = result.status
                score = result.score
                candidate_count = result.candidate_count

                ssw_document_id = (
                    document_ids.get(id(result.op455))
                    if result.op455 is not None
                    else None
                )

                # Fallback histórico:
                # Portal NF -> OP930 histórica -> CTRC -> OP455 histórico
                if status == "NOT_FOUND":
                    ctrcs = self.repository.find_occurrence_ctrcs_by_invoice(
                        connection,
                        portal_record.invoice_number,
                    )

                    ctrcs = list(dict.fromkeys(ctrcs))

                    if len(ctrcs) == 1:
                        document_ids_historicos = (
                            self.repository.find_document_ids_by_ctrc(
                                connection,
                                ctrcs[0],
                            )
                        )

                        if len(document_ids_historicos) == 1:
                            status = "MATCHED"
                            score = 1
                            candidate_count = 1
                            ssw_document_id = document_ids_historicos[0]

                        elif len(document_ids_historicos) > 1:
                            status = "AMBIGUOUS"
                            score = 0
                            candidate_count = len(document_ids_historicos)

                    elif len(ctrcs) > 1:
                        status = "AMBIGUOUS"
                        score = 0
                        candidate_count = len(ctrcs)

                match_counts[status] = (
                    match_counts.get(status, 0) + 1
                )

                self.repository.upsert_match(
                    connection,
                    portal_document_id=portal_ids[id(portal_record)],
                    ssw_document_id=ssw_document_id,
                    status=status,
                    score=score,
                    candidate_count=candidate_count,
                )

        with transaction() as connection:
            database_counts = self.repository.counts(connection)

        result = {
            "success": True,
            "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
            "imports": {
                stats.source: asdict(stats)
                for stats in (
                    stats_455,
                    stats_930,
                    stats_solucionar,
                    stats_pendencias,
                )
            },
            "matches": match_counts,
            "database": database_counts,
        }
        if progress:
            progress("Importação e cruzamento concluídos com sucesso.")
        return result
