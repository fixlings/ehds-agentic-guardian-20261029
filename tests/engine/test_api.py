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
    assert page.status_code == 200 and 'src="/formal.js' in page.text
    assert "https://" not in page.text and "API_KEY" not in page.text
    assert client.get("/formal.js").status_code == 200
    assert client.get("/v1/requests/ai-status").json() == {"enabled": False}


def test_bad_definitions_do_not_crash(client):
    r = _req("R01")
    r["definitions"] = {"thing": "something that cannot be computed"}
    r["question"] = "Tell me something."
    assert client.post("/v1/requests", json=r).status_code == 200


def test_staff_list_and_workspace_info(client):
    from server import api

    api.HISTORY.clear()
    client.post("/v1/requests", json=_req("R01"))
    client.post("/v1/requests", json=_req("R05"))
    rows = client.get("/v1/staff/requests").json()
    assert [r["id"] for r in rows] == ["R05", "R01"]
    assert rows[0]["asked"] == "Records" and rows[0]["decision"] == "APPROVE_SPE"
    detail = client.get(f"/v1/staff/requests/{rows[0]['seq']}").json()
    assert detail["request"]["id"] == "R05" and detail["result"]["workspace"]["downloads"] == "blocked"
    assert client.get("/v1/staff/requests/999").status_code == 404
    info = client.get("/v1/workspaces/R05").json()
    assert {t["name"] for t in info["tables"]} >= {"cohort", "conditions", "medications", "observations"}
    assert client.get("/v1/workspaces/NOPE").status_code == 404


def test_form_records_choice_opens_workspace_with_chosen_tables(client):
    req = _req("R05")
    req.update(id="F1", question="Pseudonymised data for adults with type 2 diabetes.", requested_records=["encounters"])
    body = client.post("/v1/requests", json=req).json()
    assert body["decision"] == "APPROVE_SPE"
    assert set(body["workspace"]["rows"]) == {"encounters"}


def test_records_request_without_cohort_opens_no_workspace(client):
    req = _req("R05")
    req.update(id="F2", purpose="We train a prediction model. Training needs individual-level histories; aggregates are not enough.",
               question="Provide pseudonymised records.", definitions=["Report the size of the cohort you would make available."])
    body = client.post("/v1/requests", json=req).json()
    assert "workspace" not in body and "error" in body["answer"]


def test_demo_d09_opens_secure_workspace(client):
    req = json.loads((ROOT / "requests" / "demo" / "D09.json").read_text(encoding="utf-8"))
    body = client.post("/v1/requests", json=req).json()
    assert body["decision"] == "APPROVE_SPE"
    assert set(body["workspace"]["rows"]) == {"conditions", "medications", "encounters"}
    assert body["verification"]["status"] == "match"


def test_workspace_shows_summaries_only(client):
    client.post("/v1/requests", json=_req("R05"))
    ok = client.post("/v1/workspaces/R05/query", json={"sql": "SELECT count(*) AS patients FROM cohort"})
    assert ok.status_code == 200
    for sql in ["SELECT * FROM cohort", "SELECT * FROM conditions LIMIT 5",
                "SELECT max(patient_id) AS x FROM cohort", "SELECT patient_id, count(*) FROM conditions GROUP BY patient_id"]:
        assert client.post("/v1/workspaces/R05/query", json={"sql": sql}).status_code == 403, sql


def test_rejected_request_can_ask_for_manual_review(client):
    from server import api

    api.HISTORY.clear()
    rej = client.post("/v1/requests", json=_req("R02")).json()
    ok = client.post("/v1/requests", json=_req("R01")).json()
    assert rej["decision"] == "REJECT" and ok["decision"] != "REJECT"
    assert client.post(f"/v1/requests/{ok['seq']}/appeal", json={"comment": "x"}).status_code == 409
    assert client.post(f"/v1/requests/{rej['seq']}/appeal", json={"comment": "We are a university."}).status_code == 200
    assert client.post(f"/v1/requests/{rej['seq']}/appeal", json={}).status_code == 409
    assert client.post("/v1/requests/999/appeal", json={}).status_code == 404
    rows = {r["seq"]: r for r in client.get("/v1/staff/requests").json()}
    assert rows[rej["seq"]]["appeal"] and not rows[ok["seq"]]["appeal"]
    detail = client.get(f"/v1/staff/requests/{rej['seq']}").json()
    assert detail["appeal"]["comment"] == "We are a university."
