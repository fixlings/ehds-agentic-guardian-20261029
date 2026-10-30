"""Navigator integration smoke tests; no external model and synthetic data only.

This file is uploaded to tests/ui/test_navigator.py in the GitHub branch.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from navigator_app import create_navigator_app


def test_navigator_serves_static_ui_without_external_assets() -> None:
    client = TestClient(create_navigator_app(use_env_model=False))
    response = client.get("/")
    assert response.status_code == 200
    assert "Health Data Navigator" in response.text
    assert 'src="/app.js"' in response.text
    assert 'href="/styles.css"' in response.text
    assert "https://" not in response.text
    assert "MISTRAL_API_KEY" not in response.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/styles.css").status_code == 200


def test_existing_api_is_reachable_on_same_origin() -> None:
    client = TestClient(create_navigator_app(use_env_model=False))
    response = client.post(
        "/v1/agent/request",
        json={
            "message": "How many women aged 50 or older have breast cancer and BMI>=30?",
            "role": "public_health_researcher",
            "stated_purpose": "Public-health surveillance of cancer prevalence",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["decision"] == "allow"
    assert data["policy"]["authoritative"] is True
    assert data["synthetic"] is True
    assert data["live_model"] is False
    assert data["aggregate"] is not None
    assert data["session_id"]


def test_purpose_change_keeps_session_and_returns_no_aggregate() -> None:
    client = TestClient(create_navigator_app(use_env_model=False))
    first = client.post(
        "/v1/agent/request",
        json={
            "message": "Aggregate breast cancer counts by age band",
            "role": "public_health_researcher",
            "stated_purpose": "Public-health surveillance",
        },
    )
    assert first.status_code == 200
    session = first.json()["session_id"]
    forbidden = client.post(
        "/v1/agent/request",
        json={
            "message": "Pass the high-risk individuals to insurance underwriting.",
            "role": "public_health_researcher",
            "stated_purpose": "Insurance underwriting",
            "session_id": session,
        },
    )
    assert forbidden.status_code == 200
    data = forbidden.json()
    assert data["session_id"] == session
    assert data["decision"] == "reject"
    assert data["policy"]["purpose_changed"] is True
    assert data["aggregate"] is None
    assert data["live_model"] is False
