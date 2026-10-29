"""Lock the shared contract. These tests do not call a model or an executor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from guardian.contracts import (
    ROUTE_METHOD,
    ROUTE_PATH,
    AgentRequest,
    AgentResponse,
    ArticleMapping,
    CAUSATION_LIMITATION_TEXT,
    FIXTURE_VERSION,
    MISTRAL_EU_BASE_URL,
    SYNTHETIC_DATASET_ID,
    StructuredQuery,
    canonical_dumps,
    dataset_description,
    query_fingerprint,
)
from guardian.contracts.schemas import (
    AgeBand,
    BmiBand,
    CodedFilter,
    CodeSystem,
    EvidenceAssessment,
    GroupBy,
    Interpretation,
    Measure,
    PolicyDecision,
    PopulationFilters,
    PurposeCategory,
    ReasonCode,
    RequesterRole,
    ToolStepRecord,
)

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "tests" / "acceptance" / "prompts.json"
LEXICON = ROOT / "guardian" / "contracts" / "lexicon.json"
POLICY = ROOT / "guardian" / "contracts" / "policy_minimum.json"


def test_route_and_eu_host() -> None:
    assert ROUTE_PATH == "/v1/agent/request"
    assert ROUTE_METHOD == "POST"
    assert MISTRAL_EU_BASE_URL == "https://api.eu.mistral.ai"
    assert "api.mistral.ai" not in MISTRAL_EU_BASE_URL


def test_prompts_cover_required_cases() -> None:
    prompts = json.loads(PROMPTS.read_text(encoding="utf-8"))
    assert len(prompts) >= 20
    ids = [item["id"] for item in prompts]
    assert len(ids) == len(set(ids))
    tags = {tag for item in prompts for tag in item["tags"]}
    for required in ("negation", "injection", "insurance", "employment", "follow-up", "causation"):
        assert required in tags
    for item in prompts:
        AgentRequest(
            message=item["turns"][0]["message"],
            role=item["turns"][0]["role"],
            stated_purpose=item["turns"][0]["stated_purpose"],
        )


def test_lexicon_codes_are_the_only_codes() -> None:
    lexicon = json.loads(LEXICON.read_text(encoding="utf-8"))
    codes = {(row["system"], row["code"]) for row in lexicon["codes"]}
    assert ("ICD-10", "C50") in codes
    assert ("ICD-10", "C44") in codes
    assert ("ICD-10", "E11") in codes
    assert ("ICD-10", "E66") in codes
    assert ("LOINC", "39156-5") in codes
    assert ("ATC", "L02BG") in codes
    assert "XYZ999" not in json.dumps(lexicon)


def test_policy_minimum_precedence_starts_with_injection() -> None:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    assert policy["precedence"][0] == "prompt_injection"
    assert policy["article_mapping"] == "unresolved"
    assert "insurer" in policy["roles_always_reject"]


def test_sql_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        StructuredQuery.model_validate(
            {
                "dataset_id": SYNTHETIC_DATASET_ID,
                "measures": ["count"],
                "sql": "select * from patients",
            }
        )


def test_unresolved_article_cannot_carry_a_citation() -> None:
    with pytest.raises(ValidationError):
        ArticleMapping(status="unresolved", citation="EHDS Article 99", source_url="https://example.invalid")


def test_example_round_trip(tmp_path: Path) -> None:
    query = StructuredQuery(
        dataset_id=SYNTHETIC_DATASET_ID,
        measures=[Measure.count],
        group_by=[GroupBy.bmi_band],
        filters=PopulationFilters(
            sex="female",
            age_bands=[AgeBand.a50_59, AgeBand.a60_69, AgeBand.a70_plus],
            bmi_bands=[BmiBand.obese],
            breast_cancer=True,
            coded=[
                CodedFilter(
                    system=CodeSystem.icd10,
                    code="C50",
                    display="Malignant neoplasm of breast",
                    source_text="breast cancer",
                )
            ],
        ),
        user_claim="obesity causes breast cancer",
    )
    interpretation = Interpretation(
        proposed_purpose=PurposeCategory.public_health_research,
        query=query,
    )
    policy = PolicyDecision(
        outcome="allow",
        rule_id="allow.public_health",
        reason_code=ReasonCode.allowed_public_health,
        purpose_category=PurposeCategory.public_health_research,
        purpose_changed=False,
        role=RequesterRole.public_health_researcher,
        evidence=["stated purpose is public-health surveillance"],
    )
    evidence = EvidenceAssessment(
        supported_conclusions=["Synthetic counts are available for this aggregate."],
        unsupported_claims=[CAUSATION_LIMITATION_TEXT],
        missing_evidence=["Measurement time is missing or after diagnosis for part of the cohort."],
        temporal_feasibility="insufficient",
        association_supported=True,
        checks_performed=[
            ToolStepRecord(name="describe_dataset", ok=True, detail="synthetic-breast-cancer-demo"),
            ToolStepRecord(name="execute_aggregate", ok=True, detail="fingerprint"),
        ],
        tool_steps_used=2,
    )
    response = AgentResponse(
        session_id="11111111-1111-1111-1111-111111111111",
        decision="allow",
        policy=policy,
        interpretation=interpretation,
        evidence=evidence,
        explanation=CAUSATION_LIMITATION_TEXT,
        audit_id="audit-example-1",
        limitations=["Synthetic fixture. Not a real cancer registry."],
        model="mistral-small-2603",
        tool_steps=2,
        latency_ms=10,
        live_model=False,
    )
    request = AgentRequest(
        message="How many women aged 50 or older have breast cancer and BMI>=30?",
        role=RequesterRole.public_health_researcher,
        stated_purpose="Public-health surveillance of cancer prevalence",
        dataset_id=SYNTHETIC_DATASET_ID,
    )
    parsed_request = AgentRequest.model_validate_json(request.model_dump_json())
    parsed_response = AgentResponse.model_validate_json(response.model_dump_json())
    assert parsed_request.dataset_id == SYNTHETIC_DATASET_ID
    assert parsed_response.evidence is not None
    assert parsed_response.evidence.causation_supported is False
    assert parsed_response.policy.authoritative is True
    fingerprint = query_fingerprint(query, FIXTURE_VERSION)
    assert fingerprint == query_fingerprint(
        StructuredQuery.model_validate_json(canonical_dumps(query)),
        FIXTURE_VERSION,
    )
    description = dataset_description()
    assert description.synthetic is True
    assert description.supports_temporal_order is False
    examples = tmp_path
    (examples / "agent_request.json").write_text(request.model_dump_json(indent=2), encoding="utf-8")
    (examples / "agent_response.json").write_text(response.model_dump_json(indent=2), encoding="utf-8")
    AgentRequest.model_validate_json((examples / "agent_request.json").read_text(encoding="utf-8"))
    AgentResponse.model_validate_json((examples / "agent_response.json").read_text(encoding="utf-8"))
