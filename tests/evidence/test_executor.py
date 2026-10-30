"""Synthetic executor: invariants, suppression, code rules, stable fingerprints."""

from __future__ import annotations

import json
from pathlib import Path

from guardian.contracts.budget import FIXTURE_VERSION, SUPPRESSION_THRESHOLD
from guardian.contracts.dataset import FIXTURE_INVARIANTS, SYNTHETIC_DATASET_ID
from guardian.contracts.hashing import query_fingerprint
from guardian.contracts.schemas import (
    AgeBand,
    BmiBand,
    CodeSystem,
    CodedFilter,
    GroupBy,
    Measure,
    PopulationFilters,
    Region,
    StructuredQuery,
    Timing,
)
from guardian.evidence.executor import MockQueryExecutor
from guardian.evidence.fixture import load_rows

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = ROOT / "guardian" / "evidence" / "expected_counts.json"


def test_fixture_invariants() -> None:
    rows = load_rows()
    assert len(FIXTURE_INVARIANTS) >= 8
    assert len(rows) >= 200
    assert {row.sex for row in rows} == {"female", "male"}
    assert {row.breast_cancer for row in rows} == {True, False}
    assert {row.bmi_band for row in rows} == set(BmiBand)
    cancer = [row for row in rows if row.breast_cancer]
    timings = {row.measurement_timing for row in cancer}
    assert Timing.before_diagnosis in timings
    assert Timing.after_diagnosis in timings
    assert Timing.unknown in timings
    assert any("C44" in row.icd10 for row in rows)
    small = [
        row
        for row in rows
        if row.sex == "male" and row.bmi_band == BmiBand.underweight and row.breast_cancer
    ]
    assert len(small) < SUPPRESSION_THRESHOLD
    assert all(row.row_id.startswith("syn-") for row in rows)
    blob = " ".join(row.row_id for row in rows).lower()
    for forbidden in ("patient", "ssn", "insurance-id"):
        assert forbidden not in blob


def test_expected_counts_file() -> None:
    executor = MockQueryExecutor()
    document = json.loads(EXPECTED.read_text(encoding="utf-8"))
    assert document["fixture_version"] == FIXTURE_VERSION
    assert len(document["queries"]) == 3
    suppressed_groups = 0
    for case in document["queries"]:
        query = StructuredQuery.model_validate(case["query"])
        result = executor.execute(query)
        assert result.synthetic is True
        assert result.population_n == case["population_n"]
        assert result.suppressed is case["suppressed"]
        assert result.query_fingerprint == query_fingerprint(query, FIXTURE_VERSION)
        assert result.timing is not None
        for name, value in case["timing"].items():
            assert getattr(result.timing, name) == value
        assert len(result.rows) == len(case["rows"])
        for actual, expected in zip(result.rows, case["rows"], strict=True):
            assert actual.group == expected["group"]
            assert actual.count == expected["count"]
            assert actual.suppressed is expected["suppressed"]
            if actual.suppressed:
                suppressed_groups += 1
        assert "row_id" not in result.model_dump_json()
        assert any("synthetic" in note.lower() for note in result.notes)
    assert suppressed_groups >= 1


def test_same_query_is_deterministic() -> None:
    executor = MockQueryExecutor()
    query = StructuredQuery(
        dataset_id=SYNTHETIC_DATASET_ID,
        measures=[Measure.count],
        filters=PopulationFilters(sex="female", breast_cancer=True),
    )
    first = executor.execute(query)
    second = executor.execute(query)
    assert first.query_fingerprint == second.query_fingerprint
    assert first.population_n == second.population_n == 90


def test_icd_prefix_loinc_passthrough_and_negated_code() -> None:
    executor = MockQueryExecutor()
    all_rows = executor.execute(
        StructuredQuery(dataset_id=SYNTHETIC_DATASET_ID, measures=[Measure.count])
    )
    loinc = executor.execute(
        StructuredQuery(
            dataset_id=SYNTHETIC_DATASET_ID,
            measures=[Measure.count],
            filters=PopulationFilters(
                coded=[
                    CodedFilter(
                        system=CodeSystem.loinc,
                        code="39156-5",
                        display="Body mass index (BMI) [Ratio]",
                        source_text="39156-5",
                    )
                ]
            ),
        )
    )
    assert loinc.population_n == all_rows.population_n == 225

    specific = executor.execute(
        StructuredQuery(
            dataset_id=SYNTHETIC_DATASET_ID,
            measures=[Measure.count],
            filters=PopulationFilters(
                coded=[
                    CodedFilter(
                        system=CodeSystem.icd10,
                        code="C50.9",
                        display="Malignant neoplasm of breast, unspecified",
                        source_text="c50.9",
                    )
                ]
            ),
        )
    )
    assert specific.population_n == 10

    prefix = executor.execute(
        StructuredQuery(
            dataset_id=SYNTHETIC_DATASET_ID,
            measures=[Measure.count],
            filters=PopulationFilters(
                coded=[
                    CodedFilter(
                        system=CodeSystem.icd10,
                        code="C50",
                        display="Malignant neoplasm of breast",
                        source_text="breast cancer",
                    )
                ]
            ),
        )
    )
    assert prefix.population_n == 107

    without_skin = executor.execute(
        StructuredQuery(
            dataset_id=SYNTHETIC_DATASET_ID,
            measures=[Measure.count],
            filters=PopulationFilters(
                coded=[
                    CodedFilter(
                        system=CodeSystem.icd10,
                        code="C44",
                        display="Other and unspecified malignant neoplasm of skin",
                        source_text="skin cancer",
                        negated=True,
                    )
                ]
            ),
        )
    )
    assert without_skin.population_n == 219


def test_small_population_suppresses_entire_result() -> None:
    executor = MockQueryExecutor()
    result = executor.execute(
        StructuredQuery(
            dataset_id=SYNTHETIC_DATASET_ID,
            measures=[Measure.count, Measure.rate_per_1000],
            group_by=[GroupBy.region],
            filters=PopulationFilters(
                sex="male",
                breast_cancer=True,
                bmi_bands=[BmiBand.underweight],
                regions=[Region.west],
                age_bands=[AgeBand.a50_59],
                timing=[Timing.after_diagnosis],
            ),
        )
    )
    assert result.population_n == 3
    assert result.suppressed is True
    assert result.rows
    assert all(row.count is None and row.rate_per_1000 is None and row.suppressed for row in result.rows)


def test_unknown_dataset_raises() -> None:
    executor = MockQueryExecutor()
    try:
        executor.describe("not-the-demo-dataset")
    except KeyError:
        return
    raise AssertionError("unknown dataset should raise KeyError")
