from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from modules.documentos.services.dashboard import dashboard_snapshot

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/documentos", response_class=HTMLResponse)
def documentos_dashboard(request: Request):
    if not request.session.get("ssw_session_id"):
        return RedirectResponse("/login", status_code=303)
    snapshot = dashboard_snapshot(limit=1000)
    return templates.TemplateResponse(
        "documentos_dashboard_v2.html",
        {"request": request, "dashboard": snapshot},
    )
