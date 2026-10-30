"""Single-origin HTTP entry point for the Health Data Navigator demo.

The existing Guardian API keeps authority over policy, calculations and evidence.
Only the static user interface is added here. Start with:

    uvicorn navigator_app:app --host 127.0.0.1 --port 8000

Set EHDS_USE_LIVE_MODEL=1 and MISTRAL_API_KEY server-side to use EU Mistral.
The default is the deterministic offline path; no key is sent to the browser.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from guardian.api.app import create_app


WEB_DIR = Path(__file__).resolve().parent / "web"


def create_navigator_app(*, use_env_model: bool | None = None) -> FastAPI:
    """Mount the static Navigator after the existing /v1 API routes."""
    if use_env_model is None:
        use_env_model = os.getenv("EHDS_USE_LIVE_MODEL", "0").strip().lower() in {
            "1", "true", "yes"
        }
    app = create_app(use_env_model=use_env_model)
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="navigator")
    return app


app = create_navigator_app()
