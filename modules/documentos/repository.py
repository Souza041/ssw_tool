from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any

from pymysql.connections import Connection

from modules.documentos.schemas import OP455Document, OP930Occurrence, PortalDocument


def sha256_file(file_path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(file_path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_values(*values: object) -> str:
    payload = "|".join("" if value is None else str(value) for value in values)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DocumentRepository:
    @staticmethod
    def find_import(connection: Connection, source: str, file_hash: str) -> dict | None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM document_imports
                WHERE source = %s AND file_hash = %s
                LIMIT 1
                """,
                (source, file_hash),
            )
            return cursor.fetchone()

    @staticmethod
    def create_import(
        connection: Connection,
        *,
        source: str,
        file_path: Path,
        period_start: date | None,
        period_end: date | None,
    ) -> int:
        file_path = Path(file_path)
        file_hash = sha256_file(file_path)
        existing = DocumentRepository.find_import(connection, source, file_hash)
        if existing:
            return int(existing["id"])

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO document_imports (
                    source, period_start, period_end, file_name,
                    file_path, file_hash, status
                ) VALUES (%s, %s, %s, %s, %s, %s, 'RUNNING')
                """,
                (
                    source,
                    period_start,
                    period_end,
                    file_path.name,
                    str(file_path),
                    file_hash,
                ),
            )
            return int(cursor.lastrowid)

    @staticmethod
    def finish_import(
        connection: Connection,
        import_id: int,
        *,
        total_rows: int,
        inserted_rows: int,
        updated_rows: int,
        rejected_rows: int,
    ) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE document_imports
                SET status = 'SUCCESS', total_rows = %s, inserted_rows = %s,
                    updated_rows = %s, rejected_rows = %s,
                    error_message = NULL, finished_at = NOW()
                WHERE id = %s
                """,
                (total_rows, inserted_rows, updated_rows, rejected_rows, import_id),
            )

    @staticmethod
    def fail_import(connection: Connection, import_id: int, error: Exception | str) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE document_imports
                SET status = 'ERROR', error_message = %s, finished_at = NOW()
                WHERE id = %s
                """,
                (str(error), import_id),
            )

    @staticmethod
    def upsert_ssw_document(
        connection: Connection,
        document: OP455Document,
        import_id: int,
    ) -> tuple[int, bool]:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM ssw_documents WHERE ctrc_raw = %s", (document.ctrc_raw,))
            existing = cursor.fetchone()
            cursor.execute(
                """
                INSERT INTO ssw_documents (
                    ctrc, ctrc_raw, cte_number, document_type, issue_date,
                    issuing_unit, receiving_unit, payer_cnpj, payer_name,
                    recipient_cnpj, recipient_name, recipient_state,
                    remittance_cover_number, archive_package_number,
                    scanned, scanned_at, origin_ctrc, expedition_map,
                    reception_map, volumes, customer_shipment, source_import_id
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON DUPLICATE KEY UPDATE
                    id = LAST_INSERT_ID(id), cte_number = VALUES(cte_number),
                    document_type = VALUES(document_type), issue_date = VALUES(issue_date),
                    issuing_unit = VALUES(issuing_unit), receiving_unit = VALUES(receiving_unit),
                    payer_cnpj = VALUES(payer_cnpj), payer_name = VALUES(payer_name),
                    recipient_cnpj = VALUES(recipient_cnpj), recipient_name = VALUES(recipient_name),
                    recipient_state = VALUES(recipient_state),
                    remittance_cover_number = VALUES(remittance_cover_number),
                    archive_package_number = VALUES(archive_package_number),
                    scanned = VALUES(scanned), scanned_at = VALUES(scanned_at),
                    origin_ctrc = VALUES(origin_ctrc), expedition_map = VALUES(expedition_map),
                    reception_map = VALUES(reception_map), volumes = VALUES(volumes),
                    customer_shipment = VALUES(customer_shipment),
                    source_import_id = VALUES(source_import_id), last_seen_at = NOW()
                """,
                (
                    document.ctrc, document.ctrc_raw, document.cte_number,
                    document.document_type, document.issue_date,
                    document.issuing_unit, document.receiving_unit,
                    document.payer_cnpj, document.payer_name,
                    document.recipient_cnpj, document.recipient_name,
                    document.recipient_state, document.remittance_cover_number,
                    document.archive_package_number, int(document.scanned),
                    document.scanned_at, document.origin_ctrc,
                    document.expedition_map, document.reception_map,
                    document.volumes, document.customer_shipment, import_id,
                ),
            )
            document_id = int(cursor.lastrowid)

        for invoice in document.invoices:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ssw_document_invoices (
                        document_id, invoice_number, invoice_series,
                        access_key, is_primary, source_import_id
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        access_key = VALUES(access_key),
                        is_primary = VALUES(is_primary),
                        source_import_id = VALUES(source_import_id)
                    """,
                    (
                        document_id, invoice.number, invoice.series or "",
                        invoice.access_key, int(invoice.is_primary), import_id,
                    ),
                )

        for order_number in document.order_numbers:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ssw_document_orders (
                        document_id, order_number, source_import_id
                    ) VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE source_import_id = VALUES(source_import_id)
                    """,
                    (document_id, order_number, import_id),
                )

        return document_id, existing is None

    @staticmethod
    def upsert_occurrence(
        connection: Connection,
        occurrence: OP930Occurrence,
        import_id: int,
    ) -> tuple[int, bool]:
        source_hash = _sha256_values(
            occurrence.ctrc_raw,
            occurrence.invoice_number,
            occurrence.invoice_series,
            occurrence.occurrence_code,
            occurrence.occurrence_at,
            occurrence.included_at,
            occurrence.occurrence_user,
        )

        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM ssw_occurrences WHERE source_hash = %s", (source_hash,))
            existing = cursor.fetchone()
            cursor.execute(
                """
                INSERT INTO ssw_occurrences (
                    document_id, ctrc, ctrc_raw, invoice_number, invoice_series,
                    occurrence_code, occurrence_description, occurrence_complement,
                    occurrence_at, included_at, occurrence_user, occurrence_company,
                    occurrence_unit, delivery_date, payer_cnpj, payer_name,
                    canceled, source_import_id, source_hash
                ) VALUES (
                    (SELECT id FROM ssw_documents WHERE ctrc_raw = %s LIMIT 1),
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON DUPLICATE KEY UPDATE
                    id = LAST_INSERT_ID(id),
                    document_id = COALESCE(VALUES(document_id), document_id),
                    occurrence_description = VALUES(occurrence_description),
                    occurrence_complement = VALUES(occurrence_complement),
                    delivery_date = VALUES(delivery_date),
                    canceled = VALUES(canceled),
                    source_import_id = VALUES(source_import_id)
                """,
                (
                    occurrence.ctrc_raw, occurrence.ctrc, occurrence.ctrc_raw,
                    occurrence.invoice_number or "", occurrence.invoice_series or "",
                    occurrence.occurrence_code, occurrence.occurrence_description,
                    occurrence.occurrence_complement, occurrence.occurrence_at,
                    occurrence.included_at, occurrence.occurrence_user,
                    occurrence.occurrence_company, occurrence.occurrence_unit,
                    occurrence.delivery_date, occurrence.payer_cnpj,
                    occurrence.payer_name, int(occurrence.canceled), import_id,
                    source_hash,
                ),
            )
            return int(cursor.lastrowid), existing is None

    @staticmethod
    def upsert_portal_document(
        connection: Connection,
        document: PortalDocument,
        import_id: int,
    ) -> tuple[int, bool]:
        source_key = _sha256_values(
            document.carrier_cnpj,
            document.warehouse_ctrc,
            document.invoice_number,
            document.invoice_series,
            document.report_type,
        )

        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM portal_documents WHERE source_key = %s", (source_key,))
            existing = cursor.fetchone()
            cursor.execute(
                """
                INSERT INTO portal_documents (
                    report_type, carrier_cnpj, warehouse_ctrc,
                    warehouse_ctrc_raw, invoice_number, invoice_series,
                    transport_number, issue_date, recipient_name, recipient_cnpj,
                    recipient_state, pending_at, pending_user, pending_reason,
                    source_import_id, source_key
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON DUPLICATE KEY UPDATE
                    id = LAST_INSERT_ID(id), transport_number = VALUES(transport_number),
                    issue_date = VALUES(issue_date), recipient_name = VALUES(recipient_name),
                    recipient_cnpj = VALUES(recipient_cnpj),
                    recipient_state = VALUES(recipient_state),
                    pending_at = VALUES(pending_at), pending_user = VALUES(pending_user),
                    pending_reason = VALUES(pending_reason),
                    source_import_id = VALUES(source_import_id), active = 1,
                    last_seen_at = NOW()
                """,
                (
                    document.report_type, document.carrier_cnpj,
                    document.warehouse_ctrc, document.ctrc_raw,
                    document.invoice_number, document.invoice_series or "",
                    document.transport_number, document.issue_date,
                    document.recipient_name, document.recipient_cnpj,
                    document.recipient_state, document.pending_at,
                    document.pending_user, document.pending_reason,
                    import_id, source_key,
                ),
            )
            return int(cursor.lastrowid), existing is None

    @staticmethod
    def upsert_match(
        connection: Connection,
        *,
        portal_document_id: int,
        ssw_document_id: int | None,
        status: str,
        score: int,
        candidate_count: int,
    ) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO document_matches (
                    portal_document_id, ssw_document_id, status,
                    score, candidate_count, matched_at
                ) VALUES (%s, %s, %s, %s, %s, IF(%s = 'MATCHED', NOW(), NULL))
                ON DUPLICATE KEY UPDATE
                    ssw_document_id = VALUES(ssw_document_id),
                    status = VALUES(status), score = VALUES(score),
                    candidate_count = VALUES(candidate_count),
                    matched_at = IF(VALUES(status) = 'MATCHED', NOW(), NULL)
                """,
                (
                    portal_document_id, ssw_document_id, status,
                    score, candidate_count, status,
                ),
            )

    @staticmethod
    def counts(connection: Connection) -> dict[str, int]:
        tables = (
            "document_imports", "ssw_documents", "ssw_document_invoices",
            "ssw_document_orders", "ssw_occurrences", "portal_documents",
            "document_matches", "document_alerts", "notification_history",
        )
        result: dict[str, int] = {}
        with connection.cursor() as cursor:
            for table in tables:
                cursor.execute(f"SELECT COUNT(*) AS total FROM {table}")
                result[table] = int(cursor.fetchone()["total"])
        return result

    @staticmethod
    def find_document_ids_by_ctrc(
        connection: Connection,
        ctrc: str,
    ) -> list[int]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT id
                FROM ssw_documents
                WHERE ctrc = %s
                """,
                (ctrc,),
            )

            return [
                int(row["id"])
                for row in cursor.fetchall()
            ]


    @staticmethod
    def find_occurrence_ctrcs_by_invoice(
        connection: Connection,
        invoice_number: str,
    ) -> list[str]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT ctrc
                FROM ssw_occurrences
                WHERE invoice_number = %s
                AND canceled = 0
                AND ctrc IS NOT NULL
                AND ctrc <> ''
                """,
                (invoice_number,),
            )

            return [
                str(row["ctrc"]).strip()
                for row in cursor.fetchall()
                if row.get("ctrc")
            ]

    @staticmethod
    def find_occurrence_ctrc_raws_by_invoice(
        connection: Connection,
        invoice_number: str,
        invoice_series: str | None = None,
    ) -> list[str]:
        with connection.cursor() as cursor:
            sql = """
                SELECT DISTINCT ctrc_raw
                FROM ssw_occurrences
                WHERE invoice_number = %s
                AND canceled = 0
                AND ctrc_raw IS NOT NULL
                AND ctrc_raw <> ''
            """

            params = [invoice_number]

            if invoice_series:
                sql += " AND invoice_series = %s"
                params.append(invoice_series)

            cursor.execute(sql, tuple(params))

            return [
                str(row["ctrc_raw"]).strip()
                for row in cursor.fetchall()
                if row.get("ctrc_raw")
            ]


    @staticmethod
    def find_document_ids_by_ctrc_raw(
        connection: Connection,
        ctrc_raw: str,
    ) -> list[int]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT id
                FROM ssw_documents
                WHERE ctrc_raw = %s
                """,
                (ctrc_raw,),
            )

            return [
                int(row["id"])
                for row in cursor.fetchall()
            ]

    @staticmethod
    def find_document_ids_by_invoice(
        connection: Connection,
        invoice_number: str,
        invoice_series: str | None = None,
    ) -> list[int]:
        with connection.cursor() as cursor:
            sql = """
                SELECT DISTINCT i.document_id
                FROM ssw_document_invoices i
                WHERE i.invoice_number = %s
            """

            params = [invoice_number]

            if invoice_series:
                sql += " AND i.invoice_series = %s"
                params.append(invoice_series)

            cursor.execute(sql, tuple(params))

            return [
                int(row["document_id"])
                for row in cursor.fetchall()
            ]
