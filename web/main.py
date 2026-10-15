import logging
import mimetypes
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from web.session_config import carregar_configuracao_sessao


logger = logging.getLogger(__name__)
session_security = carregar_configuracao_sessao()
logger.info(
    "Configuração de sessão validada; ambiente=%s; origem=%s.",
    session_security.environment,
    session_security.source,
)

from web.routes import router
from modules.metricas.routes import router as metricas_router

#from modules.metricas.scheduler import iniciar_scheduler_metricas

from starlette.middleware.gzip import GZipMiddleware

from modules.incidentes.router import (
    router as incidentes_router,
)
from modules.documentos.web import router as documentos_router

from modules.ocorrencia_73.scheduler import (
    iniciar_scheduler_ocorrencia_73,
)
from modules.ocorrencia_73.routes import (
    router as ocorrencia_73_router,
)
from web.session_store import SESSION_MAX_AGE_SECONDS

mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/javascript", ".js")

BASE_DIR = Path(__file__).resolve().parent.parent

Path("downloads").mkdir(exist_ok=True)
Path("uploads").mkdir(exist_ok=True)
Path("temp").mkdir(exist_ok=True)

app = FastAPI(title="SSW Tool")

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

app.mount("/downloads", StaticFiles(directory=str(BASE_DIR / "downloads")), name="downloads")

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

app.include_router(router)
app.include_router(metricas_router)

app.include_router(
    incidentes_router
)
app.include_router(documentos_router)
app.include_router(ocorrencia_73_router)

@app.on_event("startup")
def startup_event():
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
