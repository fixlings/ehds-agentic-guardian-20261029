"""Component boundaries. Implementations live in worker-owned packages.

Only `guardian.nlp.mistral_client` may call the Mistral API.
Purpose decisions and aggregate numbers are local and deterministic.
"""

from __future__ import annotations

from typing import Protocol

from guardian.contracts.schemas import (
    AgentRequest,
    AuditRecord,
    DatasetDescription,
    EvidenceAssessment,
    Interpretation,
    PolicyDecision,
    SessionContext,
    StructuredQuery,
    AggregateResult,
)


class ChatClient(Protocol):
    """EU Mistral chat. Tests pass a fake. The live client must use the EU host."""

    def list_models(self) -> list[str]:
        """Model ids returned by the EU endpoint."""

    def complete_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        timeout_s: float,
    ) -> dict:
        """One JSON object. Caller validates it. One retry lives in the adapter, not here."""


class NaturalLanguageInterpreter(Protocol):
    def interpret(self, message: str, session: SessionContext) -> Interpretation:
        """Validate a structured query. Drop unknown codes. Do not execute a query."""


class QueryExecutor(Protocol):
    """Adapter boundary for the teammates' deterministic engine.

    Tomorrow, replace the mock with a client that implements this protocol.
    Callers must not change when that happens.
    """

    def describe(self, dataset_id: str) -> DatasetDescription:
        """Field list for the dataset. Raise KeyError for an unknown dataset."""

    def execute(self, query: StructuredQuery) -> AggregateResult:
        """Deterministic aggregate. Reject unknown dataset or unknown codes."""


class EvidenceInvestigator(Protocol):
    def investigate(
        self,
        query: StructuredQuery,
        executor: QueryExecutor,
        *,
        max_steps: int = 2,
    ) -> EvidenceAssessment:
        """Describe, then maybe execute. Never claim causation is supported."""


class PurposeGuardian(Protocol):
    def evaluate(
        self,
        request: AgentRequest,
        session: SessionContext,
        interpretation: Interpretation,
    ) -> PolicyDecision:
        """Deterministic allow/reject.

        A model flag may add a reject. It must never turn a rule reject into allow.
        """


class AuditLog(Protocol):
    def append(self, record: AuditRecord) -> None:
        """Persist one decision, including rejects."""

    def get(self, audit_id: str) -> AuditRecord:
        """Return the stored record. Raise KeyError if missing."""
