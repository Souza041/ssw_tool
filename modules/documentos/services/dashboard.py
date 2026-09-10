from __future__ import annotations

import json
from typing import Any

from modules.documentos.database import transaction


def dashboard_snapshot(*, limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(limit, 1000))
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT alert_type, COUNT(*) AS total
                FROM document_alerts
                GROUP BY alert_type
                ORDER BY FIELD(alert_type, 'VENCIDO', 'ALERTA_5', 'ALERTA_10',
                              'ALERTA_20', 'SEM_OC_01', 'NORMAL')
                """
            )
            by_alert = {row["alert_type"]: int(row["total"]) for row in cursor.fetchall()}

            cursor.execute(
                """
                SELECT status, COUNT(*) AS total
                FROM document_alerts
                GROUP BY status
                ORDER BY status
                """
            )
            by_status = {row["status"]: int(row["total"]) for row in cursor.fetchall()}

            cursor.execute(
                """
                SELECT
                    a.id, a.portal_document_id, a.ssw_document_id,
                    a.alert_type, a.status, a.delivery_date, a.deadline_date,
                    a.days_remaining, a.details
                FROM document_alerts a
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
                LIMIT %s
                """,
                (limit,),
            )
            rows = []
            for row in cursor.fetchall():
                details = row["details"]
                if isinstance(details, str):
                    try:
                        details = json.loads(details)
                    except json.JSONDecodeError:
                        pass
                row["details"] = details
                rows.append(row)

            return {
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
            }
