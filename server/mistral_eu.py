"""EU-only Mistral chat-completion client for the hosted synthetic EHDS demo.

Uses the key only in server-side Authorization headers; no patient-level records
are supplied to the model. Implements the existing AI-check client protocol.
"""
from __future__ import annotations

import json
import os
import httpx

EU_CHAT_URL = "https://api.eu.mistral.ai/v1/chat/completions"
DEFAULT_MODEL = "mistral-small-2603"


class MistralEuClient:
    provider_label = "Mistral EU"

    def __init__(self, *, timeout_s: float = 25.0) -> None:
        key = os.environ.get("MISTRAL_API_KEY")
        if not key:
            raise ValueError("MISTRAL_API_KEY is not configured")
        self.model_id = os.environ.get("EHDS_MISTRAL_MODEL", DEFAULT_MODEL)
        self._http = httpx.Client(
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=timeout_s,
        )
        self._timeout_s = timeout_s

    def complete_json(
        self, *, model: str, system: str, user: str,
        timeout_s: float | None = None, response_format: dict | None = None,
    ) -> dict:
        payload = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "response_format": response_format or {"type": "json_object"},
        }
        response = self._http.post(EU_CHAT_URL, json=payload, timeout=timeout_s or self._timeout_s)
        response.raise_for_status()
        choices = response.json().get("choices") or []
        if not choices:
            raise RuntimeError("EU Mistral returned no completion")
        content = (choices[0].get("message") or {}).get("content") or ""
        if not isinstance(content, str):
            raise ValueError("EU Mistral returned a non-text completion")
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:].strip()
        elif content.startswith("```"):
            content = content[3:].strip()
        if content.endswith("```"):
            content = content[:-3].strip()
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("EU Mistral response is not a JSON object")
        return parsed
