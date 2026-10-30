"""Connect interpreter, purpose guardian, and evidence checks for one request.

Tool budget for an allowed query: code_lookup, describe_dataset, execute_aggregate.
Keyword rejects do not call a model and do not execute an aggregate.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from guardian.contracts.budget import DEFAULT_MODEL, MAX_TOOL_STEPS, REQUEST_DEADLINE_S
from guardian.contracts.dataset import CAUSATION_LIMITATION_TEXT
from guardian.contracts.schemas import (
    AgentRequest,
    AgentResponse,
    AuditRecord,
    Interpretation,
    PolicyDecision,
    PurposeCategory,
    TurnRecord,
)
from guardian.api.sessions import SessionStore
from guardian.evidence.executor import MockQueryExecutor
from guardian.evidence.investigator import EvidenceInvestigator
from guardian.nlp.interpreter import Interpreter
from guardian.nlp.normalizer import Normalizer
from guardian.purpose.audit import InMemoryAuditLog
from guardian.purpose.guardian import PurposeGuardian

_NEUTRAL = Interpretation(proposed_purpose=PurposeCategory.public_health_research)


class ModelUnavailableError(RuntimeError):
    """EU Mistral could not be reached. Mapped to HTTP 503."""


class DeadlineExceededError(TimeoutError):
    """Request exceeded the hard deadline. Mapped to HTTP 504."""


class UnknownDatasetError(KeyError):
    """Dataset id is not the synthetic demo set."""


class Orchestrator:
    def __init__(
        self,
        *,
        client: Any | None = None,
        guardian: PurposeGuardian | None = None,
        executor: MockQueryExecutor | None = None,
        investigator: EvidenceInvestigator | None = None,
        audit_log: InMemoryAuditLog | None = None,
        deadline_s: float = REQUEST_DEADLINE_S,
    ) -> None:
        self._client = client
        self._guardian = guardian or PurposeGuardian()
        self._executor = executor or MockQueryExecutor()
        self._investigator = investigator or EvidenceInvestigator()
        self.audit_log = audit_log or InMemoryAuditLog()
        self._deadline_s = deadline_s
        self._normalizer = Normalizer.from_default_lexicon()
        self._store = SessionStore()

    def handle(self, request: AgentRequest) -> AgentResponse:
        started = time.perf_counter()
        if self._deadline_s <= 0:
            raise DeadlineExceededError("deadline exceeded before work started")
        session = self._store.resolve(request)
        precheck = self._guardian.evaluate(request, session, _NEUTRAL)
        use_model = self._client is not None and precheck.outcome != "reject"
        if use_model:
            self._require_model()
        self._check_deadline(started)
        interpreter = Interpreter(
            client=self._client if use_model else None,
            normalizer=self._normalizer,
            dataset_id=request.dataset_id,
        )
        interpretation = interpreter.interpret(request.message, session)
        policy = self._guardian.evaluate(request, session, interpretation)
        decision = self._decision(policy, interpretation)
        evidence = None
        aggregate = None
        tool_steps = 1
        if decision == "allow" and interpretation.query is not None:
            self._check_deadline(started)
            remaining = MAX_TOOL_STEPS - tool_steps
            try:
                evidence = self._investigator.investigate(
                    interpretation.query,
                    self._executor,
                    max_steps=remaining,
                )
            except KeyError as exc:
                raise UnknownDatasetError(str(exc)) from exc
            aggregate = self._investigator.last_aggregate
            tool_steps += evidence.tool_steps_used
        if tool_steps > MAX_TOOL_STEPS:
            raise RuntimeError("tool step budget exceeded")

        explanation = _explanation(decision, policy, interpretation, evidence)
        limitations = _limitations(decision, evidence)
        audit = self._audit(
            request,
            session,
            policy,
            interpretation.query,
            aggregate.query_fingerprint if aggregate is not None else None,
            evidence,
        )
        session.turns.append(
            TurnRecord(
                message=request.message,
                decision=decision,
                purpose_category=policy.purpose_category,
                query=interpretation.query if decision == "allow" else None,
            )
        )
        session.purpose_category = policy.purpose_category
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return AgentResponse(
            session_id=session.session_id,
            decision=decision,
            policy=policy,
            interpretation=interpretation,
            evidence=evidence,
            aggregate=aggregate,
            explanation=explanation,
            audit_id=audit.audit_id,
            limitations=limitations,
            model=DEFAULT_MODEL if use_model else None,
            tool_steps=tool_steps,
            latency_ms=elapsed_ms,
            live_model=use_model,
        )

    def _require_model(self) -> None:
        try:
            models = self._client.list_models()
        except Exception as exc:  # noqa: BLE001 — transport failure becomes 503
            raise ModelUnavailableError("EU Mistral endpoint is unavailable") from exc
        if not models:
            raise ModelUnavailableError("EU Mistral returned no models")

    def _check_deadline(self, started: float) -> None:
        if time.perf_counter() - started > self._deadline_s:
            raise DeadlineExceededError("request deadline exceeded")

    @staticmethod
    def _decision(policy: PolicyDecision, interpretation: Interpretation) -> str:
        if policy.outcome == "reject":
            return "reject"
        if interpretation.needs_clarification or interpretation.query is None:
            return "clarify"
        return "allow"

    def _audit(
        self,
        request: AgentRequest,
        session,
        policy: PolicyDecision,
        query,
        fingerprint: str | None,
        evidence,
    ) -> AuditRecord:
        summary: list[str] = ["code_lookup: lexicon normalizer"]
        if evidence is not None:
            summary.extend(evidence.missing_evidence)
            summary.extend(evidence.unsupported_claims)
        record = AuditRecord(
            audit_id=str(uuid.uuid4()),
            session_id=session.session_id,
            recorded_at=datetime.now(timezone.utc).isoformat(),
            request_message=request.message,
            role=request.role,
            stated_purpose=request.stated_purpose,
            policy=policy,
            query=query,
            aggregate_fingerprint=fingerprint,
            evidence_summary=summary,
        )
        self.audit_log.append(record)
        return record


def _explanation(decision: str, policy: PolicyDecision, interpretation: Interpretation, evidence) -> str:
    parts: list[str] = []
    if decision == "reject":
        parts.append(f"Request refused ({policy.reason_code.value}).")
        for item in policy.evidence:
            parts.append(item if item.endswith(".") else f"{item}.")
        parts.append("No aggregate was executed.")
        return " ".join(parts)
    if decision == "clarify":
        question = interpretation.clarification_question or "Please clarify the population, condition, or measure."
        parts.append(question)
        if policy.outcome == "allow":
            parts.append("The stated purpose is permitted, but the question is not a complete aggregate query.")
        parts.append("No aggregate was executed.")
        return " ".join(parts)
    parts.append("Request allowed for a synthetic public-health aggregate.")
    if evidence is not None:
        parts.extend(evidence.supported_conclusions)
        parts.extend(evidence.unsupported_claims)
        if evidence.missing_evidence:
            parts.append("Missing evidence: " + "; ".join(evidence.missing_evidence))
        parts.append(
            f"Temporal feasibility: {evidence.temporal_feasibility}. Causation is not supported."
        )
    return " ".join(parts)


def _limitations(decision: str, evidence) -> list[str]:
    limits = ["Synthetic fixture only. These figures are not real patient statistics."]
    if evidence is not None and CAUSATION_LIMITATION_TEXT in evidence.unsupported_claims:
        limits.append(CAUSATION_LIMITATION_TEXT)
    if decision == "reject":
        limits.append("Forbidden purposes do not receive aggregate results.")
    return limits
