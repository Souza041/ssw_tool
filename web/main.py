from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from web.routes import router

Path("downloads").mkdir(exist_ok=True)

app = FastAPI(title="SSW Tool")

app.mount("/downloads", StaticFiles(directory="downloads"), name="downloads")

app.include_router(router)