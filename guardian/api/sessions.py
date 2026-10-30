"""In-memory session store. Lost on process exit. No patient records."""

from __future__ import annotations

import uuid

from guardian.contracts.schemas import AgentRequest, SessionContext


class UnknownSessionError(KeyError):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(session_id)


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionContext] = {}

    def resolve(self, request: AgentRequest) -> SessionContext:
        if request.session_id is None:
            session = SessionContext(
                session_id=str(uuid.uuid4()),
                role=request.role,
                stated_purpose=request.stated_purpose,
            )
            self._sessions[session.session_id] = session
            return session
        try:
            session = self._sessions[request.session_id]
        except KeyError as exc:
            raise UnknownSessionError(request.session_id) from exc
        session.role = request.role
        session.stated_purpose = request.stated_purpose
        return session
