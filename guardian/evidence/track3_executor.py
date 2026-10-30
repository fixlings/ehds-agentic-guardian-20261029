"""Aggregate executor over the official Track 3 data (20,000 synthetic patients).

Uses the opt-out-filtered DuckDB views from `ehds.data`, so opted-out patients
never enter any count. Small-cell suppression follows the Track 3 rule (< 10).
"""

from __future__ import annotations

import itertools
from collections import Counter
from functools import lru_cache

import pandas as pd

from ehds import data
from ehds.vocab import REGIONS
from guardian.contracts.budget import TRACK3_SUPPRESSION_THRESHOLD
from guardian.contracts.hashing import query_fingerprint
from guardian.contracts.schemas import (
    AgeBand,
    AggregateResult,
    AggregateRow,
    BmiBand,
    CodeSystem,
    DatasetDescription,
    FieldSpec,
    GroupBy,
    Measure,
    PopulationFilters,
    Region,
    StructuredQuery,
    Timing,
    TimingSummary,
)

TRACK3_DATASET_ID = "track3-ehds-20k"
TRACK3_VERSION = "track3-2026-10"
BMI_LOINC = "39156-5"
BREAST_CANCER_ICD10 = "C50"

# Guardian chat regions are coarse; map them to Austrian NUTS-1 groups of federal states.
REGION_STATES: dict[Region, tuple[str, ...]] = {
    Region.wien: ("Wien",),
    Region.ost: ("Wien", "Niederösterreich", "Burgenland"),
    Region.sued: ("Steiermark", "Kärnten"),
    Region.west: ("Oberösterreich", "Salzburg", "Tirol", "Vorarlberg"),
    Region.nord: ("Oberösterreich", "Niederösterreich"),
}

_BASE_SQL = f"""
WITH bmi AS (
    SELECT patient_id, value, date,
           row_number() OVER (PARTITION BY patient_id ORDER BY date DESC) AS rn
    FROM observations WHERE loinc = '{BMI_LOINC}'
), bc AS (
    SELECT patient_id, min(onset_date) AS onset
    FROM conditions WHERE icd10 LIKE '{BREAST_CANCER_ICD10}%' GROUP BY patient_id
)
SELECT p.patient_id, p.sex, p.age, p.region, b.value AS bmi, b.date AS bmi_date, bc.onset
FROM patients p
LEFT JOIN bmi b ON b.patient_id = p.patient_id AND b.rn = 1
LEFT JOIN bc ON bc.patient_id = p.patient_id
"""


def _age_band(age: int) -> str | None:
    if age < 18:
        return None
    if age < 40:
        return AgeBand.a18_39.value
    if age < 50:
        return AgeBand.a40_49.value
    if age < 60:
        return AgeBand.a50_59.value
    if age < 70:
        return AgeBand.a60_69.value
    return AgeBand.a70_plus.value


def _bmi_band(value: float | None) -> str:
    if value is None or pd.isna(value):
        return BmiBand.unknown.value
    if value < 18.5:
        return BmiBand.underweight.value
    if value < 25:
        return BmiBand.normal.value
    if value < 30:
        return BmiBand.overweight.value
    return BmiBand.obese.value


def _timing(row) -> str:
    if pd.isna(row.onset):
        return Timing.no_diagnosis.value
    if pd.isna(row.bmi_date):
        return Timing.unknown.value
    return Timing.before_diagnosis.value if row.bmi_date < row.onset else Timing.after_diagnosis.value


def _sex(value: str) -> str:
    v = str(value).strip().lower()
    return "female" if v.startswith("f") or v == "w" else "male"


@lru_cache(maxsize=1)
def _patients() -> pd.DataFrame:
    df = data.query(_BASE_SQL)
    df["sex"] = df["sex"].map(_sex)
    df["age_band"] = df["age"].map(_age_band)
    df["bmi_band"] = df["bmi"].map(_bmi_band)
    df["breast_cancer"] = df["onset"].notna().map(lambda b: "true" if b else "false")
    df["measurement_timing"] = df.apply(_timing, axis=1)
    return df.set_index("patient_id", drop=False)


@lru_cache(maxsize=256)
def _code_patients(system: CodeSystem, code: str) -> frozenset[str]:
    table, column = {
        CodeSystem.icd10: ("conditions", "icd10"),
        CodeSystem.atc: ("medications", "atc"),
        CodeSystem.loinc: ("observations", "loinc"),
    }[system]
    if system == CodeSystem.loinc:
        sql = f"SELECT DISTINCT patient_id FROM {table} WHERE {column} = ?"
        params = [code]
    else:
        sql = f"SELECT DISTINCT patient_id FROM {table} WHERE upper({column}) LIKE ?"
        params = [code.upper().replace(".", "") + "%"]
    return frozenset(data.query(sql, params)["patient_id"])


def _filter(df: pd.DataFrame, f: PopulationFilters) -> pd.DataFrame:
    if f.sex != "any":
        df = df[df.sex == f.sex]
    if f.age_bands:
        df = df[df.age_band.isin([b.value for b in f.age_bands])]
    if f.bmi_bands:
        df = df[df.bmi_band.isin([b.value for b in f.bmi_bands])]
    if f.exclude_bmi_bands:
        df = df[~df.bmi_band.isin([b.value for b in f.exclude_bmi_bands])]
    if f.breast_cancer is not None:
        df = df[df.breast_cancer == ("true" if f.breast_cancer else "false")]
    if f.regions:
        states = {s for r in f.regions for s in REGION_STATES[r]}
        df = df[df.region.isin(states)]
    if f.timing:
        df = df[df.measurement_timing.isin([t.value for t in f.timing])]
    for coded in f.coded:
        hit = df.patient_id.isin(_code_patients(coded.system, coded.code))
        df = df[~hit] if coded.negated else df[hit]
    return df


def _domain(key: GroupBy) -> list[str]:
    return {
        GroupBy.sex: ["female", "male"],
        GroupBy.age_band: [b.value for b in AgeBand],
        GroupBy.bmi_band: [b.value for b in BmiBand],
        GroupBy.breast_cancer: ["true", "false"],
        GroupBy.measurement_timing: [t.value for t in Timing],
        GroupBy.region: sorted(REGIONS),
    }[key]


class Track3QueryExecutor:
    """`QueryExecutor` over the Track 3 CSVs. Every requested group is returned, even empty ones."""

    suppression_threshold = TRACK3_SUPPRESSION_THRESHOLD

    def describe(self, dataset_id: str) -> DatasetDescription:
        if dataset_id != TRACK3_DATASET_ID:
            raise KeyError(dataset_id)
        return DatasetDescription(
            dataset_id=TRACK3_DATASET_ID,
            fields=[
                FieldSpec(name="sex", dtype="category", description="female or male"),
                FieldSpec(name="age_band", dtype="category", description="age in 2025, banded"),
                FieldSpec(name="region", dtype="category", description="Austrian federal state"),
                FieldSpec(name="icd10", dtype="code", description="diagnoses (conditions.csv)"),
                FieldSpec(name="atc", dtype="code", description="medications (medications.csv)"),
                FieldSpec(name="loinc", dtype="code", description="measurements (observations.csv)"),
                FieldSpec(name="bmi_band", dtype="category", description="latest BMI reading (LOINC 39156-5)"),
                FieldSpec(name="bmi_date", dtype="date", description="date of latest BMI", temporal=True),
                FieldSpec(name="diagnosis_onset", dtype="date", description="first C50 onset", temporal=True),
            ],
            supports_temporal_order=True,
            notes=[
                "Official Track 3 synthetic data: 20,000 patients; opted-out patients removed.",
                "Timing compares the latest BMI date with the first breast-cancer onset date.",
            ],
        )

    def execute(self, query: StructuredQuery) -> AggregateResult:
        if query.dataset_id != TRACK3_DATASET_ID:
            raise KeyError(query.dataset_id)
        t = self.suppression_threshold
        matched = _filter(_patients(), query.filters)
        population_n = len(matched)
        whole_suppressed = population_n < t
        keys = [g.value for g in query.group_by]
        counts = Counter(map(tuple, matched[keys].astype(str).values)) if keys else Counter({(): population_n})
        combos = itertools.product(*[_domain(g) for g in query.group_by]) if keys else [()]
        measures = set(query.measures)
        rows: list[AggregateRow] = []
        for combo in combos:
            n = counts.get(tuple(combo), 0)
            suppressed = whole_suppressed or n < t
            rate = prevalence = None
            if not suppressed and population_n:
                if Measure.rate_per_1000 in measures:
                    rate = round(n * 1000.0 / population_n, 1)
                if Measure.prevalence in measures:
                    prevalence = round(n / population_n, 3)
            rows.append(
                AggregateRow(
                    group=dict(zip(keys, combo)),
                    count=None if suppressed else n,
                    rate_per_1000=rate,
                    prevalence=prevalence,
                    suppressed=suppressed,
                )
            )
        tc = Counter(matched.measurement_timing)
        notes = [
            "Official Track 3 synthetic data (not real patients); opted-out patients excluded.",
            f"Counts below {t} are shown as <{t}; every requested group is listed, even if empty.",
        ]
        if whole_suppressed:
            notes.append(f"All counts suppressed because the filtered population is below {t}.")
        return AggregateResult(
            dataset_id=TRACK3_DATASET_ID,
            fixture_version=TRACK3_VERSION,
            query_fingerprint=query_fingerprint(query, TRACK3_VERSION),
            population_n=population_n,
            suppressed=whole_suppressed,
            rows=rows,
            timing=TimingSummary(**{k.value: tc.get(k.value, 0) for k in Timing}),
            notes=notes,
        )
