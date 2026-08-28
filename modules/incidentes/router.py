from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from modules.incidentes.repository import (
    IncidentesRepository,
)
from modules.incidentes.schemas import (
    DashboardFilters,
    DashboardResponse,
)
from modules.incidentes.service import (
    IncidentesService,
)


router = APIRouter(
    prefix="/incidentes",
    tags=["Incidentes"],
)

templates = Jinja2Templates(
    directory="modules/incidentes/templates"
)

repository = IncidentesRepository()

service = IncidentesService(
    repository=repository
)


# ======================================================
# Página
# ======================================================

@router.get(
    "",
    response_class=HTMLResponse,
    name="incidentes_dashboard",
)
@router.get(
    "/",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def dashboard_page(
    request: Request,
):
    """
    Renderiza a página principal do dashboard.

    Os dados são carregados posteriormente pelo
    JavaScript através da API.
    """

    snapshot = repository.snapshot_atual()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "page_title": "Incidentes",
            "module_name": "Incidentes",
            "snapshot_name": (
                snapshot.nome
                if snapshot is not None
                else None
            ),
        },
    )


# ======================================================
# API principal
# ======================================================

@router.get(
    "/api/dashboard",
    response_model=DashboardResponse,
    name="incidentes_api_dashboard",
)
def dashboard_api(
    data_inicial: Optional[date] = Query(
        default=None,
        description=(
            "Data inicial no formato YYYY-MM-DD."
        ),
    ),
    data_final: Optional[date] = Query(
        default=None,
        description=(
            "Data final no formato YYYY-MM-DD."
        ),
    ),
    grupos_clientes: Optional[List[str]] = Query(
        default=None,
    ),
    clientes: Optional[List[str]] = Query(
        default=None,
    ),
    unidades: Optional[List[str]] = Query(
        default=None,
    ),
    codigos_ocorrencia: Optional[List[str]] = Query(
        default=None,
    ),
    status_debito: Optional[List[str]] = Query(
        default=None,
    ),
    regras_debito: Optional[List[str]] = Query(
        default=None,
    ),
    produtos: Optional[List[str]] = Query(
        default=None,
    ),
    tipos_operacao: Optional[List[str]] = Query(
        default=None,
    ),
):
    """
    Retorna todos os dados necessários para montar
    o dashboard de incidentes.
    """

    try:
        filtros = DashboardFilters(
            data_inicial=data_inicial,
            data_final=data_final,
            grupos_clientes=(
                grupos_clientes or []
            ),
            clientes=clientes or [],
            unidades=unidades or [],
            codigos_ocorrencia=(
                codigos_ocorrencia or []
            ),
            status_debito=(
                status_debito or []
            ),
            regras_debito=(
                regras_debito or []
            ),
            produtos=produtos or [],
            tipos_operacao=tipos_operacao or [],
        )

        return service.dashboard(
            filtros=filtros
        )

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Não foi possível carregar "
                "o dashboard de incidentes."
            ),
        ) from exc


# ======================================================
# Status do módulo
# ======================================================

@router.get(
    "/api/status",
    name="incidentes_api_status",
)
def status_api():
    """
    Retorna informações básicas do snapshot,
    estrutura das abas e cache.
    """

    snapshot = repository.snapshot_atual()

    if snapshot is None:
        return {
            "success": False,
            "available": False,
            "message": (
                "Nenhum snapshot de incidentes "
                "foi encontrado."
            ),
            "snapshot": None,
            "cache": repository.cache_info(),
        }

    try:
        estrutura = (
            repository.validar_estrutura()
        )
    except Exception as exc:
        estrutura = {
            "valido": False,
            "erro": str(exc),
        }

    return {
        "success": True,
        "available": True,
        "message": (
            "Módulo de incidentes disponível."
        ),
        "snapshot": {
            "nome": snapshot.nome,
            "caminho": str(snapshot.path),
            "tamanho": snapshot.tamanho,
            "atualizado_em": (
                snapshot.atualizado_em
            ),
        },
        "estrutura": estrutura,
        "cache": repository.cache_info(),
    }


# ======================================================
# Cache
# ======================================================

@router.post(
    "/api/cache/reload",
    name="incidentes_api_cache_reload",
)
def recarregar_cache():
    """
    Limpa o cache e força uma nova leitura
    do snapshot atual.
    """

    snapshot = repository.snapshot_atual()

    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Nenhum snapshot disponível "
                "para recarregar."
            ),
        )

    try:
        repository.limpar_cache()

        repository.carregar_planilha(
            arquivo=snapshot.path,
            forcar_recarregamento=True,
        )

        return {
            "success": True,
            "message": (
                "Cache recarregado com sucesso."
            ),
            "cache": repository.cache_info(),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Não foi possível recarregar "
                "o cache de incidentes."
            ),
        ) from exc