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

ALLOWED_LIFECYCLE_STATUS = {
    "ACTIVE",
    "FINALIZED",
    "ALL",
}

def _normalize_document_filters(
    *,
    search: str = "",
    alert_type: str = "",
    carrier_cnpj: str = "",
    pending_status: str = "",
    lifecycle_status: str = "ACTIVE",
) -> tuple[str, str, str, str, str]:

    search = (search or "").strip()
    alert_type = (alert_type or "").strip().upper()
    pending_status = (pending_status or "").strip().upper()
    lifecycle_status = (
        lifecycle_status or "ACTIVE"
    ).strip().upper()

    carrier_cnpj = re.sub(
        r"\D",
        "",
        carrier_cnpj or "",
    )

    if alert_type not in ALLOWED_STATUS:
        alert_type = ""

    if pending_status not in {"PENDING", "CLEAR"}:
        pending_status = ""

    if lifecycle_status not in ALLOWED_LIFECYCLE_STATUS:
        lifecycle_status = "ACTIVE"

    return (
        search,
        alert_type,
        carrier_cnpj,
        pending_status,
        lifecycle_status,
    )

def _build_document_where(
    *,
    search: str = "",
    alert_type: str = "",
    carrier_cnpj: str = "",
    pending_status: str = "",
    lifecycle_status: str = "ACTIVE",
) -> tuple[str, list[Any]]:

    where = ["p.active = 1"]
    params: list[Any] = []

    if lifecycle_status == "ACTIVE":
        where.append("p.finalized_at IS NULL")
        where.append("a.status = 'OPEN'")

    elif lifecycle_status == "FINALIZED":
        where.append("p.finalized_at IS NOT NULL")

    elif lifecycle_status == "ALL":
        where.append(
            """
            (
                (p.finalized_at IS NULL AND a.status = 'OPEN')
                OR p.finalized_at IS NOT NULL
            )
            """
        )

    if carrier_cnpj:
        where.append("p.carrier_cnpj = %s")
        params.append(carrier_cnpj)

    if alert_type:
        where.append("a.alert_type = %s")
        params.append(alert_type)

    if pending_status == "PENDING":
        where.append(
            """
            (
                p.pending_reason IS NOT NULL
                AND TRIM(p.pending_reason) <> ''
            )
            """
        )

    elif pending_status == "CLEAR":
        where.append(
            """
            (
                p.pending_reason IS NULL
                OR TRIM(p.pending_reason) = ''
            )
            """
        )

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

    return " AND ".join(where), params

def _build_document_source(
    lifecycle_status: str,
) -> str:

    if lifecycle_status == "FINALIZED":
        return """
            FROM portal_documents p

            LEFT JOIN document_alerts a
                ON a.id = (
                    SELECT MAX(a2.id)
                    FROM document_alerts a2
                    WHERE a2.portal_document_id = p.id
                )

            LEFT JOIN ssw_documents s
                ON s.id = a.ssw_document_id
        """

    return """
        FROM document_alerts a

        INNER JOIN portal_documents p
            ON p.id = a.portal_document_id

        LEFT JOIN ssw_documents s
            ON s.id = a.ssw_document_id
    """

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

def _build_cards_where(
    *,
    search: str = "",
    carrier_cnpj: str = "",
    pending_status: str = "",
) -> tuple[str, list[Any]]:

    where = ["p.active = 1"]
    params: list[Any] = []

    if carrier_cnpj:
        where.append("p.carrier_cnpj = %s")
        params.append(carrier_cnpj)

    if pending_status == "PENDING":
        where.append(
            """
            (
                p.pending_reason IS NOT NULL
                AND TRIM(p.pending_reason) <> ''
            )
            """
        )

    elif pending_status == "CLEAR":
        where.append(
            """
            (
                p.pending_reason IS NULL
                OR TRIM(p.pending_reason) = ''
            )
            """
        )

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

    return " AND ".join(where), params

def dashboard_snapshot(
    *,
    page: int = 1,
    per_page: int = 25,
    search: str = "",
    alert_type: str = "",
    carrier_cnpj: str = "",
    pending_status: str = "",
    lifecycle_status: str = "ACTIVE",
) -> dict[str, Any]:

    page = max(1, page)

    if per_page not in {25, 50, 100}:
        per_page = 25

    (
        search,
        alert_type,
        carrier_cnpj,
        pending_status,
        lifecycle_status,
    ) = _normalize_document_filters(
        search=search,
        alert_type=alert_type,
        carrier_cnpj=carrier_cnpj,
        pending_status=pending_status,
        lifecycle_status=lifecycle_status,
    )

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

            cards_where, cards_params = _build_cards_where(
                search=search,
                carrier_cnpj=carrier_cnpj,
                pending_status=pending_status,
            )

            # =====================================================
            # CARDS — ATIVOS
            # =====================================================

            cursor.execute(
                f"""
                SELECT
                    COUNT(DISTINCT a.portal_document_id) AS active_total,

                    COUNT(
                        DISTINCT CASE
                            WHEN a.alert_type = 'VENCIDO'
                            THEN a.portal_document_id
                        END
                    ) AS overdue,

                    COUNT(
                        DISTINCT CASE
                            WHEN a.alert_type = 'ALERTA_5'
                            THEN a.portal_document_id
                        END
                    ) AS critical,

                    COUNT(
                        DISTINCT CASE
                            WHEN a.alert_type = 'ALERTA_10'
                            THEN a.portal_document_id
                        END
                    ) AS warning_10,

                    COUNT(
                        DISTINCT CASE
                            WHEN a.alert_type = 'ALERTA_20'
                            THEN a.portal_document_id
                        END
                    ) AS warning_20,

                    COUNT(
                        DISTINCT CASE
                            WHEN a.alert_type = 'SEM_OC_01'
                            THEN a.portal_document_id
                        END
                    ) AS without_delivery

                FROM document_alerts a

                INNER JOIN portal_documents p
                    ON p.id = a.portal_document_id

                LEFT JOIN ssw_documents s
                    ON s.id = a.ssw_document_id

                WHERE {cards_where}
                AND p.finalized_at IS NULL
                AND a.status = 'OPEN'
                """,
                tuple(cards_params),
            )

            active_cards = cursor.fetchone() or {}

            # =====================================================
            # CARDS — FINALIZADOS
            # =====================================================

            cursor.execute(
                f"""
                SELECT
                    COUNT(DISTINCT p.id) AS finalized_total,

                    COUNT(
                        DISTINCT CASE
                            WHEN EXISTS (
                                SELECT 1
                                FROM document_alerts ad
                                WHERE ad.portal_document_id = p.id
                                AND ad.delivery_date IS NOT NULL
                            )
                            THEN p.id
                        END
                    ) AS finalized_with_delivery,

                    COUNT(
                        DISTINCT CASE
                            WHEN NOT EXISTS (
                                SELECT 1
                                FROM document_alerts ad
                                WHERE ad.portal_document_id = p.id
                                AND ad.delivery_date IS NOT NULL
                            )
                            THEN p.id
                        END
                    ) AS finalized_without_delivery,

                    COUNT(
                        DISTINCT CASE
                            WHEN a.deadline_date IS NOT NULL
                            AND p.finalized_at > a.deadline_date
                            THEN p.id
                        END
                    ) AS finalized_after_deadline,

                    COUNT(
                        DISTINCT CASE
                            WHEN YEAR(p.finalized_at) = YEAR(CURDATE())
                            AND MONTH(p.finalized_at) = MONTH(CURDATE())
                            THEN p.id
                        END
                    ) AS finalized_this_month

                FROM portal_documents p

                LEFT JOIN document_alerts a
                    ON a.portal_document_id = p.id

                LEFT JOIN ssw_documents s
                    ON s.id = a.ssw_document_id

                WHERE {cards_where}
                AND p.finalized_at IS NOT NULL
                """,
                tuple(cards_params),
            )

            finalized_cards = cursor.fetchone() or {}

            # =====================================================
            # MONTA OS CARDS CONFORME O ESTADO SELECIONADO
            # =====================================================

            active_total = int(
                active_cards.get("active_total") or 0
            )

            finalized_total = int(
                finalized_cards.get("finalized_total") or 0
            )

            finalized_with_delivery = int(
                finalized_cards.get("finalized_with_delivery") or 0
            )

            finalized_without_delivery = int(
                finalized_cards.get("finalized_without_delivery") or 0
            )

            finalized_after_deadline = int(
                finalized_cards.get("finalized_after_deadline") or 0
            )

            finalized_this_month = int(
                finalized_cards.get("finalized_this_month") or 0
            )

            all_total = active_total + finalized_total

            finalized_percentage = (
                round((finalized_total / all_total) * 100, 1)
                if all_total
                else 0.0
            )

            delivery_percentage = (
                round(
                    (finalized_with_delivery / finalized_total) * 100,
                    1,
                )
                if finalized_total
                else 0.0
            )

            if lifecycle_status == "FINALIZED":
                cards = {
                    "mode": "FINALIZED",
                    "items": [
                        {
                            "label": "Documentos finalizados",
                            "value": finalized_total,
                            "caption": "Finalizados no Portal GCE",
                            "style": "success",
                        },
                        {
                            "label": "Com data de entrega",
                            "value": finalized_with_delivery,
                            "caption": "Entrega localizada no SSW",
                            "style": "info",
                        },
                        {
                            "label": "Sem data de entrega",
                            "value": finalized_without_delivery,
                            "caption": "Sem OC 01 localizada",
                            "style": "neutral",
                        },
                        {
                            "label": "Após o prazo",
                            "value": finalized_after_deadline,
                            "caption": "Finalizados após o prazo documental",
                            "style": "danger",
                        },
                        {
                            "label": "Finalizados no mês",
                            "value": finalized_this_month,
                            "caption": "Finalizações no mês atual",
                            "style": "success",
                        },
                        {
                            "label": "Com entrega",
                            "value": f"{delivery_percentage:.1f}%",
                            "caption": "Percentual dos finalizados",
                            "style": "info",
                        },
                    ],
                }

            elif lifecycle_status == "ALL":
                cards = {
                    "mode": "ALL",
                    "items": [
                        {
                            "label": "Total de documentos",
                            "value": all_total,
                            "caption": "Ativos + finalizados",
                            "style": "info",
                        },
                        {
                            "label": "Ativos",
                            "value": active_total,
                            "caption": "Em acompanhamento",
                            "style": "warning",
                        },
                        {
                            "label": "Finalizados",
                            "value": finalized_total,
                            "caption": "Finalizados no Portal GCE",
                            "style": "success",
                        },
                        {
                            "label": "Taxa de finalização",
                            "value": f"{finalized_percentage:.1f}%",
                            "caption": "Finalizados sobre o total",
                            "style": "success",
                        },
                        {
                            "label": "Com entrega",
                            "value": finalized_with_delivery,
                            "caption": "Finalizados com entrega localizada",
                            "style": "info",
                        },
                        {
                            "label": "Sem entrega",
                            "value": finalized_without_delivery,
                            "caption": "Finalizados sem OC 01 localizada",
                            "style": "neutral",
                        },
                    ],
                }

            else:
                cards = {
                    "mode": "ACTIVE",
                    "items": [
                        {
                            "label": "Documentos monitorados",
                            "value": active_total,
                            "caption": "Base atual de acompanhamento",
                            "style": "info",
                        },
                        {
                            "label": "Vencidos",
                            "value": int(
                                active_cards.get("overdue") or 0
                            ),
                            "caption": "Prazo documental excedido",
                            "style": "danger",
                        },
                        {
                            "label": "Até 5 dias",
                            "value": int(
                                active_cards.get("critical") or 0
                            ),
                            "caption": "Ação imediata",
                            "style": "critical",
                        },
                        {
                            "label": "Até 10 dias",
                            "value": int(
                                active_cards.get("warning_10") or 0
                            ),
                            "caption": "Atenção ao prazo",
                            "style": "warning",
                        },
                        {
                            "label": "Até 20 dias",
                            "value": int(
                                active_cards.get("warning_20") or 0
                            ),
                            "caption": "Em monitoramento",
                            "style": "warning",
                        },
                        {
                            "label": "Sem data de entrega",
                            "value": int(
                                active_cards.get("without_delivery") or 0
                            ),
                            "caption": "OC 01 não localizada",
                            "style": "neutral",
                        },
                    ],
                }

            # =====================================================
            # FILTROS
            # =====================================================

            where_sql, params = _build_document_where(
                search=search,
                alert_type=alert_type,
                carrier_cnpj=carrier_cnpj,
                pending_status=pending_status,
                lifecycle_status=lifecycle_status,
            )

            # =====================================================
            # TOTAL DE RESULTADOS FILTRADOS
            # =====================================================

            document_source = _build_document_source(
                lifecycle_status
            )

            cursor.execute(
                f"""
                SELECT COUNT(*) AS total

                {document_source}

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
                    p.id AS portal_document_id,
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
                    p.finalized_at,

                    s.ctrc_raw AS ssw_ctrc_raw

                {document_source}

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

                details["finalized_at"] = row.get("finalized_at")
                details["is_finalized"] = bool(row.get("finalized_at"))

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
                
                "cards": cards,

                "items": rows,

                "filters": {
                    "search": search,
                    "alert_type": alert_type,
                    "carrier_cnpj": carrier_cnpj,
                    "pending_status": pending_status,
                    "lifecycle_status": lifecycle_status,
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

def export_documents(
    *,
    search: str = "",
    alert_type: str = "",
    carrier_cnpj: str = "",
    pending_status: str = "",
    lifecycle_status: str = "ACTIVE",
) -> list[dict[str, Any]]:

    (
        search,
        alert_type,
        carrier_cnpj,
        pending_status,
        lifecycle_status,
    ) = _normalize_document_filters(
        search=search,
        alert_type=alert_type,
        carrier_cnpj=carrier_cnpj,
        pending_status=pending_status,
        lifecycle_status=lifecycle_status,
    )

    where_sql, params = _build_document_where(
        search=search,
        alert_type=alert_type,
        carrier_cnpj=carrier_cnpj,
        pending_status=pending_status,
        lifecycle_status=lifecycle_status,
    )

    document_source = _build_document_source(
        lifecycle_status
    )

    with transaction() as connection:
        with connection.cursor() as cursor:

            cursor.execute(
                f"""
                SELECT
                    a.id,
                    p.id AS portal_document_id,
                    a.alert_type,
                    a.delivery_date,
                    a.deadline_date,
                    a.days_remaining,
                    a.details,

                    p.carrier_cnpj,
                    p.invoice_number,
                    p.invoice_series,
                    p.recipient_name,
                    p.recipient_cnpj,
                    p.warehouse_ctrc,
                    p.transport_number,
                    p.pending_at,
                    p.pending_user,
                    p.pending_reason,
                    p.report_type,
                    p.finalized_at,

                    s.ctrc_raw AS ssw_ctrc_raw

                {document_source}

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
                """,
                tuple(params),
            )

            rows = []

            for row in cursor.fetchall():
                row = _decode_details(row)
                details = row["details"]

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

                details["recipient_cnpj"] = (
                    row.get("recipient_cnpj")
                    or details.get("recipient_cnpj")
                )

                details["carrier_cnpj"] = (
                    row.get("carrier_cnpj")
                    or details.get("carrier_cnpj")
                )

                details["ctrc"] = (
                    row.get("ssw_ctrc_raw")
                    or details.get("ctrc")
                    or row.get("warehouse_ctrc")
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

            return rows