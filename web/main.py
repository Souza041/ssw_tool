from pathlib import Path

import mimetypes

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from web.routes import router
from modules.metricas.routes import router as metricas_router

from modules.metricas.scheduler import iniciar_scheduler_metricas

from starlette.middleware.gzip import GZipMiddleware

from modules.incidentes.router import (
    router as incidentes_router,
)

from modules.ocorrencia_73.scheduler import (
    iniciar_scheduler_ocorrencia_73,
)

mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/javascript", ".js")

BASE_DIR = Path(__file__).resolve().parent.parent

Path("downloads").mkdir(exist_ok=True)
Path("uploads").mkdir(exist_ok=True)
Path("temp").mkdir(exist_ok=True)

app = FastAPI(title="SSW Tool")

app.add_middleware(
    SessionMiddleware,
    secret_key="troque-essa-chave-depois",
    max_age=60 * 60 * 8,
)

app.add_middleware(
    GZipMiddleware,
    minimum_size=1000,
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

@app.on_event("startup")
def startup_event():
    print(
        "[STARTUP] Iniciando scheduler de metricas",
        flush=True,
    )
    iniciar_scheduler_metricas()

    print(
        "[STARTUP] Chamando scheduler da ocorrencia 73",
        flush=True,
    )
    iniciar_scheduler_ocorrencia_73()

    print(
        "[STARTUP] Schedulers inicializados",
        flush=True,
    )