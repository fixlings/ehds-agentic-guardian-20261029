"""Evidence checks. At most describe plus one aggregate. Causation is never supported."""

from __future__ import annotations

from guardian.contracts.budget import SUPPRESSION_THRESHOLD
from guardian.contracts.dataset import CAUSATION_LIMITATION_TEXT
from guardian.contracts.schemas import (
    AggregateResult,
    EvidenceAssessment,
    GroupBy,
    StructuredQuery,
    ToolStepRecord,
)
from guardian.contracts.ports import QueryExecutor

_ASSOCIATION = "This is an association in a synthetic fixture, not a causal finding."
_SKIPPED = "Aggregate was not executed because fewer than two evidence steps remained."
_NO_STEPS = "No tool steps remained to inspect the dataset."
_NO_ASSOCIATION = "No non-suppressed count was returned, so an association cannot be stated."
_TIMING_GAP = "Measurement timing is unknown or after diagnosis for part of the filtered cohort."
_ORDER_GAP = "Dataset description: temporal order is not supported for the cohort as a whole."


def _asks_time_or_causation(query: StructuredQuery) -> bool:
    if query.user_claim:
        return True
    if query.filters.timing:
        return True
    return GroupBy.measurement_timing in query.group_by


def _combines_breast_cancer_and_bmi(query: StructuredQuery) -> bool:
    filters = query.filters
    if filters.breast_cancer is not True:
        return False
    return bool(filters.bmi_bands or filters.exclude_bmi_bands)


def _needs_limitation(query: StructuredQuery) -> bool:
    return query.user_claim is not None or _combines_breast_cancer_and_bmi(query)


def _temporal(
    query: StructuredQuery,
    result: AggregateResult | None,
) -> str:
    asks = _asks_time_or_causation(query)
    combo = _combines_breast_cancer_and_bmi(query)
    if not asks and not combo:
        return "not_applicable"
    timing = None if result is None else result.timing
    if timing is None:
        return "insufficient"
    exclusive_before = (
        timing.before_diagnosis > 0
        and timing.after_diagnosis == 0
        and timing.unknown == 0
        and timing.no_diagnosis == 0
    )
    if exclusive_before:
        return "sufficient_for_order"
    return "insufficient"


def _association(result: AggregateResult | None) -> bool:
    if result is None or result.suppressed:
        return False
    return any(row.count is not None and not row.suppressed for row in result.rows)


class EvidenceInvestigator:
    """Describe the dataset, then optionally execute one aggregate."""

    def __init__(self) -> None:
        self.last_aggregate: AggregateResult | None = None

    def investigate(
        self,
        query: StructuredQuery,
        executor: QueryExecutor,
        *,
        max_steps: int = 2,
    ) -> EvidenceAssessment:
        self.last_aggregate = None
        unsupported = [CAUSATION_LIMITATION_TEXT] if _needs_limitation(query) else []
        asks_or_combo = _asks_time_or_causation(query) or _combines_breast_cancer_and_bmi(query)
        if max_steps < 1:
            return self._assessment(
                unsupported=unsupported,
                missing=[_NO_STEPS],
                temporal="insufficient" if asks_or_combo else "not_applicable",
                association=False,
                checks=[],
                steps=0,
            )

        description = executor.describe(query.dataset_id)
        checks = [
            ToolStepRecord(name="describe_dataset", ok=True, detail=description.dataset_id),
        ]
        missing: list[str] = []
        if asks_or_combo and not description.supports_temporal_order:
            missing.append(_ORDER_GAP)
        if max_steps < 2:
            missing.append(_SKIPPED)
            return self._assessment(
                unsupported=unsupported,
                missing=missing,
                temporal="insufficient" if asks_or_combo else "not_applicable",
                association=False,
                checks=checks,
                steps=1,
            )

        result = executor.execute(query)
        self.last_aggregate = result
        checks.append(
            ToolStepRecord(name="execute_aggregate", ok=True, detail=result.query_fingerprint)
        )
        association = _association(result)
        supported = [_ASSOCIATION] if association else []
        if not association:
            missing.append(_NO_ASSOCIATION)
        if result.timing and (result.timing.unknown > 0 or result.timing.after_diagnosis > 0) and asks_or_combo:
            missing.append(_TIMING_GAP)
        if result.suppressed or any(row.suppressed for row in result.rows):
            missing.append(f"Small-cell suppression applies below {SUPPRESSION_THRESHOLD} rows.")
        return self._assessment(
            supported=supported,
            unsupported=unsupported,
            missing=missing,
            temporal=_temporal(query, result),
            association=association,
            checks=checks,
            steps=2,
        )

    @staticmethod
    def _assessment(
        *,
        unsupported: list[str],
        missing: list[str],
        temporal: str,
        association: bool,
        checks: list[ToolStepRecord],
        steps: int,
        supported: list[str] | None = None,
    ) -> EvidenceAssessment:
        return EvidenceAssessment(
            supported_conclusions=supported or [],
            unsupported_claims=unsupported,
            missing_evidence=missing,
            temporal_feasibility=temporal,  # type: ignore[arg-type]
            association_supported=association,
            checks_performed=checks,
            tool_steps_used=steps,
        )
