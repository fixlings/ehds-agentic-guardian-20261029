"""Evidence investigator refuses causal claims and reports missing timing."""

from __future__ import annotations

from guardian.contracts.dataset import CAUSATION_LIMITATION_TEXT, SYNTHETIC_DATASET_ID
from guardian.contracts.schemas import (
    BmiBand,
    GroupBy,
    Measure,
    PopulationFilters,
    StructuredQuery,
    Timing,
)
from guardian.evidence.executor import MockQueryExecutor
from guardian.evidence.investigator import EvidenceInvestigator


def _query(**kwargs) -> StructuredQuery:
    payload = {"dataset_id": SYNTHETIC_DATASET_ID, "measures": [Measure.count]}
    payload.update(kwargs)
    return StructuredQuery(**payload)


def test_causation_is_never_supported_when_timing_is_mixed() -> None:
    investigator = EvidenceInvestigator()
    query = _query(
        filters=PopulationFilters(
            breast_cancer=True,
            bmi_bands=[BmiBand.overweight, BmiBand.obese],
        ),
        user_claim="Prove that obesity causes breast cancer",
    )
    evidence = investigator.investigate(query, MockQueryExecutor())
    assert evidence.causation_supported is False
    assert evidence.temporal_feasibility == "insufficient"
    assert evidence.association_supported is True
    assert CAUSATION_LIMITATION_TEXT in evidence.unsupported_claims
    assert any("timing" in item.lower() for item in evidence.missing_evidence)
    assert evidence.tool_steps_used == 2
    assert [step.name for step in evidence.checks_performed] == ["describe_dataset", "execute_aggregate"]
    assert investigator.last_aggregate is not None
    assert investigator.last_aggregate.synthetic is True


def test_temporal_question_without_bmi_is_not_applicable() -> None:
    evidence = EvidenceInvestigator().investigate(
        _query(filters=PopulationFilters(sex="female")),
        MockQueryExecutor(),
    )
    assert evidence.temporal_feasibility == "not_applicable"
    assert evidence.unsupported_claims == []
    assert evidence.causation_supported is False
    assert evidence.association_supported is True


def test_exclusive_before_timing_is_sufficient_for_order_only() -> None:
    evidence = EvidenceInvestigator().investigate(
        _query(
            filters=PopulationFilters(timing=[Timing.before_diagnosis]),
            user_claim="Did the measurement come before diagnosis?",
            group_by=[GroupBy.measurement_timing],
        ),
        MockQueryExecutor(),
    )
    assert evidence.temporal_feasibility == "sufficient_for_order"
    assert evidence.causation_supported is False
    assert CAUSATION_LIMITATION_TEXT in evidence.unsupported_claims


def test_budget_below_two_steps_skips_execution_and_lists_missing_evidence() -> None:
    investigator = EvidenceInvestigator()
    evidence = investigator.investigate(
        _query(filters=PopulationFilters(breast_cancer=True)),
        MockQueryExecutor(),
        max_steps=1,
    )
    assert evidence.tool_steps_used == 1
    assert [step.name for step in evidence.checks_performed] == ["describe_dataset"]
    assert investigator.last_aggregate is None
    assert evidence.association_supported is False
    assert any("not executed" in item.lower() for item in evidence.missing_evidence)
    assert evidence.causation_supported is False
