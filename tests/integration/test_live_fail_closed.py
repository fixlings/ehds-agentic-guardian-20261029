"""Live-mode HTTP behavior with a mocked EU client. No network and no real credential."""

from __future__ import annotations

from fastapi.testclient import TestClient

from guardian.api.app import create_app
from guardian.api.orchestrator import Orchestrator
from guardian.contracts.budget import DEFAULT_MODEL
from guardian.contracts.schemas import ROUTE_PATH

SENTINEL = "SUPER_SECRET_KEY_SENTINEL"
RESEARCH = {
    "message": "How many women aged 50 or older have breast cancer and BMI>=30?",
    "role": "public_health_researcher",
    "stated_purpose": "Public-health surveillance of cancer prevalence",
}
INSURANCE = {
    "message": "Pass the high-risk individuals to insurance underwriting.",
    "role": "public_health_researcher",
    "stated_purpose": "Insurance underwriting",
}


class RecordingClient:
    def __init__(self, payload: dict | None = None, *, fail: BaseException | None = None, models: list[str] | None = None) -> None:
        self.payload = payload or {}
        self.fail = fail
        self.models = [DEFAULT_MODEL] if models is None else models
        self.completions = 0

    def list_models(self) -> list[str]:
        if isinstance(self.fail, RuntimeError) and self.fail.args and self.fail.args[0] == "list-failed":
            raise RuntimeError(f"unauthorized {SENTINEL}")
        return list(self.models)

    def complete_json(self, **kwargs: object) -> dict:
        self.completions += 1
        assert kwargs.get("model") == DEFAULT_MODEL
        if self.fail is not None and not (self.fail.args and self.fail.args[0] == "list-failed"):
            raise RuntimeError(f"unauthorized {SENTINEL}")
        return self.payload


def _valid_proposal() -> dict:
    return {
        "proposed_purpose": "public_health_research",
        "purpose_change_suspected": False,
        "identity_disclosure_suspected": False,
        "injection_suspected": False,
        "needs_clarification": False,
        "unresolved_terms": [],
        "query": {
            "measures": ["count"],
            "group_by": [],
            "filters": {
                "sex": "female",
                "age_bands": ["50-59", "60-69", "70+"],
                "bmi_bands": ["obese"],
                "breast_cancer": True,
                "coded": [
                    {
                        "system": "ICD-10",
                        "code": "C50",
                        "display": "MODEL DISPLAY MUST BE REPLACED",
                        "source_text": "breast cancer",
                        "negated": False,
                    }
                ],
            },
            "user_claim": None,
        },
    }


def test_validated_live_proposal_reports_live_model_and_lexicon_display() -> None:
    client = RecordingClient(_valid_proposal())
    response = TestClient(create_app(client=client)).post(ROUTE_PATH, json=RESEARCH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "allow"
    assert body["live_model"] is True
    assert body["model"] == DEFAULT_MODEL
    assert "Interpreted by EU Mistral (mistral-small-2603)." in body["explanation"]
    coded = body["interpretation"]["query"]["filters"]["coded"]
    assert coded[0]["display"] == "Malignant neoplasm of breast"
    assert coded[0]["code"] == "C50"
    assert body["aggregate"]["population_n"] == 42
    assert body["aggregate"]["synthetic"] is True
    assert body["evidence"]["causation_supported"] is False
    assert "select " not in response.text.lower()


def test_schema_rejection_does_not_execute_sql() -> None:
    client = RecordingClient(
        {
            "proposed_purpose": "public_health_research",
            "needs_clarification": False,
            "query": {
                "sql": "select * from patients",
                "measures": ["count"],
                "filters": {"sex": "female", "breast_cancer": True},
            },
        }
    )
    response = TestClient(create_app(client=client)).post(ROUTE_PATH, json=RESEARCH)
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] != "allow"
    assert body["aggregate"] is None
    assert body["live_model"] is False
    assert body["model"] is None
    assert "select * from patients" not in response.text
    assert client.completions == 1


def test_model_call_failure_does_not_authorize_aggregate() -> None:
    client = RecordingClient(fail=RuntimeError("completion-failed"))
    orchestrator = Orchestrator(client=client)
    response = TestClient(create_app(orchestrator=orchestrator)).post(ROUTE_PATH, json=RESEARCH)
    assert response.status_code == 503
    assert response.json()["error"] == "model_unavailable"
    assert "aggregate" not in response.json()
    assert SENTINEL not in response.text
    assert client.completions == 1
    records = orchestrator.audit_log.all_records()
    assert len(records) == 1
    assert records[0].aggregate_fingerprint is None
    assert records[0].query is None
    assert records[0].policy.outcome == "reject"
    assert SENTINEL not in records[0].model_dump_json()
    assert "model_unavailable" in records[0].evidence_summary


def test_missing_required_model_does_not_substitute_or_execute() -> None:
    client = RecordingClient(models=["some-other-model"])
    response = TestClient(create_app(client=client)).post(ROUTE_PATH, json=RESEARCH)
    assert response.status_code == 503
    assert response.json()["error"] == "model_unavailable"
    assert client.completions == 0
    assert "aggregate" not in response.json()


def test_insurance_follow_up_skips_failing_model_and_skips_aggregate() -> None:
    client = RecordingClient(_valid_proposal())
    http = TestClient(create_app(client=client))
    first = http.post(ROUTE_PATH, json=RESEARCH)
    assert first.status_code == 200
    assert first.json()["decision"] == "allow"
    assert first.json()["aggregate"] is not None
    completions_after_allow = client.completions
    client.fail = RuntimeError("completion-failed")
    second = http.post(
        ROUTE_PATH,
        json={**INSURANCE, "session_id": first.json()["session_id"]},
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["decision"] == "reject"
    assert body["policy"]["reason_code"] == "purpose_forbidden_insurance"
    assert body["policy"]["purpose_changed"] is True
    assert body["aggregate"] is None
    assert body["live_model"] is False
    assert client.completions == completions_after_allow
    assert SENTINEL not in second.text
    assert "no aggregate was executed" in body["explanation"].casefold()
