"""Seeded synthetic cohort. No real patient records and no direct identifiers."""

from __future__ import annotations

from dataclasses import dataclass

from guardian.contracts.budget import SUPPRESSION_THRESHOLD
from guardian.contracts.schemas import AgeBand, BmiBand, Region, SyntheticRow, Timing

_DATES: dict[str, tuple[str | None, str | None]] = {
    "before_diagnosis": ("2019-03-01", "2020-11-01"),
    "after_diagnosis": ("2021-04-01", "2020-11-01"),
    "unknown": (None, "2020-11-01"),
    "no_diagnosis": ("2020-01-15", None),
}


@dataclass(frozen=True)
class _Block:
    n: int
    sex: str
    age_band: str
    region: str
    breast_cancer: bool
    icd10: tuple[str, ...]
    bmi_band: str
    timing: str
    atc: tuple[str, ...] = ()
    loinc: tuple[str, ...] = ()


# 225 rows. Counts are the source for guardian/evidence/expected_counts.json.
# Female breast-cancer obese aged 50+ is 20 + 12 + 10 = 42.
# Male underweight breast cancer is 3, below the suppression threshold.
BLOCKS: tuple[_Block, ...] = (
    _Block(20, "female", "50-59", "Wien", True, ("C50",), "obese", "before_diagnosis", (), ("39156-5",)),
    _Block(12, "female", "50-59", "Wien", True, ("C50",), "obese", "after_diagnosis", (), ("39156-5",)),
    _Block(10, "female", "60-69", "Ost", True, ("C50.9",), "obese", "unknown", (), ()),
    _Block(18, "female", "40-49", "Süd", True, ("C50",), "overweight", "before_diagnosis", ("L02BG",), ("39156-5",)),
    _Block(16, "female", "70+", "West", True, ("C50",), "normal", "before_diagnosis"),
    _Block(6, "female", "18-39", "Nord", True, ("C50",), "underweight", "before_diagnosis"),
    _Block(8, "female", "50-59", "Nord", True, ("C50",), "unknown", "unknown"),
    _Block(10, "male", "60-69", "Ost", True, ("C50",), "obese", "before_diagnosis", (), ("39156-5",)),
    _Block(3, "male", "50-59", "West", True, ("C50",), "underweight", "after_diagnosis"),
    _Block(4, "male", "40-49", "Wien", True, ("C50",), "overweight", "after_diagnosis"),
    _Block(30, "female", "40-49", "Ost", False, ("E66",), "obese", "no_diagnosis", (), ("39156-5",)),
    _Block(25, "female", "18-39", "Süd", False, (), "normal", "no_diagnosis"),
    _Block(22, "male", "70+", "Nord", False, ("E11",), "overweight", "no_diagnosis"),
    _Block(20, "male", "50-59", "Wien", False, (), "normal", "no_diagnosis"),
    _Block(8, "female", "60-69", "West", False, (), "underweight", "no_diagnosis"),
    _Block(7, "male", "18-39", "Ost", False, (), "unknown", "no_diagnosis"),
    _Block(6, "female", "70+", "Nord", False, ("C44",), "normal", "no_diagnosis"),
)

_ROWS: tuple[SyntheticRow, ...] | None = None


def load_rows() -> tuple[SyntheticRow, ...]:
    global _ROWS
    if _ROWS is None:
        _ROWS = tuple(_build_rows())
    return _ROWS


def _build_rows() -> list[SyntheticRow]:
    rows: list[SyntheticRow] = []
    for block in BLOCKS:
        measured_on, diagnosis_on = _DATES[block.timing]
        for _ in range(block.n):
            rows.append(
                SyntheticRow(
                    row_id=f"syn-{len(rows):04d}",
                    sex=block.sex,  # type: ignore[arg-type]
                    age_band=AgeBand(block.age_band),
                    region=Region(block.region),
                    breast_cancer=block.breast_cancer,
                    icd10=list(block.icd10),
                    bmi_band=BmiBand(block.bmi_band),
                    bmi_measured_on=measured_on,
                    diagnosis_on=diagnosis_on,
                    measurement_timing=Timing(block.timing),
                    atc=list(block.atc),
                    loinc=list(block.loinc),
                )
            )
    if len(rows) < 200:
        raise RuntimeError("synthetic fixture must contain at least 200 rows")
    small = [
        row
        for row in rows
        if row.sex == "male" and row.bmi_band == BmiBand.underweight and row.breast_cancer
    ]
    if len(small) >= SUPPRESSION_THRESHOLD:
        raise RuntimeError("synthetic fixture lost its suppressed small cell")
    return rows
