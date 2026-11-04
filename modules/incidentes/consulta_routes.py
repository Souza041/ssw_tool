
"""Rotas da Central de Consulta de Incidentes."""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from web.session_store import obter_client
from modules.incidentes.consulta import (
    ConsultaInvalida,
    consultar_incidentes,
)
from modules.incidentes.historico import (
    HistoricoInvalido,
    consultar_historico,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/incidentes")

BASE_DIR = Path(__file__).resolve().parents[2]

templates = Jinja2Templates(
    directory=str(BASE_DIR / "web" / "templates")
)


def _autenticado(request: Request) -> bool:
    if not request.session.get("ssw_session_id"):
        return False

    return obter_client(request) is not None


def _exigir_autenticacao(request: Request) -> None:
    if not _autenticado(request):
        raise HTTPException(
            status_code=401,
            detail="Sessao expirada. Faca login novamente.",
            headers={"Cache-Control": "no-store"},
        )


@router.get(
    "/consulta",
    response_class=HTMLResponse,
)
def pagina_consulta(request: Request):
    if not _autenticado(request):
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    response = templates.TemplateResponse(
        "incidentes_consulta.html",
        {"request": request},
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/api/consulta")
def api_consulta(
    request: Request,
    termo: str = Query(..., min_length=1, max_length=80),
    tipo: str = Query("auto", pattern="^(auto|ctrc|nf)$"),
    limite: int = Query(100, ge=1, le=500),
):
    _exigir_autenticacao(request)

    try:
        resultado = consultar_incidentes(
            termo,
            tipo=tipo,
            limite=limite,
        )
        return resultado

    except ConsultaInvalida as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception:
        logger.exception(
            "Falha na consulta de incidentes"
        )
        raise HTTPException(
            status_code=500,
            detail="Erro interno ao consultar incidentes.",
        )


@router.get("/api/historico")
def api_historico(
    request: Request,
    termo: str = Query(..., min_length=1, max_length=80),
    tipo: str = Query("auto", pattern="^(auto|ctrc|nf)$"),
    limite: int = Query(500, ge=1, le=5000),
):
    _exigir_autenticacao(request)

    try:
        return consultar_historico(
            termo,
            tipo=tipo,
            limite=limite,
        )

    except HistoricoInvalido as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception:
        logger.exception(
            "Falha na consulta historica de incidentes"
        )
        raise HTTPException(
            status_code=500,
            detail="Erro interno ao consultar historico.",
        )
