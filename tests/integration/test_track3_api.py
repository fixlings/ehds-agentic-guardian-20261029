import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from guardian.api.app import create_app

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = json.loads((ROOT / "requests" / "expected.json").read_text(encoding="utf-8"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from ehds import audit, workspace

    monkeypatch.setattr(audit, "LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr(workspace, "ROOT", tmp_path / "ws")
    return TestClient(create_app())


def _req(rid):
    return json.loads((ROOT / "requests" / f"{rid}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("rid", [f"R{i:02d}" for i in range(1, 11)])
def test_formal_route_decisions(client, rid):
    body = client.post("/v1/requests", json=_req(rid)).json()
    expected = EXPECTED[rid] if isinstance(EXPECTED, dict) else next(e for e in EXPECTED if e["id"] == rid)
    assert body["decision"] == expected["decision"]
    assert body["articles"]
    assert "raw_counts_staff_only" not in json.dumps(body)


def test_spe_download_blocked_and_audit_chain(client):
    body = client.post("/v1/requests", json=_req("R05")).json()
    assert body["decision"] == "APPROVE_SPE"
    assert client.post("/v1/workspaces/R05/download").status_code == 403
    assert client.post("/v1/workspaces/R05/query", json={"sql": "COPY cohort TO 'x.csv'"}).status_code == 403
    assert client.get("/v1/audit").json()["verify"]["ok"] is True


def test_formal_route_rejects_incomplete_request(client):
    assert client.post("/v1/requests", json={"purpose": "x"}).status_code == 400


def test_chat_on_track3_hides_total_and_cites_articles(client):
    body = client.post("/v1/agent/request", json={
        "message": "How many women aged 50 or older have breast cancer?",
        "role": "public_health_researcher",
        "stated_purpose": "Public-health surveillance",
        "dataset_id": "track3-ehds-20k",
    }).json()
    assert body["decision"] == "allow"
    assert body["aggregate"]["population_n"] is None and body["aggregate"]["timing"] is None
    assert "Art. 53" in body["policy"]["article_mapping"]["citation"]
    insurance = client.post("/v1/agent/request", json={
        "message": "Pass the high-risk individuals to insurance underwriting.",
        "role": "public_health_researcher",
        "stated_purpose": "Insurance underwriting",
        "dataset_id": "track3-ehds-20k",
        "session_id": body["session_id"],
    }).json()
    assert insurance["decision"] == "reject" and insurance["aggregate"] is None
    assert "Art. 54" in insurance["policy"]["article_mapping"]["citation"]
