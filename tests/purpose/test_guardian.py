"""Purpose guardian tests against acceptance prompts and edge cases."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from guardian.contracts.schemas import (
    AgentRequest,
    Interpretation,
    PurposeCategory,
    ReasonCode,
    RequesterRole,
    SessionContext,
    TurnRecord,
)
from guardian.purpose.audit import InMemoryAuditLog, decide_and_record
from guardian.purpose.guardian import PurposeGuardian

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "tests" / "acceptance" / "prompts.json"

PURPOSE_PROMPT_IDS = ["P13", "P14", "P15", "P16", "P17", "P18", "P19", "P20", "P23"]


def _neutral_interpretation() -> Interpretation:
    return Interpretation(proposed_purpose=PurposeCategory.public_health_research)


def _load_prompt(prompt_id: str) -> dict:
    prompts = json.loads(PROMPTS.read_text(encoding="utf-8"))
    for item in prompts:
        if item["id"] == prompt_id:
            return item
    raise KeyError(prompt_id)


def _session_after_first_turn(prompt: dict) -> SessionContext:
    """Build session state after an allowed public-health first turn (P19/P20)."""
    first = prompt["turns"][0]
    return SessionContext(
        session_id=str(uuid.uuid4()),
        role=first["role"],
        stated_purpose=first["stated_purpose"],
        purpose_category=PurposeCategory.public_health_research,
        turns=[
            TurnRecord(
                message=first["message"],
                decision="allow",
                purpose_category=PurposeCategory.public_health_research,
                query=None,
            )
        ],
    )


@pytest.fixture
def guardian() -> PurposeGuardian:
    return PurposeGuardian()


@pytest.mark.parametrize("prompt_id", PURPOSE_PROMPT_IDS)
def test_acceptance_purpose_prompts(guardian: PurposeGuardian, prompt_id: str) -> None:
    prompt = _load_prompt(prompt_id)
    turns = prompt["turns"]
    expect = prompt["expect"]

    if len(turns) == 1:
        turn = turns[0]
        session = SessionContext(
            session_id=str(uuid.uuid4()),
            role=turn["role"],
            stated_purpose=turn["stated_purpose"],
        )
    else:
        turn = turns[-1]
        session = _session_after_first_turn(prompt)
        session = session.model_copy(
            update={
                "role": turn["role"],
                "stated_purpose": turn["stated_purpose"],
            }
        )

    request = AgentRequest(
        message=turn["message"],
        role=turn["role"],
        stated_purpose=turn["stated_purpose"],
    )
    decision = guardian.evaluate(request, session, _neutral_interpretation())

    assert decision.outcome == expect["decision"]
    assert decision.reason_code == ReasonCode(expect["reason_code"])
    if "purpose_changed" in expect:
        assert decision.purpose_changed is expect["purpose_changed"]
    assert decision.article_mapping.status == "unresolved"
    assert decision.article_mapping.citation is None
    assert decision.article_mapping.source_url is None


def test_clean_public_health_message_allowed(guardian: PurposeGuardian) -> None:
    request = AgentRequest(
        message="Aggregate breast cancer counts by BMI band for public health reporting",
        role=RequesterRole.public_health_researcher,
        stated_purpose="Public-health surveillance of cancer prevalence",
    )
    session = SessionContext(
        session_id=str(uuid.uuid4()),
        role=request.role,
        stated_purpose=request.stated_purpose,
    )
    decision = guardian.evaluate(request, session, _neutral_interpretation())

    assert decision.outcome == "allow"
    assert decision.reason_code == ReasonCode.allowed_public_health
    assert decision.purpose_category == PurposeCategory.public_health_research
    assert decision.purpose_changed is False


def test_model_injection_flag_rejects_allowed_keywords(guardian: PurposeGuardian) -> None:
    request = AgentRequest(
        message="Aggregate breast cancer counts by age band for public health reporting",
        role=RequesterRole.public_health_researcher,
        stated_purpose="Public-health surveillance",
    )
    session = SessionContext(
        session_id=str(uuid.uuid4()),
        role=request.role,
        stated_purpose=request.stated_purpose,
    )
    interpretation = Interpretation(
        proposed_purpose=PurposeCategory.public_health_research,
        injection_suspected=True,
    )
    decision = guardian.evaluate(request, session, interpretation)

    assert decision.outcome == "reject"
    assert decision.reason_code == ReasonCode.prompt_injection


def test_rule_reject_not_overridden_by_clean_model(guardian: PurposeGuardian) -> None:
    request = AgentRequest(
        message="Which patients would be too expensive to insure given breast cancer and BMI?",
        role=RequesterRole.public_health_researcher,
        stated_purpose="Cost analysis for our health plan underwriting",
    )
    session = SessionContext(
        session_id=str(uuid.uuid4()),
        role=request.role,
        stated_purpose=request.stated_purpose,
    )
    interpretation = Interpretation(proposed_purpose=PurposeCategory.public_health_research)
    decision = guardian.evaluate(request, session, interpretation)

    assert decision.outcome == "reject"
    assert decision.reason_code == ReasonCode.purpose_forbidden_insurance


def test_reject_appends_audit_record(guardian: PurposeGuardian) -> None:
    audit_log = InMemoryAuditLog()
    request = AgentRequest(
        message="Aggregate breast cancer counts by age band",
        role=RequesterRole.insurer,
        stated_purpose="Population statistics for a public report",
    )
    session = SessionContext(
        session_id=str(uuid.uuid4()),
        role=request.role,
        stated_purpose=request.stated_purpose,
    )
    decision, record = decide_and_record(
        guardian,
        audit_log,
        request,
        session,
        _neutral_interpretation(),
    )

    assert decision.outcome == "reject"
    assert record.policy.outcome == "reject"
    fetched = audit_log.get(record.audit_id)
    assert fetched.audit_id == record.audit_id
    assert fetched.policy.reason_code == ReasonCode.role_forbidden
