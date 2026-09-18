from __future__ import annotations

import json
import math
import re
from typing import Any

from modules.documentos.database import transaction
from modules.documentos.pipeline_repository import get_latest_pipeline_run


ALLOWED_STATUS = {
    "VENCIDO",
    "ALERTA_5",
    "ALERTA_10",
    "ALERTA_20",
    "SEM_OC_01",
    "NORMAL",
}


def _decode_details(row: dict[str, Any]) -> dict[str, Any]:
    details = row.get("details")

    if isinstance(details, str):
        try:
            details = json.loads(details)
        except (TypeError, json.JSONDecodeError):
            details = {}

    if not isinstance(details, dict):
        details = {}

    row["details"] = details
    return row

def _duration_label(seconds: int | None) -> str:
    if seconds is None:
        return "-"

    seconds = max(0, int(seconds))

    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}min {secs}s"

    if minutes:
        return f"{minutes}min {secs}s"

    return f"{secs}s"


def _step_info(status: str | None) -> dict[str, Any]:
    status = (status or "PENDING").upper()

    labels = {
        "SUCCESS": "Concluído",
        "ERROR": "Falha",
        "RUNNING": "Em andamento",
        "SKIPPED": "Não solicitado",
        "PENDING": "Não iniciado",
    }

    return {
        "status": status,
        "label": labels.get(status, status),
        "success": status == "SUCCESS",
        "error": status == "ERROR",
        "running": status == "RUNNING",
        "skipped": status == "SKIPPED",
    }


def _automation_snapshot() -> dict[str, Any]:
    run = get_latest_pipeline_run()

    if not run:
        return {
            "available": False,
            "status": "UNKNOWN",
            "label": "Sem execução registrada",
            "healthy": False,
            "running": False,
            "error": False,
            "run_id": None,
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
            "duration_label": "-",
            "steps": {},
            "matching": {
                "matched": 0,
                "ambiguous": 0,
                "not_found": 0,
            },
            "alerts_analyzed": 0,
            "notifications_sent": 0,
            "error_step": None,
            "error_message": None,
        }

    status = str(run.get("status") or "UNKNOWN").upper()

    labels = {
        "SUCCESS": "Operação normal",
        "ERROR": "Falha no processamento",
        "RUNNING": "Processamento em andamento",
    }

    return {
        "available": True,
        "status": status,
        "label": labels.get(status, "Status desconhecido"),
        "healthy": status == "SUCCESS",
        "running": status == "RUNNING",
        "error": status == "ERROR",

        "run_id": int(run["id"]),

        "reference_date": run.get("reference_date"),
        "period_start": run.get("period_start"),
        "period_end": run.get("period_end"),

        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),

        "duration_seconds": run.get("duration_seconds"),
        "duration_label": _duration_label(
            run.get("duration_seconds")
        ),

        "steps": {
            "portal": {
                "name": "Portal GCE",
                **_step_info(run.get("portal_status")),
            },
            "ssw": {
                "name": "SSW",
                **_step_info(run.get("ssw_status")),
            },
            "import": {
                "name": "Importação",
                **_step_info(run.get("import_status")),
            },
            "matching": {
                "name": "Cruzamentos",
                **_step_info(run.get("matching_status")),
            },
            "alerts": {
                "name": "Alertas",
                **_step_info(run.get("alerts_status")),
            },
            "notifications": {
                "name": "Notificações",
                **_step_info(run.get("notifications_status")),
            },
        },

        "matching": {
            "matched": int(run.get("matched_count") or 0),
            "ambiguous": int(run.get("ambiguous_count") or 0),
            "not_found": int(run.get("not_found_count") or 0),
        },

        "alerts_analyzed": int(
            run.get("alerts_analyzed") or 0
        ),

        "notifications_sent": int(
            run.get("notifications_sent") or 0
        ),

        "error_step": run.get("error_step"),
        "error_message": run.get("error_message"),
    }

def dashboard_snapshot(
    *,
    page: int = 1,
    per_page: int = 25,
    search: str = "",
    alert_type: str = "",
    carrier_cnpj: str = "",
) -> dict[str, Any]:

    page = max(1, page)

    if per_page not in {25, 50, 100}:
        per_page = 25

    search = (search or "").strip()
    alert_type = (alert_type or "").strip().upper()

    carrier_cnpj = re.sub(
        r"\D",
        "",
        carrier_cnpj or "",
    )

    if alert_type not in ALLOWED_STATUS:
        alert_type = ""

    automation = _automation_snapshot()

    offset = (page - 1) * per_page

    with transaction() as connection:
        with connection.cursor() as cursor:

            # =====================================================
            # TRANSPORTADORAS / CNPJs DISPONÍVEIS
            # =====================================================

            cursor.execute(
                """
                SELECT
                    carrier_cnpj,
                    COUNT(*) AS total
                FROM portal_documents
                WHERE active = 1
                  AND carrier_cnpj IS NOT NULL
                  AND carrier_cnpj <> ''
                GROUP BY carrier_cnpj
                ORDER BY carrier_cnpj
                """
            )

            carriers = [
                {
                    "cnpj": str(row["carrier_cnpj"]),
                    "total": int(row["total"] or 0),
                }
                for row in cursor.fetchall()
            ]

            # =====================================================
            # CARDS — somente situação ATUAL dos documentos
            # =====================================================

            cursor.execute(
                """
                SELECT alert_type, COUNT(*) AS total
                FROM document_alerts
                WHERE status = 'OPEN'
                GROUP BY alert_type
                ORDER BY FIELD(
                    alert_type,
                    'VENCIDO',
                    'ALERTA_5',
                    'ALERTA_10',
                    'ALERTA_20',
                    'SEM_OC_01',
                    'NORMAL'
                )
                """
            )

            by_alert = {
                row["alert_type"]: int(row["total"])
                for row in cursor.fetchall()
            }

            cursor.execute(
                """
                SELECT status, COUNT(*) AS total
                FROM document_alerts
                GROUP BY status
                ORDER BY status
                """
            )

            by_status = {
                row["status"]: int(row["total"])
                for row in cursor.fetchall()
            }

            # =====================================================
            # FILTROS
            # =====================================================

            where = ["a.status = 'OPEN'"]
            params: list[Any] = []

            if carrier_cnpj:
                where.append("p.carrier_cnpj = %s")
                params.append(carrier_cnpj)

            if alert_type:
                where.append("a.alert_type = %s")
                params.append(alert_type)

            if search:
                like = f"%{search}%"

                where.append(
                    """
                    (
                        p.invoice_number LIKE %s
                        OR p.recipient_name LIKE %s
                        OR p.warehouse_ctrc LIKE %s
                        OR s.ctrc_raw LIKE %s
                    )
                    """
                )

                params.extend([like, like, like, like])

            where_sql = " AND ".join(where)

            # =====================================================
            # TOTAL DE RESULTADOS FILTRADOS
            # =====================================================

            cursor.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM document_alerts a
                INNER JOIN portal_documents p
                    ON p.id = a.portal_document_id
                LEFT JOIN ssw_documents s
                    ON s.id = a.ssw_document_id
                WHERE {where_sql}
                """,
                tuple(params),
            )

            result = cursor.fetchone()
            total_items = int(result["total"] or 0)

            total_pages = (
                math.ceil(total_items / per_page)
                if total_items
                else 1
            )

            # Se alguém pedir página acima da última,
            # volta para a última página existente.
            if page > total_pages:
                page = total_pages
                offset = (page - 1) * per_page

            # =====================================================
            # DOCUMENTOS DA PÁGINA
            # =====================================================

            query_params = list(params)
            query_params.extend([per_page, offset])

            cursor.execute(
                f"""
                SELECT
                    a.id,
                    a.portal_document_id,
                    a.ssw_document_id,
                    a.alert_type,
                    a.status,
                    a.delivery_date,
                    a.deadline_date,
                    a.days_remaining,
                    a.details,

                    p.invoice_number,
                    p.invoice_series,
                    p.recipient_name,
                    p.recipient_cnpj,
                    p.warehouse_ctrc,
                    p.carrier_cnpj,
                    p.transport_number,
                    p.pending_at,
                    p.pending_user,
                    p.pending_reason,
                    p.report_type,

                    s.ctrc_raw AS ssw_ctrc_raw

                FROM document_alerts a

                INNER JOIN portal_documents p
                    ON p.id = a.portal_document_id

                LEFT JOIN ssw_documents s
                    ON s.id = a.ssw_document_id

                WHERE {where_sql}

                ORDER BY
                    CASE a.alert_type
                        WHEN 'VENCIDO' THEN 1
                        WHEN 'ALERTA_5' THEN 2
                        WHEN 'ALERTA_10' THEN 3
                        WHEN 'ALERTA_20' THEN 4
                        WHEN 'SEM_OC_01' THEN 5
                        ELSE 6
                    END,
                    a.days_remaining ASC,
                    a.id ASC

                LIMIT %s OFFSET %s
                """,
                tuple(query_params),
            )

            rows = []

            for row in cursor.fetchall():
                row = _decode_details(row)
                details = row["details"]

                # =================================================
                # Normaliza informações importantes do front.
                # Banco atual tem preferência sobre JSON histórico.
                # =================================================

                details["invoice_number"] = (
                    row.get("invoice_number")
                    or details.get("invoice_number")
                )

                details["invoice_series"] = (
                    row.get("invoice_series")
                    or details.get("invoice_series")
                )

                details["recipient_name"] = (
                    row.get("recipient_name")
                    or details.get("recipient_name")
                )

                # CTRC completo do SSW tem prioridade.
                details["ctrc"] = (
                    row.get("ssw_ctrc_raw")
                    or details.get("ctrc")
                    or row.get("warehouse_ctrc")
                )

                details["recipient_cnpj"] = (
                    row.get("recipient_cnpj")
                    or details.get("recipient_cnpj")
                )

                details["carrier_cnpj"] = (
                    row.get("carrier_cnpj")
                    or details.get("carrier_cnpj")
                )

                details["transport_number"] = (
                    row.get("transport_number")
                    or details.get("transport_number")
                )

                details["pending_at"] = (
                    row.get("pending_at")
                    or details.get("pending_at")
                )

                details["pending_user"] = (
                    row.get("pending_user")
                    or details.get("pending_user")
                )

                details["pending_reason"] = (
                    row.get("pending_reason")
                    or details.get("pending_reason")
                )

                details["report_type"] = (
                    row.get("report_type")
                    or details.get("report_type")
                )

                details["has_pending"] = bool(
                    details.get("pending_reason")
                )

                rows.append(row)

            # =====================================================
            # PAGINAÇÃO
            # =====================================================

            start_item = (
                offset + 1
                if total_items
                else 0
            )

            end_item = min(
                offset + len(rows),
                total_items,
            )

            return {
                "automation": automation,
                
                "totals": {
                    "alerts": sum(by_alert.values()),
                    "overdue": by_alert.get("VENCIDO", 0),
                    "critical": by_alert.get("ALERTA_5", 0),
                    "warning_10": by_alert.get("ALERTA_10", 0),
                    "warning_20": by_alert.get("ALERTA_20", 0),
                    "without_delivery": by_alert.get("SEM_OC_01", 0),
                    "normal": by_alert.get("NORMAL", 0),
                },

                "by_alert_type": by_alert,
                "by_status": by_status,

                "items": rows,

                "filters": {
                    "search": search,
                    "alert_type": alert_type,
                    "carrier_cnpj": carrier_cnpj,
                },

                "carriers": carriers,

                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total_items": total_items,
                    "total_pages": total_pages,
                    "start_item": start_item,
                    "end_item": end_item,
                    "has_previous": page > 1,
                    "has_next": page < total_pages,
                    "previous_page": page - 1 if page > 1 else None,
                    "next_page": page + 1 if page < total_pages else None,
                },
            }