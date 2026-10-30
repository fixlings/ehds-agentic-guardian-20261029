"""Natural-language interpreter.

Offline mode uses the lexicon normalizer only.
Live mode asks EU Mistral for a structured query proposal and validates it locally.
The model does not decide access and cannot supply SQL or unlisted codes.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from guardian.contracts import SYNTHETIC_DATASET_ID
from guardian.contracts.schemas import (
    CodedFilter,
    Interpretation,
    PopulationFilters,
    PurposeCategory,
    SessionContext,
    StructuredQuery,
)
from guardian.nlp.normalizer import Normalizer

_SYSTEM_PROMPT = """You assist a public-health statistics agent. Respond with one JSON object only.
You may set: proposed_purpose (one of public_health_research, insurance, employment, marketing, identity_disclosure, unknown),
purpose_change_suspected, identity_disclosure_suspected, injection_suspected (booleans).
Do not include medical codes, filters, SQL, or query fields. Those are filled locally."""

_FORBIDDEN_KEYS = {"sql", "statement", "script", "exec"}


class ModelCallError(RuntimeError):
    """EU Mistral did not return a JSON object. The message is safe to show."""


class _ProposalRejected(Exception):
    """The model JSON failed vocabulary or schema checks."""


class Interpreter:
    """Builds a validated Interpretation from user text and session context."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        normalizer: Normalizer | None = None,
        dataset_id: str = SYNTHETIC_DATASET_ID,
        propose_query: bool = False,
    ) -> None:
        self._client = client
        self._normalizer = normalizer or Normalizer.from_default_lexicon()
        self._dataset_id = dataset_id
        self._propose_query = propose_query
        self.validated_live_response = False

    def interpret(self, message: str, session: SessionContext) -> Interpretation:
        self.validated_live_response = False
        if self._propose_query and self._client is not None:
            return self._interpret_with_eu_model(message, session)
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
            raise ModelCallError("EU Mistral did not return JSON") from None
        if not isinstance(raw, dict):
            raise ModelCallError("EU Mistral did not return a JSON object")

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

    def _interpret_with_eu_model(self, message: str, session: SessionContext) -> Interpretation:
        from guardian.contracts.budget import DEFAULT_MODEL, MODEL_TIMEOUT_S

        prior = session.turns[-1].query if session.turns else None
        user_payload = json.dumps(
            {
                "message": message,
                "stated_purpose": session.stated_purpose,
                "role": session.role.value,
                "prior_query": prior.model_dump(mode="json") if prior is not None else None,
            },
            ensure_ascii=False,
        )
        try:
            raw = self._client.complete_json(
                model=DEFAULT_MODEL,
                system=self._live_system_prompt(),
                user=user_payload,
                timeout_s=MODEL_TIMEOUT_S,
            )
        except Exception:
            raise ModelCallError("EU Mistral did not return JSON") from None
        if not isinstance(raw, dict):
            raise ModelCallError("EU Mistral did not return a JSON object")
        try:
            interpretation = self._validate_proposal(raw, message)
        except _ProposalRejected:
            self.validated_live_response = False
            return self._safe_refusal(raw)
        self.validated_live_response = True
        return interpretation

    def _live_system_prompt(self) -> str:
        codes = [
            f"{row['system']} {row['code']} ({row['display']})"
            for row in self._normalizer.lexicon()["codes"]
        ]
        return (
            "Translate one public-health statistics question into a single JSON object. "
            "You only propose a query. You do not decide access, invent patient facts, or write SQL. "
            "Permitted codes: "
            + "; ".join(codes)
            + ". "
            "sex: female, male, or any. "
            "age_bands: 18-39, 40-49, 50-59, 60-69, 70+. "
            "bmi_bands and exclude_bmi_bands: underweight, normal, overweight, obese, unknown. "
            "regions: Wien, Ost, Süd, West, Nord. "
            "measures: count, rate_per_1000, prevalence. "
            "group_by: sex, age_band, bmi_band, breast_cancer, measurement_timing, region. "
            "timing: before_diagnosis, after_diagnosis, unknown, no_diagnosis. "
            "proposed_purpose: public_health_research, insurance, employment, marketing, identity_disclosure, unknown. "
            "Map informal clinical language onto this vocabulary. "
            "Women means sex female. BMI >= 30 means bmi band obese only. "
            "Aged 50 or older means age bands 50-59, 60-69, and 70+. "
            "High BMI or elevated BMI means overweight and obese. "
            "A question about whether a measurement came first is not a filter: leave timing empty "
            "unless the user asks to count only one timing class. "
            "If you cannot map the question safely, set needs_clarification true and query null. "
            "If the user claims cause or time order, set user_claim to the user message exactly. "
            "Keys: proposed_purpose, purpose_change_suspected, identity_disclosure_suspected, "
            "injection_suspected, needs_clarification, unresolved_terms, query. "
            "query contains measures, group_by, filters, user_claim. "
            "filters contains sex, age_bands, bmi_bands, exclude_bmi_bands, breast_cancer, coded, regions, timing. "
            "coded items contain system, code, display, source_text, negated. "
            "Never add a sql field or any executable text."
        )

    def _validate_proposal(self, raw: dict, message: str) -> Interpretation:
        if _contains_forbidden(raw):
            raise _ProposalRejected("forbidden executable field")
        proposed = _purpose(raw.get("proposed_purpose"))
        if proposed is None:
            raise _ProposalRejected("proposed_purpose missing or not permitted")
        flags = _flag_dict(raw)
        unresolved = _terms_in_message(raw.get("unresolved_terms"), message)
        if raw.get("needs_clarification") is True or raw.get("query") is None:
            return Interpretation(
                proposed_purpose=proposed,
                needs_clarification=True,
                clarification_question=self._clarification_question(unresolved),
                unresolved_terms=unresolved,
                query=None,
                notes=["validated EU Mistral clarification"],
                **flags,
            )
        payload = raw.get("query")
        if not isinstance(payload, dict):
            raise _ProposalRejected("query was not an object")
        body = dict(payload)
        model_claim = body.get("user_claim")
        body["user_claim"] = None
        body["dataset_id"] = self._dataset_id
        try:
            query = StructuredQuery.model_validate(body)
        except ValidationError:
            raise _ProposalRejected("query did not match the aggregate schema") from None
        query = self._enforce_vocabulary(query, message)
        if model_claim or self._message_has_causal_cue(message):
            query = query.model_copy(update={"user_claim": message})
        if not _has_population_constraint(query.filters):
            raise _ProposalRejected("no permitted population constraint")
        return Interpretation(
            proposed_purpose=proposed,
            needs_clarification=False,
            unresolved_terms=unresolved,
            query=query,
            notes=["validated EU Mistral proposal"],
            **flags,
        )

    def _enforce_vocabulary(self, query: StructuredQuery, message: str) -> StructuredQuery:
        allowed = {
            (row["system"], row["code"]): row for row in self._normalizer.lexicon()["codes"]
        }
        cleaned: list[CodedFilter] = []
        folded_message = message.casefold()
        for coded in query.filters.coded:
            row = allowed.get((coded.system.value, coded.code))
            if row is None:
                raise _ProposalRejected("model code is not allowlisted")
            source = coded.source_text.strip()
            if not source or source.casefold() not in folded_message:
                source = row["aliases"][0]
            cleaned.append(
                CodedFilter(
                    system=coded.system,
                    code=row["code"],
                    display=row["display"],
                    source_text=source[:200],
                    negated=bool(coded.negated),
                )
            )
        filters = query.filters.model_copy(update={"coded": cleaned})
        return query.model_copy(update={"filters": filters})

    def _message_has_causal_cue(self, message: str) -> bool:
        folded = message.casefold()
        return any(str(cue).casefold() in folded for cue in self._normalizer.lexicon()["causal_claim_cues"])

    def _safe_refusal(self, raw: dict) -> Interpretation:
        proposed = _purpose(raw.get("proposed_purpose")) or PurposeCategory.unknown
        return Interpretation(
            proposed_purpose=proposed,
            needs_clarification=True,
            clarification_question=self._clarification_question([]),
            unresolved_terms=[],
            query=None,
            notes=["model proposal rejected"],
            **_flag_dict(raw),
        )


def _purpose(value: object) -> PurposeCategory | None:
    if not isinstance(value, str):
        return None
    try:
        return PurposeCategory(value)
    except ValueError:
        return None


def _flag_dict(raw: dict) -> dict[str, bool]:
    return {
        "purpose_change_suspected": raw.get("purpose_change_suspected") is True,
        "identity_disclosure_suspected": raw.get("identity_disclosure_suspected") is True,
        "injection_suspected": raw.get("injection_suspected") is True,
    }


def _terms_in_message(value: object, message: str) -> list[str]:
    if not isinstance(value, list):
        return []
    folded = message.casefold()
    terms: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or len(text) > 80 or text.casefold() not in folded:
            continue
        if text not in terms:
            terms.append(text)
        if len(terms) == 8:
            break
    return terms


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _FORBIDDEN_KEYS or "sql" in key.casefold():
                return True
            if _contains_forbidden(child):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_forbidden(item) for item in value)
    return False


def _has_population_constraint(filters: PopulationFilters) -> bool:
    return bool(
        filters.sex != "any"
        or filters.age_bands
        or filters.bmi_bands
        or filters.exclude_bmi_bands
        or filters.breast_cancer is not None
        or filters.coded
        or filters.regions
        or filters.timing
    )
