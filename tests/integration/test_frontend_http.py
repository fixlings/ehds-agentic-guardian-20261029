"""HTTP contract for a frontend. No network and no real credential."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from guardian.api.app import create_app
from guardian.api.live import main
from guardian.contracts.schemas import ROUTE_PATH

SENTINEL = "not-a-real-credential-sentinel"
RESEARCH = {
    "message": "How many women aged 50 or older have breast cancer and BMI>=30?",
    "role": "public_health_researcher",
    "stated_purpose": "Public-health surveillance of cancer prevalence",
}


def test_offline_post_returns_a_decision_without_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", SENTINEL)
    response = TestClient(create_app()).post(ROUTE_PATH, json=RESEARCH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "allow"
    assert body["session_id"]
    assert body["synthetic"] is True
    assert body["live_model"] is False
    assert body["aggregate"]["population_n"] == 42
    assert SENTINEL not in response.text


def test_live_entry_missing_key_fails_without_echoing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setenv("UNRELATED_ENV", SENTINEL)
    with pytest.raises(SystemExit) as caught:
        main()
    message = str(caught.value)
    assert message == "MISTRAL_API_KEY is not set. Live API was not started."
    assert SENTINEL not in message
