from guardian.contracts.schemas import CodedFilter, PopulationFilters, StructuredQuery
from guardian.evidence.track3_executor import TRACK3_DATASET_ID, Track3QueryExecutor

from ehds import data


def _q(**kw) -> StructuredQuery:
    filters = kw.pop("filters", PopulationFilters())
    return StructuredQuery(dataset_id=TRACK3_DATASET_ID, measures=kw.pop("measures", ["count"]), filters=filters, **kw)


def test_counts_match_independent_sql_and_exclude_opt_outs():
    coded = [CodedFilter(system="ICD-10", code="E11", display="T2D", source_text="diabetes")]
    result = Track3QueryExecutor().execute(_q(filters=PopulationFilters(coded=coded)))
    expected = data.query(
        "SELECT count(DISTINCT c.patient_id) n FROM raw_conditions c JOIN raw_patients p USING (patient_id) "
        "WHERE c.icd10 LIKE 'E11%' AND lower(p.opted_out) <> 'true'"
    ).n[0]
    assert result.rows[0].count == expected


def test_every_group_listed_and_small_groups_hidden_below_10():
    filters = PopulationFilters(sex="female", breast_cancer=True)
    result = Track3QueryExecutor().execute(_q(group_by=["region", "bmi_band"], measures=["count", "prevalence"], filters=filters))
    assert len(result.rows) == 9 * 5
    for row in result.rows:
        if row.suppressed:
            assert row.count is None and row.prevalence is None
        else:
            assert row.count >= 10


def test_unknown_dataset_rejected():
    import pytest

    with pytest.raises(KeyError):
        Track3QueryExecutor().execute(_q().model_copy(update={"dataset_id": "nope"}))
