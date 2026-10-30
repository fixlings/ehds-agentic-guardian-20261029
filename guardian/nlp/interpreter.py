"""Natural-language interpreter: lexicon normalizer plus optional EU model flags."""

from __future__ import annotations

import json
from typing import Any

from guardian.contracts import SYNTHETIC_DATASET_ID
from guardian.contracts.schemas import Interpretation, PurposeCategory, SessionContext
from guardian.nlp.normalizer import Normalizer

_SYSTEM_PROMPT = """You assist a public-health statistics agent. Respond with one JSON object only.
You may set: proposed_purpose (one of public_health_research, insurance, employment, marketing, identity_disclosure, unknown),
purpose_change_suspected, identity_disclosure_suspected, injection_suspected (booleans).
Do not include medical codes, filters, SQL, or query fields. Those are filled locally."""


class Interpreter:
    """Builds a validated Interpretation from user text and session context."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        normalizer: Normalizer | None = None,
        dataset_id: str = SYNTHETIC_DATASET_ID,
    ) -> None:
        self._client = client
        self._normalizer = normalizer or Normalizer.from_default_lexicon()
        self._dataset_id = dataset_id

    def interpret(self, message: str, session: SessionContext) -> Interpretation:
        prior = session.turns[-1].query if session.turns else None
        turn = self._normalizer.normalize_message(message)
        query, needs_clarification, unresolved = self._normalizer.merge_with_prior(
            turn,
            prior,
            dataset_id=self._dataset_id,
        )

        flags = self._default_flags(session)
        if self._client is not None:
            flags = self._merge_model_flags(message, session, flags)

        if needs_clarification or (query is None and not turn.clinical_match and prior is None):
            return Interpretation(
                proposed_purpose=flags["proposed_purpose"],
                purpose_change_suspected=flags["purpose_change_suspected"],
                identity_disclosure_suspected=flags["identity_disclosure_suspected"],
                injection_suspected=flags["injection_suspected"],
                needs_clarification=True,
                clarification_question=self._clarification_question(unresolved),
                unresolved_terms=unresolved,
                query=None,
            )

        if query is None and prior is not None:
            query, _, unresolved = self._normalizer.merge_with_prior(turn, prior, dataset_id=self._dataset_id)

        if query is not None:
            query = self._strip_unknown_codes(query, unresolved)

        if turn.unresolved_terms and query is None:
            needs_clarification = True

        return Interpretation(
            proposed_purpose=flags["proposed_purpose"],
            purpose_change_suspected=flags["purpose_change_suspected"],
            identity_disclosure_suspected=flags["identity_disclosure_suspected"],
            injection_suspected=flags["injection_suspected"],
            needs_clarification=needs_clarification and query is None,
            clarification_question=self._clarification_question(unresolved)
            if needs_clarification and query is None
            else None,
            unresolved_terms=unresolved,
            query=query,
        )

    def _default_flags(self, session: SessionContext) -> dict[str, Any]:
        category = session.purpose_category
        if category == PurposeCategory.unknown:
            proposed = PurposeCategory.public_health_research
        else:
            proposed = category
        return {
            "proposed_purpose": proposed,
            "purpose_change_suspected": False,
            "identity_disclosure_suspected": False,
            "injection_suspected": False,
        }

    def _merge_model_flags(
        self, message: str, session: SessionContext, base: dict[str, Any]
    ) -> dict[str, Any]:
        from guardian.contracts.budget import DEFAULT_MODEL, MODEL_TIMEOUT_S

        model = DEFAULT_MODEL
        if hasattr(self._client, "resolve_model"):
            model = self._client.resolve_model()
        elif hasattr(self._client, "list_models"):
            models = self._client.list_models()
            model = DEFAULT_MODEL if DEFAULT_MODEL in models else (models[0] if models else DEFAULT_MODEL)

        user_payload = json.dumps(
            {
                "message": message,
                "stated_purpose": session.stated_purpose,
                "role": session.role.value,
            },
            ensure_ascii=False,
        )
        try:
            raw = self._client.complete_json(
                model=model,
                system=_SYSTEM_PROMPT,
                user=user_payload,
                timeout_s=MODEL_TIMEOUT_S,
            )
        except Exception:
            return base

        out = dict(base)
        purpose = raw.get("proposed_purpose")
        if isinstance(purpose, str):
            try:
                out["proposed_purpose"] = PurposeCategory(purpose)
            except ValueError:
                pass
        for key in (
            "purpose_change_suspected",
            "identity_disclosure_suspected",
            "injection_suspected",
        ):
            if isinstance(raw.get(key), bool):
                out[key] = raw[key]
        return out

    @staticmethod
    def _clarification_question(unresolved: list[str]) -> str:
        if unresolved:
            terms = ", ".join(unresolved[:5])
            return f"Please clarify which condition or measure you mean (unclear: {terms})."
        return "Please clarify which condition, population, or measure you need."

    @staticmethod
    def _strip_unknown_codes(query: Any, unresolved: list[str]) -> Any:
        unresolved_fold = {t.casefold() for t in unresolved}
        coded = [
            c
            for c in query.filters.coded
            if c.code.casefold() not in unresolved_fold
            and c.source_text.casefold() not in unresolved_fold
        ]
        if len(coded) != len(query.filters.coded):
            return query.model_copy(
                update={"filters": query.filters.model_copy(update={"coded": coded})}
            )
        return query
