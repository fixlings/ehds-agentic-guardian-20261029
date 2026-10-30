"""EU Mistral adapter: pinned regional endpoint, JSON and secret boundaries."""
import json

import pytest

from ehds import ai_check
from server import mistral_eu


class _Reply:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": '{"prohibited": false, "category": "none"}'}}]}


class _HttpClient:
    def __init__(self, *, headers, timeout):
        self.headers = headers
        self.timeout = timeout
        self.calls = []

    def post(self, url, *, json, timeout):
        self.calls.append((url, json, timeout))
        return _Reply()


def test_eu_only_json_pinned_model_and_server_secret(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key-not-for-production")
    monkeypatch.setenv("EHDS_AI_CHECK", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(mistral_eu.httpx, "Client", _HttpClient)
    client = mistral_eu.MistralEuClient()
    result = client.complete_json(model="untrusted-model", system="sys", user="synthetic request")
    assert result == {"prohibited": False, "category": "none"}
    url, payload, timeout = client._http.calls[0]
    assert url == "https://api.eu.mistral.ai/v1/chat/completions"
    assert payload["model"] == "mistral-small-2603"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][1]["content"] == "synthetic request"
    assert client._http.headers["Authorization"] == "Bearer test-key-not-for-production"
    assert "test-key-not-for-production" not in json.dumps(payload)
    assert timeout == 25
    assert ai_check.enabled() is True


def test_missing_mistral_key(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(ValueError):
        mistral_eu.MistralEuClient()


def test_ai_check_disabled_when_configured_off(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key-not-for-production")
    monkeypatch.setenv("EHDS_AI_CHECK", "0")
    assert ai_check.enabled() is False
