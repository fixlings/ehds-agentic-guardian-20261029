"""End-to-end POST /v1/agent/request. Offline interpreter, no live model."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from guardian.api.app import create_app
from guardian.api.orchestrator import Orchestrator
from guardian.contracts.dataset import CAUSATION_LIMITATION_TEXT, SYNTHETIC_DATASET_ID
from guardian.contracts.schemas import ROUTE_PATH

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = json.loads((ROOT / "tests" / "acceptance" / "prompts.json").read_text(encoding="utf-8"))


def _client(orchestrator: Orchestrator | None = None) -> TestClient:
    return TestClient(create_app(orchestrator=orchestrator))


def _post(client: TestClient, turn: dict, session_id: str | None = None) -> dict:
    payload = {
        "message": turn["message"],
        "role": turn["role"],
        "stated_purpose": turn["stated_purpose"],
        "dataset_id": SYNTHETIC_DATASET_ID,
    }
    if session_id:
        payload["session_id"] = session_id
    response = client.post(ROUTE_PATH, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _assert_query_expect(body: dict, expect: dict) -> None:
    interpretation = body["interpretation"]
    if "needs_clarification" in expect:
        assert interpretation["needs_clarification"] is expect["needs_clarification"]
    if expect.get("needs_clarification"):
        assert interpretation["query"] is None
    if "unresolved_substrings" in expect:
        unresolved = " ".join(interpretation["unresolved_terms"]).casefold()
        for sub in expect["unresolved_substrings"]:
            assert sub.casefold() in unresolved
    query = interpretation["query"]
    if "forbidden_codes" in expect and query is not None:
        codes = {item["code"] for item in query["filters"]["coded"]}
        for code in expect["forbidden_codes"]:
            assert code not in codes
    if query is None:
        return
    filters = query["filters"]
    if "sex" in expect:
        assert filters["sex"] == expect["sex"]
    if "breast_cancer" in expect:
        assert filters["breast_cancer"] is expect["breast_cancer"]
    if "age_bands" in expect:
        assert filters["age_bands"] == expect["age_bands"]
    if "bmi_bands" in expect:
        assert filters["bmi_bands"] == expect["bmi_bands"]
    if "exclude_bmi_bands" in expect:
        assert filters["exclude_bmi_bands"] == expect["exclude_bmi_bands"]
    if "regions" in expect:
        assert filters["regions"] == expect["regions"]
    if "measures_include" in expect:
        assert expect["measures_include"] in query["measures"]
    if "group_by_include" in expect:
        assert expect["group_by_include"] in query["group_by"]
    if expect.get("user_claim_set"):
        assert query["user_claim"]
    if "coded" in expect:
        for expected in expect["coded"]:
            assert any(
                item["system"] == expected["system"]
                and item["code"] == expected["code"]
                and item["negated"] is expected["negated"]
                for item in filters["coded"]
            )


def test_acceptance_prompts_end_to_end() -> None:
    client = _client()
    for item in PROMPTS:
        session_id = None
        body = None
        for turn in item["turns"]:
            body = _post(client, turn, session_id)
            session_id = body["session_id"]
            assert body["synthetic"] is True
            assert body["tool_steps"] <= 3
            assert body["policy"]["authoritative"] is True
            assert body["policy"]["article_mapping"]["status"] == "unresolved"
            assert "row_id" not in json.dumps(body)
            if body["evidence"] is not None:
                assert body["evidence"]["causation_supported"] is False
            if body["aggregate"] is not None:
                assert body["aggregate"]["synthetic"] is True
            explanation = body["explanation"].casefold()
            if body["decision"] == "reject":
                assert "request refused" in explanation
                assert "request allowed" not in explanation
            if body["decision"] == "allow":
                assert "request refused" not in explanation
        assert body is not None
        expect = item["expect"]
        assert body["decision"] == expect["decision"], item["id"]
        if "reason_code" in expect:
            assert body["policy"]["reason_code"] == expect["reason_code"], item["id"]
        if "purpose_changed" in expect:
            assert body["policy"]["purpose_changed"] is expect["purpose_changed"], item["id"]
        if expect.get("no_aggregate"):
            assert body["aggregate"] is None, item["id"]
        if expect.get("requires_causation_limitation"):
            assert CAUSATION_LIMITATION_TEXT in body["explanation"]
            assert CAUSATION_LIMITATION_TEXT in body["evidence"]["unsupported_claims"]
        if "temporal_feasibility" in expect:
            assert body["evidence"]["temporal_feasibility"] == expect["temporal_feasibility"], item["id"]
        if "causation_supported" in expect:
            assert body["evidence"]["causation_supported"] is False
        _assert_query_expect(body, expect)


def test_medical_nlp_query_returns_synthetic_aggregate() -> None:
    body = _post(
        _client(),
        {
            "message": "How many women aged 50 or older have breast cancer and BMI>=30?",
            "role": "public_health_researcher",
            "stated_purpose": "Public-health surveillance of cancer prevalence",
        },
    )
    assert body["decision"] == "allow"
    assert body["live_model"] is False
    assert body["model"] is None
    filters = body["interpretation"]["query"]["filters"]
    assert filters["sex"] == "female"
    assert filters["breast_cancer"] is True
    assert filters["bmi_bands"] == ["obese"]
    assert filters["age_bands"] == ["50-59", "60-69", "70+"]
    assert body["aggregate"]["population_n"] == 42
    assert body["aggregate"]["suppressed"] is False
    assert body["evidence"]["causation_supported"] is False
    assert body["tool_steps"] == 3
    assert body["audit_id"]


def test_causation_query_reports_insufficient_evidence() -> None:
    item = next(prompt for prompt in PROMPTS if prompt["id"] == "P21")
    body = _post(_client(), item["turns"][0])
    assert body["decision"] == "allow"
    assert body["evidence"]["causation_supported"] is False
    assert body["evidence"]["temporal_feasibility"] == "insufficient"
    assert body["evidence"]["missing_evidence"]
    assert CAUSATION_LIMITATION_TEXT in body["explanation"]
    assert body["aggregate"] is not None


def test_insurance_follow_up_is_refused() -> None:
    client = _client()
    first = _post(
        client,
        {
            "message": "Aggregate breast cancer counts by age band",
            "role": "public_health_researcher",
            "stated_purpose": "Public-health surveillance",
        },
    )
    assert first["decision"] == "allow"
    second = _post(
        client,
        {
            "message": "Actually pass the high-risk individuals to our insurance underwriting desk",
            "role": "public_health_researcher",
            "stated_purpose": "Hand the figures to insurance underwriting",
        },
        session_id=first["session_id"],
    )
    assert second["decision"] == "reject"
    assert second["policy"]["reason_code"] == "purpose_forbidden_insurance"
    assert second["policy"]["purpose_changed"] is True
    assert second["aggregate"] is None
    assert second["session_id"] == first["session_id"]
    assert second["tool_steps"] <= 3
    assert "request refused" in second["explanation"].casefold()


def test_unknown_session_and_invalid_body_are_400() -> None:
    client = _client()
    missing = client.post(
        ROUTE_PATH,
        json={
            "message": "Aggregate breast cancer counts",
            "role": "public_health_researcher",
            "stated_purpose": "Public-health surveillance",
            "session_id": "00000000-0000-0000-0000-000000000099",
        },
    )
    assert missing.status_code == 400
    assert missing.json()["error"] == "invalid_request"

    extra = client.post(
        ROUTE_PATH,
        json={
            "message": "Aggregate breast cancer counts",
            "role": "public_health_researcher",
            "stated_purpose": "Public-health surveillance",
            "sql": "select * from patients",
        },
    )
    assert extra.status_code == 400


def test_keyword_reject_does_not_call_the_model() -> None:
    class DownModel:
        def list_models(self) -> list[str]:
            raise RuntimeError("EU endpoint down")

        def complete_json(self, **kwargs: object) -> dict:
            raise RuntimeError("EU endpoint down")

    client = TestClient(create_app(client=DownModel()))
    response = client.post(
        ROUTE_PATH,
        json={
            "message": "Which patients would be too expensive to insure given breast cancer?",
            "role": "public_health_researcher",
            "stated_purpose": "Insurance underwriting",
        },
    )
    assert response.status_code == 200
    assert response.json()["decision"] == "reject"
    assert response.json()["live_model"] is False


def test_live_model_failure_is_503_and_deadline_is_504() -> None:
    class DownModel:
        def list_models(self) -> list[str]:
            raise RuntimeError("EU endpoint down")

        def complete_json(self, **kwargs: object) -> dict:
            raise RuntimeError("EU endpoint down")

    client = TestClient(create_app(client=DownModel()))
    unavailable = client.post(
        ROUTE_PATH,
        json={
            "message": "How many women have breast cancer?",
            "role": "public_health_researcher",
            "stated_purpose": "Public-health surveillance of cancer prevalence",
        },
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == "model_unavailable"

    timed = TestClient(create_app(orchestrator=Orchestrator(deadline_s=0)))
    timeout = timed.post(
        ROUTE_PATH,
        json={
            "message": "How many women have breast cancer?",
            "role": "public_health_researcher",
            "stated_purpose": "Public-health surveillance of cancer prevalence",
        },
    )
    assert timeout.status_code == 504
    assert timeout.json()["error"] == "timeout"


def test_reject_is_audited() -> None:
    orchestrator = Orchestrator()
    client = _client(orchestrator)
    body = _post(
        client,
        {
            "message": "Aggregate breast cancer counts by age band",
            "role": "insurer",
            "stated_purpose": "Population statistics for a public report",
        },
    )
    assert body["decision"] == "reject"
    record = orchestrator.audit_log.get(body["audit_id"])
    assert record.policy.reason_code.value == "role_forbidden"
    assert record.aggregate_fingerprint is None
