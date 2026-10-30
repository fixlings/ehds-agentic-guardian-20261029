import json
from pathlib import Path

from ehds import evidence, memory
from ehds.pipeline import handle

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "requests" / "demo"
REQ = json.loads((DEMO / "D06.json").read_text())
BANDS = json.loads((DEMO / "D07.json").read_text())


class Fake:
    model_id = "fake"

    def __init__(self, reply):
        self.reply = reply

    def complete_json(self, **_):
        return self.reply


def test_differencing_pair_is_held_back():
    first = handle(REQ, create_workspace=False)
    assert "held_back" not in first
    second = handle(BANDS, create_workspace=False)
    assert second["held_back"][0]["earlier_request"] == "D06"
    assert second["answer"]["40+"]["women_with_breast_cancer"] == "suppressed"
    other_org = handle(dict(BANDS, id="M3", applicant={"organisation": "Other", "country": "AT"}), create_workspace=False)
    assert "held_back" not in other_org and other_org["answer"]["40+"]["women_with_breast_cancer"] == 111


def test_repeating_the_same_request_is_not_held_back():
    handle(REQ, create_workspace=False)
    assert "held_back" not in handle(REQ, create_workspace=False)


def test_purpose_change_is_flagged():
    handle(REQ, create_workspace=False)
    out = handle(dict(REQ, id="M4", purpose="We are an insurer and want to set premiums for women with breast cancer."),
                 create_workspace=False)
    assert out["decision"] == "REJECT" and any("Purpose change" in f for f in out["staff"]["flags"])
    assert out["staff"]["earlier_requests"][0]["id"] == "D06"


def test_evidence_caveats_and_model_note():
    req = dict(REQ, question="Does obesity cause breast cancer?")
    assert any("cause" in c for c in evidence.fixed_caveats(req, {"n": "<10"}))
    ev = evidence.investigate(req, {"n": 42}, Fake({"supports": "no", "can_show": "a", "cannot_show": "b", "next_steps": ["x"]}))
    assert ev["status"] == "ok" and ev["supports"] == "no" and ev["next_steps"] == ["x"]


def test_memory_holds_no_ids_in_output():
    out = handle(REQ, create_workspace=False)
    assert "p-" not in json.dumps(out["answer"]) and "released_groups" not in json.dumps(out, default=str)
    assert memory.history(dict(REQ, id="Z"))[0]["id"] == "D06"
