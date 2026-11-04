import logging
import mimetypes
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import PlainTextResponse
from web.session_config import carregar_configuracao_sessao
from web.qa_mode import qa_enabled, qa_data_dir
from web.session_store import SESSION_MAX_AGE_SECONDS


logger = logging.getLogger(__name__)
session_security = carregar_configuracao_sessao()
logger.info(
    "Configuração de sessão validada; ambiente=%s; origem=%s.",
    session_security.environment,
    session_security.source,
)
QA_MODE = qa_enabled()

if not QA_MODE:
    from web.routes import router
    from modules.metricas.routes import router as metricas_router

    from modules.incidentes.router import router as incidentes_router
    from modules.incidentes.consulta_routes import router as incidentes_consulta_router
    from modules.documentos.web import router as documentos_router
    from modules.whirlpool.routes import router as whirlpool_router
    from web.documentos_lg import router as documentos_lg_router
    from modules.ocorrencia_73.scheduler import iniciar_scheduler_ocorrencia_73
    from modules.ocorrencia_73.routes import router as ocorrencia_73_router

mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/javascript", ".js")

BASE_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = qa_data_dir(BASE_DIR) if QA_MODE else Path.cwd()

if QA_MODE:
    for directory in ("downloads", "uploads", "temp"):
        (RUNTIME_DIR / directory).mkdir(parents=True, exist_ok=True)
else:
    Path("downloads").mkdir(exist_ok=True)
    Path("uploads").mkdir(exist_ok=True)
    Path("temp").mkdir(exist_ok=True)

app = FastAPI(title="SSW Tool")

if QA_MODE:
    @app.get("/qa/health")
    def qa_health():
        return {"qa_mode": True, "operational_routes": False}

@app.middleware("http")
async def bloquear_operacoes_qa(request, call_next):
    if QA_MODE and request.url.path != "/qa/health" and not request.url.path.startswith("/static/"):
        return PlainTextResponse("Operações desabilitadas no ambiente QA.", status_code=503)
    return await call_next(request)

app.add_middleware(
    SessionMiddleware,
    secret_key=session_security.secret_key,
    max_age=SESSION_MAX_AGE_SECONDS,
)

app.add_middleware(
    GZipMiddleware,
    minimum_size=1000,
)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "web" / "static")),
    name="static",
)

app.mount(
    "/downloads",
    StaticFiles(directory=str((RUNTIME_DIR if QA_MODE else BASE_DIR) / "downloads")),
    name="downloads",
)

app.mount(
    "/metricas-static",
    StaticFiles(directory=str(BASE_DIR / "modules" / "metricas" / "static")),
    name="metricas-static",
)

app.mount(
    "/incidentes-static",
    StaticFiles(
        directory=str(
            BASE_DIR
            / "modules"
            / "incidentes"
            / "static"
        )
    ),
    name="incidentes-static",
)

if not QA_MODE:
    app.include_router(router)
    app.include_router(metricas_router)
    app.include_router(incidentes_router)
    app.include_router(incidentes_consulta_router)
    app.include_router(documentos_router)
    app.include_router(ocorrencia_73_router)
    app.include_router(whirlpool_router)
    app.include_router(documentos_lg_router)

@app.on_event("startup")
def startup_event():
    if QA_MODE:
        logger.info("QA ativo: schedulers operacionais não iniciados.")
        return
    #print(
    #    "[STARTUP] Iniciando scheduler de metricas",
    #    flush=True,
    #)
    # iniciar_scheduler_metricas()

    print(
        "[STARTUP] Chamando scheduler da ocorrencia 73",
        flush=True,
    )
    iniciar_scheduler_ocorrencia_73()

    print(
        "[STARTUP] Schedulers inicializados",
        flush=True,
    )
