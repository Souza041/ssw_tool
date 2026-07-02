from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from web.routes import router

Path("downloads").mkdir(exist_ok=True)
Path("uploads").mkdir(exist_ok=True)
Path("temp").mkdir(exist_ok=True)

app = FastAPI(title="SSW Tool")

app.add_middleware(
    SessionMiddleware,
    secret_key="troque-essa-chave-depois",
    max_age=60 * 60 * 8,
)

app.mount("/downloads", StaticFiles(directory="downloads"), name="downloads")

app.include_router(router)