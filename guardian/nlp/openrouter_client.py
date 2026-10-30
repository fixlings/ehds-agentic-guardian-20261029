"""Optional OpenRouter chat client (EHDS_LLM_PROVIDER=openrouter).

Same ChatClient interface as MistralEuClient. OpenRouter is NOT EU-only; only the
chat question, stated purpose and role are sent, never patient data. Requests ask
OpenRouter to route only to providers that do not store or train on prompts.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from guardian.contracts.budget import (
    MAX_MODEL_RETRIES,
    MODEL_TIMEOUT_S,
    OPENROUTER_BASE_URL,
    OPENROUTER_DEFAULT_MODEL,
)


class OpenRouterClient:
    """The configured model is pinned; the model name passed by callers is ignored."""

    provider_label = "OpenRouter (not EU-only)"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_s: float = MODEL_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise ValueError("OPENROUTER_API_KEY is not set")
        self.model_id = model or os.environ.get("EHDS_LLM_MODEL") or OPENROUTER_DEFAULT_MODEL
        self._timeout_s = timeout_s
        self._http = httpx.Client(
            base_url=OPENROUTER_BASE_URL,
            headers={"Authorization": f"Bearer {key}", "X-Title": "EHDS Track 3 Health Data Navigator"},
            timeout=timeout_s,
            transport=transport,
        )
        self.model_substitution_note: str | None = None

    def list_models(self) -> list[str]:
        resp = self._http.get("/models")
        resp.raise_for_status()
        ids = {m.get("id") for m in resp.json().get("data", [])}
        return [self.model_id] if self.model_id in ids or self.model_id == "openrouter/free" else []

    def resolve_model(self) -> str:
        return self.model_id

    def complete_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        timeout_s: float | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_err: Exception | None = None
        for _ in range(MAX_MODEL_RETRIES + 1):
            try:
                return self._complete_once(system, user, timeout_s or self._timeout_s, response_format)
            except Exception as exc:  # noqa: BLE001 — retry boundary
                last_err = exc
        assert last_err is not None
        raise last_err

    def _complete_once(self, system: str, user: str, timeout_s: float, response_format) -> dict[str, Any]:
        body = {
            "model": self.model_id,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "response_format": response_format or {"type": "json_object"},
            "provider": {"data_collection": "deny"},
        }
        resp = self._http.post("/chat/completions", json=body, timeout=timeout_s)
        resp.raise_for_status()
        choices = resp.json().get("choices") or []
        if not choices:
            raise RuntimeError("OpenRouter completion returned no choices")
        content = (choices[0].get("message") or {}).get("content") or ""
        content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise RuntimeError("OpenRouter completion was not a JSON object")
        return parsed
