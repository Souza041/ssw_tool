"""Leituras administrativas mínimas, sem exposição de JSON ou logs persistidos."""

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from modules.health_center.monitor import enabled
from modules.health_center.privacy import checked_execution_id, checked_identifier
from modules.health_center.repository import HealthRepository
from web.admin_access import is_admin
from web.session_store import obter_client


router = APIRouter(prefix="/admin/health-center", tags=["Health Center"])
templates = Jinja2Templates(directory="web/templates")


def _authorize(request: Request) -> None:
    if obter_client(request) is None:
        raise HTTPException(status_code=401, detail="Autenticação necessária.")
    if not is_admin(request):
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")


def _repository(request: Request) -> HealthRepository:
    _authorize(request)
    if not enabled():
        raise HTTPException(status_code=503, detail="Health Center desativado.")
    return HealthRepository()


def _read(call):
    try:
        return call()
    except Exception:
        raise HTTPException(status_code=503, detail="Observabilidade indisponível.") from None


def _threshold() -> int:
    try:
        value = int(os.getenv("HEALTH_RUNNING_SUSPECT_MINUTES", "120"))
    except ValueError as exc:
        raise ValueError("Limiar de RUNNING inválido.") from exc
    if not 1 <= value <= 10080:
        raise ValueError("Limiar de RUNNING fora do intervalo.")
    return value


def dashboard_context(
    request: Request, *, bot_id: str | None = None, status: str | None = None,
    days: int = 7, page: int = 1,
) -> dict:
    """Dados da seção Nexus; nunca consulta o banco com o flag desligado."""
    _authorize(request)
    if not enabled():
        return {"state": "disabled", "overview": None, "catalog": [],
                "history": {"items": [], "total": 0, "page": page, "per_page": 20}}
    try:
        repository = HealthRepository()
        now = datetime.now(timezone.utc)
        threshold = _threshold()
        return {
            "state": "ready",
            "overview": repository.overview(now=now, threshold_minutes=threshold),
            "catalog": repository.catalog(limit=50, now=now, threshold_minutes=threshold),
            "history": repository.history(
                bot_id=bot_id, status=status, since=now - timedelta(days=days),
                page=page, per_page=20,
            ),
        }
    except Exception:
        return {"state": "error", "overview": None, "catalog": [],
                "history": {"items": [], "total": 0, "page": page, "per_page": 20}}


@router.get("/status")
def health_status(request: Request):
    _authorize(request)
    return {"enabled": enabled(), "operational_state": "unverified"}


@router.get("/overview")
def health_overview(request: Request):
    repository = _repository(request)
    return _read(lambda: repository.overview(
        now=datetime.now(timezone.utc), threshold_minutes=_threshold()
    ))


@router.get("/bots")
def health_bots(request: Request, limit: int = Query(50, ge=1, le=100)):
    repository = _repository(request)
    return {"items": _read(lambda: repository.catalog(
        limit=limit, now=datetime.now(timezone.utc), threshold_minutes=_threshold()
    ))}


@router.get("/bots/{bot_id}")
def health_bot_detail(
    request: Request,
    bot_id: str = Path(pattern=r"^[a-z][a-z0-9_]{0,99}$"),
    days: int = Query(30, ge=1, le=90),
    page: int = Query(1, ge=1, le=1000),
):
    repository = _repository(request)
    now = datetime.now(timezone.utc)
    bots = _read(lambda: repository.catalog(
        limit=1, bot_id=bot_id, now=now, threshold_minutes=_threshold(),
    ))
    if not bots:
        raise HTTPException(status_code=404, detail="Automação não encontrada.")
    since = now - timedelta(days=days)
    return {
        "bot": bots[0],
        "activity": _read(lambda: repository.bot_activity(bot_id=bot_id, since=since)),
        "history": _read(lambda: repository.history(
            bot_id=bot_id, since=since, page=page, per_page=20,
        )),
        "days": days,
    }


@router.get("/bots/{bot_id}/view", response_class=HTMLResponse)
def health_bot_page(
    request: Request,
    bot_id: str = Path(pattern=r"^[a-z][a-z0-9_]{0,99}$"),
    days: int = Query(30, ge=1, le=90),
    page: int = Query(1, ge=1, le=1000),
):
    detail = health_bot_detail(request, bot_id, days, page)
    return templates.TemplateResponse(request, "health_bot_detail.html", {
        "is_admin": True, "detail": detail,
    })


@router.get("/executions")
def health_executions(
    request: Request,
    bot_id: str | None = Query(None, pattern=r"^[a-z][a-z0-9_]{0,99}$"),
    status: str | None = Query(None, pattern=r"^(PENDING|RUNNING|SUCCESS|PARTIAL|FAILED|CANCELLED|SKIPPED)$"),
    days: int = Query(7, ge=1, le=90),
    page: int = Query(1, ge=1, le=1000),
    per_page: int = Query(25, ge=1, le=100),
):
    repository = _repository(request)
    return _read(lambda: repository.history(
        bot_id=bot_id, status=status,
        since=datetime.now(timezone.utc) - timedelta(days=days),
        page=page, per_page=per_page,
    ))


@router.get("/executions/{execution_id}")
def health_execution_detail(
    request: Request,
    execution_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,36}$"),
):
    repository = _repository(request)
    result = _read(lambda: repository.execution_detail(execution_id))
    if result is None:
        raise HTTPException(status_code=404, detail="Execução não encontrada.")
    return result


@router.get("/executions/{execution_id}/view", response_class=HTMLResponse)
def health_execution_page(
    request: Request,
    execution_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,36}$"),
):
    detail = health_execution_detail(request, execution_id)
    return templates.TemplateResponse(request, "health_execution_detail.html", {
        "is_admin": True, "execution": detail,
    })


@router.get("/suspicious-running")
def health_suspicious_running(request: Request, limit: int = Query(50, ge=1, le=100)):
    repository = _repository(request)
    rows = _read(lambda: repository.suspicious_running(
        now=datetime.now(timezone.utc), limit=limit,
    ))
    return {
        "items": [{
            "execution_id": _safe_identity(row.get("id"), checked_execution_id),
            "bot_id": _safe_identity(row.get("bot_id"), checked_identifier),
            "started_at_utc": row.get("started_at_utc"),
            "observability_state": "SUSPECTED_INTERRUPTION",
        } for row in rows],
        "classification": "suspected_interruption_only",
    }


def _safe_identity(value, checker):
    try:
        return checker(value)
    except (ValueError, TypeError):
        return None
