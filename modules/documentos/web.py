from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from modules.documentos.services.dashboard import dashboard_snapshot


router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/documentos", response_class=HTMLResponse)
def documentos_dashboard(
    request: Request,
    page: int = 1,
    per_page: int = 25,
    q: str = "",
    status: str = "",
):
    if not request.session.get("ssw_session_id"):
        return RedirectResponse("/login", status_code=303)

    snapshot = dashboard_snapshot(
        page=page,
        per_page=per_page,
        search=q,
        alert_type=status,
    )

    return templates.TemplateResponse(
        "documentos_dashboard_v2.html",
        {
            "request": request,
            "dashboard": snapshot,
        },
    )

@router.get(
    "/documentos/metricas",
    response_class=HTMLResponse,
)
def documentos_metricas(
    request: Request,
    days: int = 30,
):
    from modules.documentos.services.metrics import (
        metrics_snapshot,
    )

    days = max(7, min(days, 365))

    dashboard = metrics_snapshot(days)

    return templates.TemplateResponse(
        "documentos_metricas.html",
        {
            "request": request,
            "dashboard": dashboard,
            "days": days,
        },
    )