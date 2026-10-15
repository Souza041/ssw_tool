import logging
import os
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Path as ApiPath, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from modules.ocorrencia_73.observabilidade import (
    PAGINA_TAMANHO_PADRAO,
    PAGINA_MAXIMA,
    Ocorrencia73ObservabilidadeRepository,
    estado_seguranca_oc73,
    proximas_execucoes_teoricas,
    usuario_pode_observar,
)
from web.session_store import obter_client


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/oc73/observabilidade", tags=["OC73"])
BASE_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=[
    str(BASE_DIR / "web" / "templates"),
    str(BASE_DIR / "modules" / "ocorrencia_73" / "templates"),
])


def _exigir_acesso_observabilidade(request: Request):
    client = obter_client(request)
    if client is None:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)
    if not usuario_pode_observar(getattr(client, "usuario", None)):
        raise HTTPException(
            status_code=403,
            detail="Acesso à observabilidade OC73 não autorizado.",
        )
    return None


def _limite_alerta_minutos() -> int:
    try:
        valor = int(os.getenv("OC73_ALERTA_EXECUTANDO_MINUTOS", "120"))
    except ValueError:
        return 120
    return max(1, min(valor, 10080))


@router.get("", response_class=HTMLResponse)
def painel_observabilidade(
    request: Request,
    pagina: int = Query(1, ge=1, le=PAGINA_MAXIMA),
    por_pagina: int = Query(PAGINA_TAMANHO_PADRAO, ge=1, le=100),
    fluxo: str | None = Query(None),
    status: str | None = Query(None),
    data_inicio: date | None = Query(None),
    data_fim: date | None = Query(None),
):
    redirect = _exigir_acesso_observabilidade(request)
    if redirect:
        return redirect

    contexto = {
        "request": request,
        "seguranca": estado_seguranca_oc73(),
        "proximas_execucoes": proximas_execucoes_teoricas(),
        "filtros": {
            "fluxo": fluxo or "",
            "status": status or "",
            "data_inicio": data_inicio,
            "data_fim": data_fim,
        },
        "limite_alerta_minutos": _limite_alerta_minutos(),
        "erro_banco": None,
    }
    try:
        repository = Ocorrencia73ObservabilidadeRepository()
        contexto["historico"] = repository.listar_execucoes(
            pagina=pagina,
            por_pagina=por_pagina,
            fluxo=fluxo,
            status=status,
            data_inicio=data_inicio,
            data_fim=data_fim,
        )
        contexto["ultimas_execucoes"] = (
            repository.ultimas_execucoes_por_fluxo()
        )
        contexto["alertas"] = repository.execucoes_anormais(
            contexto["limite_alerta_minutos"]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(
            "Falha ao consultar observabilidade OC73; tipo=%s",
            type(exc).__name__,
        )
        contexto.update({
            "historico": {
                "itens": [], "total": 0, "pagina": 1,
                "por_pagina": por_pagina, "total_paginas": 1,
            },
            "ultimas_execucoes": {"CWB": None, "BIG": None},
            "alertas": [],
            "erro_banco": (
                "Banco de auditoria indisponível. Nenhuma ação foi executada."
            ),
        })

    return templates.TemplateResponse(
        request,
        "oc73_observabilidade.html",
        contexto,
        status_code=503 if contexto["erro_banco"] else 200,
    )


@router.get("/execucoes/{execucao_id}", response_class=HTMLResponse)
def detalhe_execucao(
    request: Request,
    execucao_id: int = ApiPath(..., ge=1),
    pagina: int = Query(1, ge=1, le=PAGINA_MAXIMA),
    por_pagina: int = Query(PAGINA_TAMANHO_PADRAO, ge=1, le=100),
):
    redirect = _exigir_acesso_observabilidade(request)
    if redirect:
        return redirect

    try:
        repository = Ocorrencia73ObservabilidadeRepository()
        execucao = repository.obter_execucao(execucao_id)
        if execucao is None:
            raise HTTPException(status_code=404, detail="Execução não encontrada.")
        tentativas = repository.listar_tentativas(
            execucao_id,
            pagina=pagina,
            por_pagina=por_pagina,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Falha ao consultar execução OC73 id=%s; tipo=%s",
            execucao_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="Banco de auditoria indisponível.",
        )

    return templates.TemplateResponse(
        request,
        "oc73_execucao_detalhe.html",
        {
            "request": request,
            "execucao": execucao,
            "tentativas": tentativas,
        },
    )
