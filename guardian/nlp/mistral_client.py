"""EU-region Mistral chat client. Only this module calls the Mistral API."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from guardian.contracts.budget import (
    DEFAULT_MODEL,
    MAX_MODEL_RETRIES,
    MISTRAL_EU_BASE_URL,
    MODEL_TIMEOUT_S,
)


class MistralEuClient:
    """ChatClient implementation pinned to the EU inference host."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = MISTRAL_EU_BASE_URL,
        timeout_s: float = MODEL_TIMEOUT_S,
    ) -> None:
        key = api_key or os.environ.get("MISTRAL_API_KEY")
        if not key:
            raise ValueError("MISTRAL_API_KEY is not set")
        if base_url.rstrip("/") != MISTRAL_EU_BASE_URL:
            raise ValueError("Mistral client must use the EU host only")
        self._api_key = key
        self._base_url = MISTRAL_EU_BASE_URL.rstrip("/")
        self._timeout_s = timeout_s
        self._resolved_model: str | None = None
        self._model_substitution_note: str | None = None

    @property
    def model_substitution_note(self) -> str | None:
        return self._model_substitution_note

    def list_models(self) -> list[str]:
        data = self._request("GET", "/v1/models")
        items = data.get("data") or data.get("models") or []
        ids: list[str] = []
        for item in items:
            if isinstance(item, str):
                ids.append(item)
            elif isinstance(item, dict) and item.get("id"):
                ids.append(str(item["id"]))
        return ids

    def resolve_model(self) -> str:
        if self._resolved_model:
            return self._resolved_model
        models = self.list_models()
        if DEFAULT_MODEL in models:
            self._resolved_model = DEFAULT_MODEL
            return self._resolved_model
        chat_like = [
            m
            for m in models
            if "embed" not in m.lower() and "moderation" not in m.lower()
        ]
        if not chat_like:
            chat_like = models
        if not chat_like:
            raise RuntimeError("No models returned from EU Mistral list_models")
        self._resolved_model = chat_like[0]
        self._model_substitution_note = (
            f"Default model {DEFAULT_MODEL} not in EU list; using {self._resolved_model}. "
            f"Available: {', '.join(models)}"
        )
        return self._resolved_model

    def complete_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        attempts = MAX_MODEL_RETRIES + 1
        last_err: Exception | None = None
        for _ in range(attempts):
            try:
                return self._complete_once(model=model, system=system, user=user, timeout_s=timeout)
            except Exception as exc:  # noqa: BLE001 — retry boundary
                last_err = exc
        assert last_err is not None
        raise last_err

    def _complete_once(
        self, *, model: str, system: str, user: str, timeout_s: float
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        data = self._request("POST", "/v1/chat/completions", json_body=payload, timeout_s=timeout_s)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("Mistral completion returned no choices")
        content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise RuntimeError("Mistral completion empty content")
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise RuntimeError("Mistral completion was not a JSON object")
        return parsed

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}{path}"
        if re.search(r"api\.mistral\.ai", url) and "api.eu.mistral.ai" not in url:
            raise ValueError("Refusing non-EU Mistral host")
        with httpx.Client(timeout=timeout) as client:
            response = client.request(method, url, headers=headers, json=json_body)
            response.raise_for_status()
            return response.json()
