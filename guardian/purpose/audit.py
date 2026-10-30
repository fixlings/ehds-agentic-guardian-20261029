"""In-memory audit log for policy decisions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from guardian.contracts.schemas import (
    AgentRequest,
    AuditRecord,
    Interpretation,
    PolicyDecision,
    SessionContext,
    StructuredQuery,
)
from guardian.purpose.guardian import PurposeGuardian


class InMemoryAuditLog:
    """Append-only audit store keyed by audit_id."""

    def __init__(self) -> None:
        self._records: dict[str, AuditRecord] = {}

    def append(self, record: AuditRecord) -> None:
        self._records[record.audit_id] = record

    def get(self, audit_id: str) -> AuditRecord:
        try:
            return self._records[audit_id]
        except KeyError as exc:
            raise KeyError(audit_id) from exc

    def all_records(self) -> list[AuditRecord]:
        return list(self._records.values())


def decide_and_record(
    guardian: PurposeGuardian,
    audit_log: InMemoryAuditLog,
    request: AgentRequest,
    session: SessionContext,
    interpretation: Interpretation,
    *,
    query: StructuredQuery | None = None,
) -> tuple[PolicyDecision, AuditRecord]:
    """Run policy evaluation and persist an audit record (used by tests and API wiring)."""
    decision = guardian.evaluate(request, session, interpretation)
    record = AuditRecord(
        audit_id=str(uuid.uuid4()),
        session_id=session.session_id,
        recorded_at=datetime.now(timezone.utc).isoformat(),
        request_message=request.message,
        role=request.role,
        stated_purpose=request.stated_purpose,
        policy=decision,
        query=query,
    )
    audit_log.append(record)
    return decision, record
