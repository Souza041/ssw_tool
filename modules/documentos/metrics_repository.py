from __future__ import annotations

from datetime import date
from typing import Any

from modules.documentos.database import transaction


def save_daily_snapshot(
    snapshot_date: date,
) -> dict[str, int]:
    """
    Calcula o estado atual do módulo Documentos GCE e
    persiste um snapshot diário em document_metrics_daily.

    O mesmo dia pode ser recalculado sem duplicar registros.
    """

    with transaction() as connection:
        with connection.cursor() as cursor:

            # =========================================================
            # DOCUMENTOS MONITORADOS
            # =========================================================

            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM portal_documents
                WHERE active = 1
                """
            )

            monitored_count = int(
                cursor.fetchone()["total"] or 0
            )

            # =========================================================
            # ALERTAS ABERTOS
            # =========================================================

            cursor.execute(
                """
                SELECT
                    COUNT(
                        DISTINCT CASE
                            WHEN alert_type = 'VENCIDO'
                            THEN portal_document_id
                        END
                    ) AS overdue_count,

                    COUNT(
                        DISTINCT CASE
                            WHEN alert_type = 'ALERTA_5'
                            THEN portal_document_id
                        END
                    ) AS alert_5_count,

                    COUNT(
                        DISTINCT CASE
                            WHEN alert_type = 'ALERTA_10'
                            THEN portal_document_id
                        END
                    ) AS alert_10_count,

                    COUNT(
                        DISTINCT CASE
                            WHEN alert_type = 'ALERTA_20'
                            THEN portal_document_id
                        END
                    ) AS alert_20_count,

                    COUNT(
                        DISTINCT CASE
                            WHEN alert_type = 'SEM_OC_01'
                            THEN portal_document_id
                        END
                    ) AS without_delivery_count

                FROM document_alerts
                WHERE status = 'OPEN'
                """
            )

            alerts = cursor.fetchone() or {}

            overdue_count = int(
                alerts.get("overdue_count") or 0
            )

            alert_5_count = int(
                alerts.get("alert_5_count") or 0
            )

            alert_10_count = int(
                alerts.get("alert_10_count") or 0
            )

            alert_20_count = int(
                alerts.get("alert_20_count") or 0
            )

            without_delivery_count = int(
                alerts.get("without_delivery_count") or 0
            )

            # =========================================================
            # MATCHING ATUAL
            # =========================================================

            cursor.execute(
                """
                SELECT
                    SUM(status = 'MATCHED') AS matched_count,
                    SUM(status = 'AMBIGUOUS') AS ambiguous_count,
                    SUM(status = 'NOT_FOUND') AS not_found_count
                FROM document_matches
                """
            )

            matching = cursor.fetchone() or {}

            matched_count = int(
                matching.get("matched_count") or 0
            )

            ambiguous_count = int(
                matching.get("ambiguous_count") or 0
            )

            not_found_count = int(
                matching.get("not_found_count") or 0
            )

            # =========================================================
            # MOVIMENTAÇÃO DO DIA
            # =========================================================

            cursor.execute(
                """
                SELECT
                    COUNT(DISTINCT portal_document_id)
                        AS opened_count
                FROM document_alerts
                WHERE DATE(opened_at) = %s
                """,
                (snapshot_date,),
            )

            opened_count = int(
                cursor.fetchone()["opened_count"] or 0
            )

            cursor.execute(
                """
                SELECT
                    COUNT(DISTINCT portal_document_id)
                        AS resolved_count
                FROM document_alerts
                WHERE resolved_at IS NOT NULL
                  AND DATE(resolved_at) = %s
                """,
                (snapshot_date,),
            )

            resolved_count = int(
                cursor.fetchone()["resolved_count"] or 0
            )

            # =========================================================
            # UPSERT SNAPSHOT
            # =========================================================

            cursor.execute(
                """
                INSERT INTO document_metrics_daily (
                    snapshot_date,

                    monitored_count,
                    overdue_count,
                    alert_5_count,
                    alert_10_count,
                    alert_20_count,
                    without_delivery_count,

                    matched_count,
                    ambiguous_count,
                    not_found_count,

                    opened_count,
                    resolved_count
                )
                VALUES (
                    %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s
                )

                ON DUPLICATE KEY UPDATE
                    monitored_count = VALUES(monitored_count),
                    overdue_count = VALUES(overdue_count),
                    alert_5_count = VALUES(alert_5_count),
                    alert_10_count = VALUES(alert_10_count),
                    alert_20_count = VALUES(alert_20_count),
                    without_delivery_count =
                        VALUES(without_delivery_count),

                    matched_count = VALUES(matched_count),
                    ambiguous_count = VALUES(ambiguous_count),
                    not_found_count = VALUES(not_found_count),

                    opened_count = VALUES(opened_count),
                    resolved_count = VALUES(resolved_count),

                    updated_at = NOW()
                """,
                (
                    snapshot_date,

                    monitored_count,
                    overdue_count,
                    alert_5_count,
                    alert_10_count,
                    alert_20_count,
                    without_delivery_count,

                    matched_count,
                    ambiguous_count,
                    not_found_count,

                    opened_count,
                    resolved_count,
                ),
            )

    return {
        "monitored": monitored_count,
        "overdue": overdue_count,
        "alert_5": alert_5_count,
        "alert_10": alert_10_count,
        "alert_20": alert_20_count,
        "without_delivery": without_delivery_count,
        "matched": matched_count,
        "ambiguous": ambiguous_count,
        "not_found": not_found_count,
        "opened": opened_count,
        "resolved": resolved_count,
    }

def get_latest_snapshot() -> dict[str, Any] | None:
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM document_metrics_daily
                ORDER BY snapshot_date DESC
                LIMIT 1
                """
            )
            return cursor.fetchone()


def get_snapshot_history(days: int = 30) -> list[dict[str, Any]]:
    days = max(1, min(int(days), 365))

    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    snapshot_date,
                    monitored_count,
                    overdue_count,
                    alert_5_count,
                    alert_10_count,
                    alert_20_count,
                    without_delivery_count,
                    matched_count,
                    ambiguous_count,
                    not_found_count,
                    opened_count,
                    resolved_count
                FROM document_metrics_daily
                WHERE snapshot_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                ORDER BY snapshot_date ASC
                """,
                (days,),
            )

            return list(cursor.fetchall())


def get_pipeline_history(limit: int = 30) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 100))

    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    status,
                    reference_date,
                    started_at,
                    finished_at,
                    duration_seconds,

                    portal_status,
                    ssw_status,
                    import_status,
                    matching_status,
                    alerts_status,
                    notifications_status,

                    matched_count,
                    ambiguous_count,
                    not_found_count,
                    alerts_analyzed,
                    notifications_sent,

                    error_step,
                    error_message

                FROM document_pipeline_runs
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            )

            return list(cursor.fetchall())


def get_pipeline_summary(days: int = 30) -> dict[str, Any]:
    days = max(1, min(int(days), 365))

    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total_runs,

                    SUM(status = 'SUCCESS')
                        AS successful_runs,

                    SUM(status = 'ERROR')
                        AS failed_runs,

                    AVG(
                        CASE
                            WHEN status = 'SUCCESS'
                            THEN duration_seconds
                        END
                    ) AS average_duration,

                    MAX(duration_seconds)
                        AS maximum_duration

                FROM document_pipeline_runs
                WHERE reference_date >=
                    DATE_SUB(CURDATE(), INTERVAL %s DAY)
                """,
                (days,),
            )

            return cursor.fetchone() or {}