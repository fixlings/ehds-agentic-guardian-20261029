import json

import httpx
import pytest

from server.llm import OPENROUTER_BASE_URL, OPENROUTER_DEFAULT_MODEL, OpenRouterClient


def _transport(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": OPENROUTER_DEFAULT_MODEL}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "```json\n{\"ok\": true}\n```"}}]})

    return httpx.MockTransport(handler)


def test_pins_model_host_and_no_data_retention():
    seen: list[httpx.Request] = []
    client = OpenRouterClient(api_key="test", transport=_transport(seen))
    assert client.list_models() == [OPENROUTER_DEFAULT_MODEL]
    assert client.complete_json(model="mistral-small-2603", system="s", user="u") == {"ok": True}
    body = json.loads(seen[-1].content)
    assert body["model"] == OPENROUTER_DEFAULT_MODEL
    assert body["provider"] == {"data_collection": "deny"}
    assert all(str(r.url).startswith(OPENROUTER_BASE_URL) for r in seen)


def test_requires_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError):
        OpenRouterClient()
