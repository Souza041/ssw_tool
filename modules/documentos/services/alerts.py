from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import time

import pymysql

from modules.documentos.database import transaction


@dataclass(frozen=True)
class AlertRefreshStats:
    analyzed: int = 0
    created_or_updated: int = 0
    without_delivery: int = 0
    overdue: int = 0
    critical: int = 0
    warning_10: int = 0
    warning_20: int = 0
    normal: int = 0
    oc10: int = 0
    finalized_resolved: int = 0

def _to_date(value: Any) -> date | None:
    """Normaliza DATE retornado como date, datetime ou texto pelo MySQL."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else value.isoformat()


def classify_alert(days_remaining: int | None) -> str:
    if days_remaining is None:
        return "SEM_OC_01"
    if days_remaining < 0:
        return "VENCIDO"
    if days_remaining <= 5:
        return "ALERTA_5"
    if days_remaining <= 10:
        return "ALERTA_10"
    if days_remaining <= 20:
        return "ALERTA_20"
    return "NORMAL"

def _resolve_finalized_alerts(connection: Any) -> int:
    """
    Encerra alertas ainda abertos de documentos que já foram
    finalizados no Portal GCE.

    Retorna a quantidade de alertas efetivamente resolvidos.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE document_alerts a
            INNER JOIN portal_documents p
                ON p.id = a.portal_document_id
            SET
                a.status = 'RESOLVED',
                a.resolved_at = NOW()
            WHERE a.status = 'OPEN'
              AND p.finalized_at IS NOT NULL
            """
        )

        return int(cursor.rowcount or 0)

def _fetch_documents(connection: Any) -> list[dict[str, Any]]:
    """Retorna um registro por documento do portal com o histórico SSW agregado."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                p.id AS portal_document_id,
                m.ssw_document_id,
                p.invoice_number,
                p.invoice_series,
                p.transport_number,
                p.recipient_name,
                p.pending_reason,
                p.warehouse_ctrc,
                s.recipient_cnpj,
                s.recipient_name AS ssw_recipient_name,
                s.archive_package_number,
                s.remittance_cover_number,
                s.expedition_map,
                s.reception_map,
                MAX(CASE WHEN o.occurrence_code = '01'
                    AND o.canceled = 0 THEN o.delivery_date END) AS delivery_date,
                MAX(CASE WHEN o.occurrence_code = '93'
                    AND o.canceled = 0 THEN o.occurrence_at END) AS last_scan_at,
                SUM(CASE WHEN o.occurrence_code = '93'
                    AND o.canceled = 0 THEN 1 ELSE 0 END) AS scan_count,
                MAX(CASE WHEN o.occurrence_code = '10'
                    AND o.canceled = 0 THEN 1 ELSE 0 END) AS has_oc10,
                GROUP_CONCAT(DISTINCT CASE WHEN o.occurrence_code = '93'
                    AND o.canceled = 0 THEN o.occurrence_unit END
                    SEPARATOR ', ') AS scan_units,
                GROUP_CONCAT(DISTINCT CASE WHEN o.occurrence_code = '93'
                    AND o.canceled = 0 THEN o.occurrence_company END
                    SEPARATOR ', ') AS scan_partners
            FROM document_matches m
            INNER JOIN portal_documents p ON p.id = m.portal_document_id
            LEFT JOIN ssw_documents s ON s.id = m.ssw_document_id
            LEFT JOIN ssw_occurrences o ON o.document_id = s.id
            WHERE m.status = 'MATCHED'
                AND p.active = 1
                AND p.finalized_at IS NULL
            GROUP BY
                p.id, m.ssw_document_id, p.invoice_number, p.invoice_series,
                p.transport_number, p.recipient_name, p.pending_reason,
                p.warehouse_ctrc, s.recipient_cnpj, s.recipient_name,
                s.archive_package_number, s.remittance_cover_number,
                s.expedition_map, s.reception_map
            """
        )
        return list(cursor.fetchall())

def _process_alert_batch(
    connection: Any,
    rows: list[dict[str, Any]],
    reference_date: date,
) -> dict[str, int]:
    counters = {
        "analyzed": 0,
        "created_or_updated": 0,
        "without_delivery": 0,
        "overdue": 0,
        "critical": 0,
        "warning_10": 0,
        "warning_20": 0,
        "normal": 0,
        "oc10": 0,
    }

    with connection.cursor() as cursor:
        for row in rows:
            counters["analyzed"] += 1

            delivery_date = _to_date(
                row["delivery_date"]
            )

            deadline_date = (
                delivery_date + timedelta(days=30)
                if delivery_date is not None
                else None
            )

            days_remaining = (
                (deadline_date - reference_date).days
                if deadline_date is not None
                else None
            )

            alert_type = classify_alert(days_remaining)

            if days_remaining is None:
                counters["without_delivery"] += 1

            elif alert_type == "VENCIDO":
                counters["overdue"] += 1

            elif alert_type == "ALERTA_5":
                counters["critical"] += 1

            elif alert_type == "ALERTA_10":
                counters["warning_10"] += 1

            elif alert_type == "ALERTA_20":
                counters["warning_20"] += 1

            else:
                counters["normal"] += 1

            has_oc10 = bool(row["has_oc10"])

            if has_oc10:
                counters["oc10"] += 1

            details = {
                "ctrc": row["warehouse_ctrc"],
                "invoice_number": row["invoice_number"],
                "invoice_series": row["invoice_series"],
                "transport_number": row["transport_number"],
                "recipient_name": (
                    row["recipient_name"]
                    or row["ssw_recipient_name"]
                ),
                "recipient_cnpj": row["recipient_cnpj"],
                "pending_reason": row["pending_reason"],
                "archive_package_number": (
                    row["archive_package_number"]
                ),
                "remittance_cover_number": (
                    row["remittance_cover_number"]
                ),
                "expedition_map": row["expedition_map"],
                "reception_map": row["reception_map"],
                "scan_count": int(
                    row["scan_count"] or 0
                ),
                "last_scan_at": _to_text(
                    row["last_scan_at"]
                ),
                "scan_units": row["scan_units"],
                "scan_partners": row["scan_partners"],
                "has_oc10": has_oc10,
                "reference_date": (
                    reference_date.isoformat()
                ),
            }

            cursor.execute(
                """
                UPDATE document_alerts
                SET
                    status = 'RESOLVED',
                    resolved_at = NOW()
                WHERE portal_document_id = %s
                  AND status = 'OPEN'
                  AND (
                        alert_type <> %s
                        OR NOT (deadline_date <=> %s)
                      )
                """,
                (
                    row["portal_document_id"],
                    alert_type,
                    deadline_date,
                ),
            )

            cursor.execute(
                """
                SELECT id
                FROM document_alerts
                WHERE portal_document_id = %s
                  AND alert_type = %s
                  AND deadline_date <=> %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    row["portal_document_id"],
                    alert_type,
                    deadline_date,
                ),
            )

            existing_alert = cursor.fetchone()

            if existing_alert:
                cursor.execute(
                    """
                    UPDATE document_alerts
                    SET
                        ssw_document_id = %s,
                        status = 'OPEN',
                        delivery_date = %s,
                        deadline_date = %s,
                        days_remaining = %s,
                        has_oc10 = %s,
                        details = %s,
                        resolved_at = NULL
                    WHERE id = %s
                    """,
                    (
                        row["ssw_document_id"],
                        delivery_date,
                        deadline_date,
                        days_remaining,
                        int(has_oc10),
                        json.dumps(
                            details,
                            ensure_ascii=False,
                            default=str,
                        ),
                        existing_alert["id"],
                    ),
                )

            else:
                cursor.execute(
                    """
                    INSERT INTO document_alerts (
                        portal_document_id,
                        ssw_document_id,
                        alert_type,
                        status,
                        delivery_date,
                        deadline_date,
                        days_remaining,
                        has_oc10,
                        details
                    ) VALUES (
                        %s, %s, %s, 'OPEN',
                        %s, %s, %s, %s, %s
                    )
                    ON DUPLICATE KEY UPDATE
                        ssw_document_id =
                            VALUES(ssw_document_id),
                        alert_type =
                            VALUES(alert_type),
                        status = IF(
                            document_alerts.status =
                                'RESOLVED',
                            'OPEN',
                            document_alerts.status
                        ),
                        deadline_date =
                            VALUES(deadline_date),
                        delivery_date =
                            VALUES(delivery_date),
                        days_remaining =
                            VALUES(days_remaining),
                        has_oc10 =
                            VALUES(has_oc10),
                        details =
                            VALUES(details),
                        resolved_at = IF(
                            VALUES(alert_type) =
                                'VENCIDO',
                            document_alerts.resolved_at,
                            NULL
                        )
                    """,
                    (
                        row["portal_document_id"],
                        row["ssw_document_id"],
                        alert_type,
                        delivery_date,
                        deadline_date,
                        days_remaining,
                        int(has_oc10),
                        json.dumps(
                            details,
                            ensure_ascii=False,
                            default=str,
                        ),
                    ),
                )

            counters["created_or_updated"] += 1

    return counters

def refresh_alerts(
    reference_date: date | None = None,
    batch_size: int = 250,
    max_retries: int = 3,
) -> AlertRefreshStats:
    reference_date = reference_date or date.today()

    counters = {
        "analyzed": 0,
        "created_or_updated": 0,
        "without_delivery": 0,
        "overdue": 0,
        "critical": 0,
        "warning_10": 0,
        "warning_20": 0,
        "normal": 0,
        "oc10": 0,
        "finalized_resolved": 0,
    }

    # Primeiro encerra qualquer alerta ainda aberto
    # cujo documento já foi finalizado no Portal GCE.
    with transaction() as connection:
        counters["finalized_resolved"] = (
            _resolve_finalized_alerts(connection)
        )

    # Depois carrega somente documentos ainda não finalizados.
    with transaction() as connection:
        rows = _fetch_documents(connection)

    total = len(rows)

    for batch_start in range(0, total, batch_size):
        batch = rows[
            batch_start:batch_start + batch_size
        ]

        attempt = 0

        while True:
            attempt += 1

            try:
                with transaction() as connection:
                    batch_counters = _process_alert_batch(
                        connection,
                        batch,
                        reference_date,
                    )

                # Só soma depois que a transação terminou
                # com sucesso.
                for key, value in batch_counters.items():
                    counters[key] += value

                break

            except pymysql.err.OperationalError as exc:
                error_code = (
                    exc.args[0]
                    if exc.args
                    else None
                )

                # 2006 = MySQL server has gone away
                # 2013 = Lost connection during query
                retryable = error_code in (2006, 2013)

                if (
                    not retryable
                    or attempt >= max_retries
                ):
                    raise

                wait_seconds = attempt * 2

                print(
                    f"Conexão MySQL perdida no lote "
                    f"{batch_start + 1}-"
                    f"{min(batch_start + len(batch), total)} "
                    f"(erro {error_code}). "
                    f"Tentativa {attempt}/{max_retries}. "
                    f"Reconectando em {wait_seconds}s..."
                )

                time.sleep(wait_seconds)

        processed = min(
            batch_start + len(batch),
            total,
        )

        print(
            f"Alertas: {processed}/{total} "
            f"documentos processados..."
        )

    return AlertRefreshStats(**counters)