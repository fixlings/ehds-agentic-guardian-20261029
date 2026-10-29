"""Canonical synthetic dataset. The mock executor and investigator share this."""

from __future__ import annotations

from guardian.contracts.budget import FIXTURE_VERSION, SUPPRESSION_THRESHOLD
from guardian.contracts.schemas import DatasetDescription, FieldSpec

SYNTHETIC_DATASET_ID = "synthetic-breast-cancer-demo"

CAUSATION_LIMITATION_TEXT = (
    "An association between elevated BMI and breast cancer in this synthetic dataset "
    "does not establish that BMI preceded the diagnosis or caused the cancer. "
    "Temporal order is incomplete when measurement timing is unknown or falls after diagnosis."
)

FIXTURE_INVARIANTS = (
    "At least 200 synthetic rows.",
    "Includes female and male.",
    "Includes breast_cancer true and false.",
    "Includes every BMI band, including unknown.",
    "Breast-cancer rows include before_diagnosis, after_diagnosis, and unknown timing.",
    "Includes at least one ICD-10 C44 code so skin-cancer exclusion is observable.",
    "Includes at least one aggregate group with n < suppression threshold.",
    "Contains no real patient records and no direct identifiers.",
    f"Small-cell suppression threshold is {SUPPRESSION_THRESHOLD}.",
    f"Fixture version is {FIXTURE_VERSION}.",
)


def dataset_description() -> DatasetDescription:
    return DatasetDescription(
        dataset_id=SYNTHETIC_DATASET_ID,
        fields=[
            FieldSpec(name="sex", dtype="category", description="female or male"),
            FieldSpec(name="age_band", dtype="category", description="18-39, 40-49, 50-59, 60-69, 70+"),
            FieldSpec(name="region", dtype="category", description="Synthetic Austrian regions"),
            FieldSpec(name="breast_cancer", dtype="boolean", description="True when an ICD-10 C50* code is present"),
            FieldSpec(name="icd10", dtype="code", description="Allowlisted ICD-10 codes on the row"),
            FieldSpec(name="bmi_band", dtype="category", description="underweight, normal, overweight, obese, unknown"),
            FieldSpec(name="bmi_measured_on", dtype="date", description="ISO date or null", temporal=True),
            FieldSpec(name="diagnosis_on", dtype="date", description="ISO date or null", temporal=True),
            FieldSpec(
                name="measurement_timing",
                dtype="category",
                description="before_diagnosis, after_diagnosis, unknown, or no_diagnosis",
                temporal=True,
            ),
            FieldSpec(name="atc", dtype="code", description="Allowlisted ATC codes"),
            FieldSpec(name="loinc", dtype="code", description="Allowlisted LOINC codes"),
        ],
        supports_temporal_order=False,
        notes=[
            "Synthetic demonstration fixture. Do not present results as real health statistics.",
            "supports_temporal_order is false for the cohort as a whole because timing is mixed and partly unknown.",
            CAUSATION_LIMITATION_TEXT,
        ],
    )
