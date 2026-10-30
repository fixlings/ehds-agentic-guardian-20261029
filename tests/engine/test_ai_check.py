import json
from pathlib import Path

import pytest

from ehds import ai_check
from ehds.judge import decide
from ehds.pipeline import handle

ROOT = Path(__file__).resolve().parents[2]
BASE = {"id": "T1", "requested_as": "health data request", "definitions": ["Type 2 diabetes = ICD-10 code starting with E11."],
        "question": "How many patients have type 2 diabetes, by region?"}


class FakeModel:
    model_id = "fake"

    def __init__(self, reply):
        self.reply = reply

    def complete_json(self, **_):
        return self.reply


@pytest.fixture(autouse=True)
def _clear_cache():
    ai_check._cache.clear()


@pytest.mark.parametrize("purpose", [
    "We are a health insurer and want to adjust the monthly fees of members in regions with many diabetics.",
    "Our sales representatives should visit the doctors in regions with the most diabetics to boost sales.",
    "As an employer we want to know which workers will get sick so we can plan who to let go.",
    "Our bank wants to decide who gets a mortgage based on chronic disease.",
    "We are developing a new vodka brand.",
    "Please tell me whether my neighbour Max Huber has diabetes.",
    "Just curious.",
])
def test_rules_reject_rephrased_forbidden_or_purposeless_requests(purpose):
    assert decide(dict(BASE, purpose=purpose)).decision == "REJECT"


@pytest.mark.parametrize("f", sorted((ROOT / "requests" / "demo").glob("D0[123].json")))
def test_demo_bad_requests_rejected_by_rules_alone(f):
    assert decide(json.loads(f.read_text())).decision == "REJECT"


def test_ai_rejects_with_verified_quote():
    req = dict(BASE, purpose="Research on diabetes for our premium customers programme.")
    ai = ai_check.review(req, FakeModel({"prohibited": True, "category": "marketing", "prohibited_quote": "premium customers programme",
                                         "allowed_purpose": "none", "reason": "Marketing."}))
    d = ai_check.apply(decide(req), ai, req)
    assert d.decision == "REJECT" and d.articles == ["54"]


def test_ai_unverified_quote_only_flags():
    req = dict(BASE, purpose="Research on regional diabetes care.")
    ai = ai_check.review(req, FakeModel({"prohibited": True, "category": "marketing", "prohibited_quote": "invented words"}))
    d = ai_check.apply(decide(req), ai, req)
    assert d.decision == "APPROVE_STATISTICAL" and any("could not quote" in f for f in d.flags)


def test_ai_cannot_approve_what_rules_reject():
    req = dict(BASE, purpose="Just curious.")
    ai = ai_check.review(req, FakeModel({"prohibited": False, "category": "none", "allowed_purpose": "scientific_research"}))
    assert ai_check.apply(decide(req), ai, req).decision == "REJECT"


def test_ai_downgrades_unjustified_records():
    req = dict(BASE, requested_as="health data access application", purpose="Research on diabetes care. Aggregates are not enough.")
    assert decide(req).decision == "APPROVE_SPE"
    out = handle(req, create_workspace=False, ai_client=FakeModel({"prohibited": False, "category": "none",
                 "allowed_purpose": "scientific_research", "records_justified": False, "reason": "No reason given."}))
    assert out["decision"] == "APPROVE_STATISTICAL" and "workspace" not in out


def test_records_without_reason_get_statistics():
    req = json.loads((ROOT / "requests" / "demo" / "D04.json").read_text())
    assert decide(req).decision == "APPROVE_STATISTICAL"


def test_model_outage_falls_back_to_rules():
    class Broken:
        def complete_json(self, **_):
            raise TimeoutError
    out = handle(json.loads((ROOT / "requests" / "R01.json").read_text()), create_workspace=False, ai_client=Broken())
    assert out["decision"] == "APPROVE_STATISTICAL" and out["ai_check"]["status"] == "unavailable"
