import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from navigator_app import create_app

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



def test_ui_is_served_without_external_assets(client):
    page = client.get("/")
    assert page.status_code == 200 and 'src="/formal.js"' in page.text
    assert "https://" not in page.text and "API_KEY" not in page.text
    assert client.get("/formal.js").status_code == 200
    assert client.get("/v1/requests/ai-status").json() == {"enabled": False}


def test_bad_definitions_do_not_crash(client):
    r = _req("R01")
    r["definitions"] = {"thing": "something that cannot be computed"}
    r["question"] = "Tell me something."
    assert client.post("/v1/requests", json=r).status_code == 200
