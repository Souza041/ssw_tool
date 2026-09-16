from __future__ import annotations

from datetime import date
from typing import Any

from modules.documentos.database import transaction


def create_pipeline_run(
    *,
    reference_date: date,
    period_start: date | None,
    period_end: date | None,
    triggered_by: str = "MANUAL",
) -> int:
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO document_pipeline_runs (
                    status,
                    reference_date,
                    period_start,
                    period_end,
                    triggered_by
                )
                VALUES (
                    'RUNNING',
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    reference_date,
                    period_start,
                    period_end,
                    triggered_by.upper(),
                ),
            )

            return int(cursor.lastrowid)


def update_step(
    run_id: int,
    step: str,
    status: str,
) -> None:
    allowed_steps = {
        "portal": "portal_status",
        "ssw": "ssw_status",
        "import": "import_status",
        "matching": "matching_status",
        "alerts": "alerts_status",
        "notifications": "notifications_status",
    }

    column = allowed_steps.get(step)

    if not column:
        raise ValueError(
            f"Etapa de pipeline inválida: {step}"
        )

    allowed_status = {
        "PENDING",
        "RUNNING",
        "SUCCESS",
        "ERROR",
        "SKIPPED",
    }

    status = status.upper()

    if status not in allowed_status:
        raise ValueError(
            f"Status de pipeline inválido: {status}"
        )

    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE document_pipeline_runs
                SET {column} = %s
                WHERE id = %s
                """,
                (status, run_id),
            )


def finish_pipeline_run(
    run_id: int,
    *,
    matches: dict[str, Any] | None = None,
    alerts: dict[str, Any] | None = None,
    notifications: dict[str, Any] | None = None,
) -> None:

    matches = matches or {}
    alerts = alerts or {}
    notifications = notifications or {}

    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE document_pipeline_runs
                SET
                    status = 'SUCCESS',
                    finished_at = NOW(),

                    duration_seconds =
                        TIMESTAMPDIFF(
                            SECOND,
                            started_at,
                            NOW()
                        ),

                    matched_count = %s,
                    ambiguous_count = %s,
                    not_found_count = %s,

                    alerts_analyzed = %s,
                    notifications_sent = %s,

                    error_step = NULL,
                    error_message = NULL

                WHERE id = %s
                """,
                (
                    int(matches.get("MATCHED", 0)),
                    int(matches.get("AMBIGUOUS", 0)),
                    int(matches.get("NOT_FOUND", 0)),
                    int(alerts.get("analyzed", 0)),
                    int(notifications.get("sent", 0)),
                    run_id,
                ),
            )


def fail_pipeline_run(
    run_id: int,
    *,
    step: str,
    error: Exception | str,
) -> None:
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE document_pipeline_runs
                SET
                    status = 'ERROR',
                    finished_at = NOW(),

                    duration_seconds =
                        TIMESTAMPDIFF(
                            SECOND,
                            started_at,
                            NOW()
                        ),

                    error_step = %s,
                    error_message = %s

                WHERE id = %s
                """,
                (
                    step,
                    str(error),
                    run_id,
                ),
            )


def get_latest_pipeline_run() -> dict[str, Any] | None:
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM document_pipeline_runs
                ORDER BY id DESC
                LIMIT 1
                """
            )

            return cursor.fetchone()