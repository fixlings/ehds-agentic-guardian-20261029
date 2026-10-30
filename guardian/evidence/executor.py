"""Deterministic aggregate executor over the synthetic fixture."""

from __future__ import annotations

from collections import Counter, defaultdict

from guardian.contracts.budget import FIXTURE_VERSION, SUPPRESSION_THRESHOLD
from guardian.contracts.dataset import SYNTHETIC_DATASET_ID, dataset_description
from guardian.contracts.hashing import query_fingerprint
from guardian.contracts.schemas import (
    AggregateResult,
    AggregateRow,
    CodeSystem,
    DatasetDescription,
    GroupBy,
    Measure,
    PopulationFilters,
    StructuredQuery,
    SyntheticRow,
    Timing,
    TimingSummary,
)
from guardian.evidence.fixture import load_rows


def _code_hit(row_codes: list[str], wanted: str) -> bool:
    wanted_u = wanted.upper()
    for code in row_codes:
        code_u = code.upper()
        if code_u == wanted_u or code_u.startswith(wanted_u + "."):
            return True
    return False


def _row_matches(row: SyntheticRow, filters: PopulationFilters) -> bool:
    if filters.sex != "any" and row.sex != filters.sex:
        return False
    if filters.age_bands and row.age_band not in filters.age_bands:
        return False
    if filters.bmi_bands and row.bmi_band not in filters.bmi_bands:
        return False
    if filters.exclude_bmi_bands and row.bmi_band in filters.exclude_bmi_bands:
        return False
    if filters.breast_cancer is not None and row.breast_cancer is not filters.breast_cancer:
        return False
    if filters.regions and row.region not in filters.regions:
        return False
    if filters.timing and row.measurement_timing not in filters.timing:
        return False
    for coded in filters.coded:
        if coded.system == CodeSystem.loinc:
            continue
        codes = row.icd10 if coded.system == CodeSystem.icd10 else row.atc
        hit = _code_hit(codes, coded.code)
        if coded.negated and hit:
            return False
        if not coded.negated and not hit:
            return False
    return True


def _group_value(row: SyntheticRow, key: GroupBy) -> str:
    if key == GroupBy.sex:
        return row.sex
    if key == GroupBy.age_band:
        return row.age_band.value
    if key == GroupBy.bmi_band:
        return row.bmi_band.value
    if key == GroupBy.breast_cancer:
        return "true" if row.breast_cancer else "false"
    if key == GroupBy.measurement_timing:
        return row.measurement_timing.value
    if key == GroupBy.region:
        return row.region.value
    raise ValueError(f"unsupported group_by {key}")


class MockQueryExecutor:
    """Same query and fixture version always produce the same fingerprint."""

    def __init__(self, rows: tuple[SyntheticRow, ...] | None = None) -> None:
        self._rows = rows if rows is not None else load_rows()

    def describe(self, dataset_id: str) -> DatasetDescription:
        if dataset_id != SYNTHETIC_DATASET_ID:
            raise KeyError(dataset_id)
        return dataset_description()

    def execute(self, query: StructuredQuery) -> AggregateResult:
        if query.dataset_id != SYNTHETIC_DATASET_ID:
            raise KeyError(query.dataset_id)
        matched = [row for row in self._rows if _row_matches(row, query.filters)]
        population_n = len(matched)
        whole_suppressed = population_n < SUPPRESSION_THRESHOLD
        grouped: dict[tuple[tuple[str, str], ...], list[SyntheticRow]] = defaultdict(list)
        for row in matched:
            key = tuple((group.value, _group_value(row, group)) for group in query.group_by)
            grouped[key].append(row)
        if not query.group_by:
            grouped = {(): matched}

        measures = set(query.measures)
        aggregate_rows: list[AggregateRow] = []
        for key in sorted(grouped):
            members = grouped[key]
            count_n = len(members)
            suppressed = whole_suppressed or count_n < SUPPRESSION_THRESHOLD
            count = None if suppressed else count_n
            rate = None
            prevalence = None
            if not suppressed and population_n:
                if Measure.rate_per_1000 in measures:
                    rate = round(count_n * 1000.0 / population_n, 4)
                if Measure.prevalence in measures:
                    prevalence = round(count_n / population_n, 6)
            aggregate_rows.append(
                AggregateRow(
                    group=dict(key),
                    count=count,
                    rate_per_1000=rate,
                    prevalence=prevalence,
                    suppressed=suppressed,
                )
            )

        timing_counts = Counter(row.measurement_timing for row in matched)
        notes = [
            "Synthetic demonstration figures. Not real health statistics.",
            f"Fixture version {FIXTURE_VERSION}.",
        ]
        if whole_suppressed:
            notes.append(
                f"Counts suppressed because the filtered population is below {SUPPRESSION_THRESHOLD}."
            )
        return AggregateResult(
            dataset_id=query.dataset_id,
            fixture_version=FIXTURE_VERSION,
            query_fingerprint=query_fingerprint(query, FIXTURE_VERSION),
            population_n=population_n,
            suppressed=whole_suppressed,
            rows=aggregate_rows,
            timing=TimingSummary(
                before_diagnosis=timing_counts[Timing.before_diagnosis],
                after_diagnosis=timing_counts[Timing.after_diagnosis],
                unknown=timing_counts[Timing.unknown],
                no_diagnosis=timing_counts[Timing.no_diagnosis],
            ),
            notes=notes,
        )
