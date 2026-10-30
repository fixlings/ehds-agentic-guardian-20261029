"""HTTP adapter for POST /v1/agent/request.

The default app is offline. Set EHDS_USE_MISTRAL=1 and call create_app()
with use_env_model=True to use the EU Mistral host. The demo script stays mocked.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from guardian.api.orchestrator import (
    DeadlineExceededError,
    ModelUnavailableError,
    Orchestrator,
    UnknownDatasetError,
)
from guardian.api.sessions import UnknownSessionError
from guardian.contracts.schemas import ROUTE_PATH, AgentRequest, AgentResponse, ErrorBody


def create_app(
    *,
    client: Any | None = None,
    orchestrator: Orchestrator | None = None,
    use_env_model: bool = False,
) -> FastAPI:
    if orchestrator is None:
        if client is None and use_env_model:
            from guardian.nlp.mistral_client import MistralEuClient

            client = MistralEuClient()
        orchestrator = Orchestrator(client=client)
    app = FastAPI(title="EHDS agentic guardian", version="0.1.0")
    app.state.orchestrator = orchestrator

    @app.post(ROUTE_PATH)
    async def agent_request(request: Request) -> JSONResponse:
        raw = await request.body()
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return _error(400, "invalid_request", "Body must be JSON.")
        if not isinstance(payload, dict):
            return _error(400, "invalid_request", "Body must be a JSON object.")
        try:
            body = AgentRequest.model_validate(payload)
        except ValidationError:
            return _error(400, "invalid_request", "Request body does not match the agent contract.")
        try:
            result: AgentResponse = orchestrator.handle(body)
        except UnknownSessionError as exc:
            return _error(400, "invalid_request", "Unknown session_id.", session_id=exc.session_id)
        except UnknownDatasetError:
            return _error(400, "invalid_request", "Unknown dataset_id.", session_id=body.session_id)
        except ModelUnavailableError:
            return _error(503, "model_unavailable", "EU Mistral endpoint is unavailable.", session_id=body.session_id)
        except DeadlineExceededError:
            return _error(504, "timeout", "Request deadline exceeded.", session_id=body.session_id)
        return JSONResponse(status_code=200, content=result.model_dump(mode="json"))

    return app


def _error(status: int, error: str, message: str, session_id: str | None = None) -> JSONResponse:
    body = ErrorBody(error=error, message=message, session_id=session_id)  # type: ignore[arg-type]
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


app = create_app(use_env_model=False)
