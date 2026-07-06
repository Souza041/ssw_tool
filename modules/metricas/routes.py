from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from modules.metricas.auth import usuario_e_admin, usuario_logado_id
from modules.metricas.service import MetricasService

router = APIRouter()

templates = Jinja2Templates(directory="modules/metricas/templates")


@router.get("/metricas", response_class=HTMLResponse)
def metricas_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard_metricas_v34_base.html",
        context={}
    )


@router.get("/api/metricas/dashboard")
def metricas_dashboard():
    service = MetricasService()
    data = service.obter_dashboard()
    return JSONResponse(content=jsonable_encoder(data))


@router.post("/api/metricas/refresh")
def metricas_refresh(request: Request):
    try:
        service = MetricasService()

        result = service.atualizar_op455(
            triggered_by="manual",
            triggered_user_id=usuario_logado_id(request),
            dias=30,
        )

        status_code = 200 if result.get("success") else 500
        return JSONResponse(content=jsonable_encoder(result), status_code=status_code)

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e)
            }
        )