"""Health Data Navigator: the request API plus the static web page on one origin.

    uvicorn navigator_app:app --host 127.0.0.1 --port 8000

Set OPENROUTER_API_KEY to switch on the AI request check. The key never reaches the browser.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server.api import router

WEB_DIR = Path(__file__).resolve().parent / "web"


def create_app() -> FastAPI:
    app = FastAPI(title="EHDS Track 3 Health Data Navigator")
    app.include_router(router)
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
