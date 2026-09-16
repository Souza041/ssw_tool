from __future__ import annotations

from typing import Any

from modules.documentos.metrics_repository import (
    get_latest_snapshot,
    get_snapshot_history,
    get_pipeline_history,
    get_pipeline_summary,
)


def _duration_label(seconds: int | float | None) -> str:
    if seconds is None:
        return "-"

    seconds = max(0, int(seconds))

    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}min"

    if minutes:
        return f"{minutes}min {secs}s"

    return f"{secs}s"


def metrics_snapshot(days: int = 30) -> dict[str, Any]:
    days = max(7, min(int(days), 365))

    latest = get_latest_snapshot()
    history = get_snapshot_history(days)
    chart = {
        "labels": [
            item["snapshot_date"].strftime("%d/%m")
            for item in history
        ],
        "monitored": [
            int(item["monitored_count"] or 0)
            for item in history
        ],
        "overdue": [
            int(item["overdue_count"] or 0)
            for item in history
        ],
        "alert_5": [
            int(item["alert_5_count"] or 0)
            for item in history
        ],
        "alert_10": [
            int(item["alert_10_count"] or 0)
            for item in history
        ],
        "alert_20": [
            int(item["alert_20_count"] or 0)
            for item in history
        ],
        "without_delivery": [
            int(item["without_delivery_count"] or 0)
            for item in history
        ],

        "opened": [
            int(item["opened_count"] or 0)
            for item in history
        ],
        "resolved": [
            int(item["resolved_count"] or 0)
            for item in history
        ],
    }
    pipeline_history = get_pipeline_history(30)
    pipeline = get_pipeline_summary(days)

    total_runs = int(
        pipeline.get("total_runs") or 0
    )

    successful_runs = int(
        pipeline.get("successful_runs") or 0
    )

    failed_runs = int(
        pipeline.get("failed_runs") or 0
    )

    success_rate = (
        round((successful_runs / total_runs) * 100, 1)
        if total_runs
        else 0.0
    )

    latest = latest or {}

    pending_matching = (
        int(latest.get("ambiguous_count") or 0)
        +
        int(latest.get("not_found_count") or 0)
    )

    movement = {
        "opened": sum(
            int(item["opened_count"] or 0)
            for item in history
        ),
        "resolved": sum(
            int(item["resolved_count"] or 0)
            for item in history
        ),
    }

    movement["balance"] = (
        movement["opened"] - movement["resolved"]
    )

    return {
        "period_days": days,

        "latest": latest,

        "cards": {
            "monitored": int(
                latest.get("monitored_count") or 0
            ),
            "overdue": int(
                latest.get("overdue_count") or 0
            ),
            "without_delivery": int(
                latest.get("without_delivery_count") or 0
            ),
            "matched": int(
                latest.get("matched_count") or 0
            ),
            "pending_matching": pending_matching,
        },

        "pipeline": {
            "total": total_runs,
            "success": successful_runs,
            "errors": failed_runs,
            "success_rate": success_rate,

            "average_duration_seconds": int(
                pipeline.get("average_duration") or 0
            ),

            "average_duration_label": _duration_label(
                pipeline.get("average_duration")
            ),

            "maximum_duration_label": _duration_label(
                pipeline.get("maximum_duration")
            ),
        },

        "chart": chart,

        "history": history,
        "pipeline_history": pipeline_history,

        "movement": movement,
    }