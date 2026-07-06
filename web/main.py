from pathlib import Path

import mimetypes

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from web.routes import router
from modules.metricas.routes import router as metricas_router

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

app.mount("/downloads", StaticFiles(directory=str(BASE_DIR / "downloads")), name="downloads")

app.mount(
    "/metricas-static",
    StaticFiles(directory=str(BASE_DIR / "modules" / "metricas" / "static")),
    name="metricas-static",
)

app.include_router(router)
app.include_router(metricas_router)