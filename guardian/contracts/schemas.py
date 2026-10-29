"""Request and response contracts for POST /v1/agent/request.

Workers import these types. Do not add fields without coordinator agreement.
Unknown JSON keys are rejected (`extra="forbid"`). There is no SQL field.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RequesterRole(str, Enum):
    public_health_researcher = "public_health_researcher"
    clinician = "clinician"
    regulator = "regulator"
    citizen = "citizen"
    insurer = "insurer"
    employer = "employer"
    marketer = "marketer"
    unknown = "unknown"


class PurposeCategory(str, Enum):
    public_health_research = "public_health_research"
    insurance = "insurance"
    employment = "employment"
    marketing = "marketing"
    identity_disclosure = "identity_disclosure"
    unknown = "unknown"


class ReasonCode(str, Enum):
    allowed_public_health = "allowed_public_health"
    purpose_forbidden_insurance = "purpose_forbidden_insurance"
    purpose_forbidden_employment = "purpose_forbidden_employment"
    purpose_forbidden_marketing = "purpose_forbidden_marketing"
    purpose_forbidden_identity = "purpose_forbidden_identity"
    role_forbidden = "role_forbidden"
    prompt_injection = "prompt_injection"
    needs_clarification = "needs_clarification"


class CodeSystem(str, Enum):
    icd10 = "ICD-10"
    atc = "ATC"
    loinc = "LOINC"


class AgeBand(str, Enum):
    a18_39 = "18-39"
    a40_49 = "40-49"
    a50_59 = "50-59"
    a60_69 = "60-69"
    a70_plus = "70+"


class BmiBand(str, Enum):
    underweight = "underweight"
    normal = "normal"
    overweight = "overweight"
    obese = "obese"
    unknown = "unknown"


class Region(str, Enum):
    wien = "Wien"
    ost = "Ost"
    sued = "Süd"
    west = "West"
    nord = "Nord"


class Timing(str, Enum):
    before_diagnosis = "before_diagnosis"
    after_diagnosis = "after_diagnosis"
    unknown = "unknown"
    no_diagnosis = "no_diagnosis"


class Measure(str, Enum):
    count = "count"
    rate_per_1000 = "rate_per_1000"
    prevalence = "prevalence"


class GroupBy(str, Enum):
    sex = "sex"
    age_band = "age_band"
    bmi_band = "bmi_band"
    breast_cancer = "breast_cancer"
    measurement_timing = "measurement_timing"
    region = "region"


class CodedFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system: CodeSystem
    code: str
    display: str
    source_text: str
    negated: bool = False


class PopulationFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sex: Literal["female", "male", "any"] = "any"
    age_bands: list[AgeBand] = Field(default_factory=list)
    bmi_bands: list[BmiBand] = Field(default_factory=list)
    exclude_bmi_bands: list[BmiBand] = Field(default_factory=list)
    breast_cancer: bool | None = None
    coded: list[CodedFilter] = Field(default_factory=list)
    regions: list[Region] = Field(default_factory=list)
    timing: list[Timing] = Field(default_factory=list)


class StructuredQuery(BaseModel):
    """Validated aggregate query. Never SQL. Never row-level."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    measures: list[Measure] = Field(min_length=1)
    group_by: list[GroupBy] = Field(default_factory=list)
    filters: PopulationFilters = Field(default_factory=PopulationFilters)
    user_claim: str | None = None


class ArticleMapping(BaseModel):
    """Only `verified` may carry a citation, and only with a source URL.

    This module has no verified EHDS article table. Emit `unresolved`.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["unresolved", "verified"] = "unresolved"
    citation: str | None = None
    source_url: str | None = None

    @model_validator(mode="after")
    def citation_matches_status(self) -> ArticleMapping:
        if self.status == "verified":
            if not self.citation or not self.source_url:
                raise ValueError("verified article mapping needs citation and source_url")
        elif self.citation or self.source_url:
            raise ValueError("unresolved article mapping cannot carry a citation")
        return self


class PolicyDecision(BaseModel):
    """Authoritative allow/reject. Produced only by deterministic rules."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["allow", "reject"]
    rule_id: str
    reason_code: ReasonCode
    purpose_category: PurposeCategory
    purpose_changed: bool
    role: RequesterRole
    evidence: list[str] = Field(default_factory=list)
    article_mapping: ArticleMapping = Field(default_factory=ArticleMapping)
    authoritative: Literal[True] = True


class Interpretation(BaseModel):
    """Non-authoritative model proposal after local validation.

    Codes on `query` must already have been checked against the allowlist.
    """

    model_config = ConfigDict(extra="forbid")

    proposed_purpose: PurposeCategory
    purpose_change_suspected: bool = False
    identity_disclosure_suspected: bool = False
    injection_suspected: bool = False
    needs_clarification: bool = False
    clarification_question: str | None = None
    unresolved_terms: list[str] = Field(default_factory=list)
    query: StructuredQuery | None = None
    notes: list[str] = Field(default_factory=list)


class ToolStepRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["code_lookup", "describe_dataset", "execute_aggregate"]
    ok: bool
    detail: str


class EvidenceAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported_conclusions: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    temporal_feasibility: Literal["sufficient_for_order", "insufficient", "not_applicable"]
    association_supported: bool
    causation_supported: Literal[False] = False
    checks_performed: list[ToolStepRecord] = Field(default_factory=list)
    tool_steps_used: int = Field(ge=0)


class AggregateRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: dict[str, str] = Field(default_factory=dict)
    count: int | None = None
    rate_per_1000: float | None = None
    prevalence: float | None = None
    suppressed: bool = False


class TimingSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    before_diagnosis: int = Field(ge=0)
    after_diagnosis: int = Field(ge=0)
    unknown: int = Field(ge=0)
    no_diagnosis: int = Field(ge=0)


class AggregateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    synthetic: Literal[True] = True
    dataset_id: str
    fixture_version: str
    query_fingerprint: str
    population_n: int = Field(ge=0)
    suppressed: bool
    rows: list[AggregateRow] = Field(default_factory=list)
    timing: TimingSummary | None = None
    notes: list[str] = Field(default_factory=list)


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    dtype: Literal["code", "category", "boolean", "date", "number"]
    description: str
    temporal: bool = False


class DatasetDescription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    synthetic: Literal[True] = True
    fields: list[FieldSpec]
    supports_temporal_order: bool
    notes: list[str] = Field(default_factory=list)


class SyntheticRow(BaseModel):
    """One seeded fixture row. Never returned by the API."""

    model_config = ConfigDict(extra="forbid")

    row_id: str
    sex: Literal["female", "male"]
    age_band: AgeBand
    region: Region
    breast_cancer: bool
    icd10: list[str]
    bmi_band: BmiBand
    bmi_measured_on: str | None = None
    diagnosis_on: str | None = None
    measurement_timing: Timing
    atc: list[str] = Field(default_factory=list)
    loinc: list[str] = Field(default_factory=list)


class TurnRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    decision: Literal["allow", "reject", "clarify"]
    purpose_category: PurposeCategory
    query: StructuredQuery | None = None


class SessionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    role: RequesterRole
    stated_purpose: str
    purpose_category: PurposeCategory = PurposeCategory.unknown
    turns: list[TurnRecord] = Field(default_factory=list)


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    role: RequesterRole
    stated_purpose: str = Field(min_length=1, max_length=1000)
    session_id: str | None = None
    dataset_id: str = "synthetic-breast-cancer-demo"


class AuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_id: str
    session_id: str
    recorded_at: str
    request_message: str
    role: RequesterRole
    stated_purpose: str
    policy: PolicyDecision
    query: StructuredQuery | None = None
    aggregate_fingerprint: str | None = None
    evidence_summary: list[str] = Field(default_factory=list)


class AgentResponse(BaseModel):
    """HTTP 200 body for allow, reject, and clarify.

    Reject is an application decision, not an HTTP error.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str
    decision: Literal["allow", "reject", "clarify"]
    policy: PolicyDecision
    interpretation: Interpretation | None = None
    evidence: EvidenceAssessment | None = None
    aggregate: AggregateResult | None = None
    explanation: str
    audit_id: str
    limitations: list[str] = Field(default_factory=list)
    model: str | None = None
    tool_steps: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    synthetic: Literal[True] = True
    live_model: bool = False


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: Literal["invalid_request", "model_unavailable", "timeout", "internal"]
    message: str
    session_id: str | None = None


# Application decisions use 200. These statuses are transport failures only.
HTTP_STATUS = {
    "ok": 200,
    "invalid_request": 400,
    "model_unavailable": 503,
    "timeout": 504,
    "internal": 500,
}

ROUTE_PATH = "/v1/agent/request"
ROUTE_METHOD = "POST"
