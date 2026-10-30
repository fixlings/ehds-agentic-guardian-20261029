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

    @property
    def category(self) -> str:
        return str(self.args[0]) if self.args else "query_schema"


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
        except _ProposalRejected as exc:
            self.validated_live_response = False
            return self._safe_refusal(raw, exc.category)
        self.validated_live_response = (
            interpretation.query is not None and not interpretation.needs_clarification
        )
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
            "If you can map the question, set needs_clarification false and include query. "
            "Do not set needs_clarification true when query is present. "
            "Use age band ids such as 50-59, not 50+. Use bmi band obese, not BMI>=30. "
            "coded.system must be ICD-10, LOINC, or ATC. "
            "Each coded item needs system, code, display, source_text, and negated. "
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
            raise _ProposalRejected("forbidden_field")
        if "proposed_purpose" in raw and raw.get("proposed_purpose") not in (None, ""):
            proposed = _purpose(raw.get("proposed_purpose"))
            if proposed is None:
                raise _ProposalRejected("purpose_invalid")
        else:
            proposed = PurposeCategory.unknown
        flags = _flag_dict(raw)
        unresolved = _terms_in_message(raw.get("unresolved_terms"), message)
        original_query = raw.get("query")
        model_claim = original_query.get("user_claim") if isinstance(original_query, dict) else None
        try:
            body = _normalize_query_dict(raw, self._normalizer.lexicon())
        except _ProposalRejected as exc:
            if exc.category in {"clarification_requested", "query_missing"}:
                return self._noted(
                    Interpretation(
                        proposed_purpose=proposed,
                        needs_clarification=True,
                        clarification_question=self._clarification_question(unresolved),
                        unresolved_terms=unresolved,
                        query=None,
                        notes=["model proposal rejected"],
                        **flags,
                    ),
                    exc.category,
                )
            raise
        body["user_claim"] = None
        body["dataset_id"] = self._dataset_id
        try:
            query = StructuredQuery.model_validate(body)
        except ValidationError:
            raise _ProposalRejected("query_schema") from None
        query = self._enforce_vocabulary(query, message)
        query = _derive_breast_cancer(query)
        if model_claim or self._message_has_causal_cue(message):
            query = query.model_copy(update={"user_claim": message})
        if not _has_population_constraint(query.filters):
            raise _ProposalRejected("no_population_constraint")
        return self._noted(
            Interpretation(
                proposed_purpose=proposed,
                needs_clarification=False,
                unresolved_terms=unresolved,
                query=query,
                notes=["validated EU Mistral proposal"],
                **flags,
            ),
            "ok",
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
                raise _ProposalRejected("unlisted_code")
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

    def _safe_refusal(self, raw: dict, category: str) -> Interpretation:
        proposed = _purpose(raw.get("proposed_purpose")) or PurposeCategory.unknown
        return self._noted(
            Interpretation(
                proposed_purpose=proposed,
                needs_clarification=True,
                clarification_question=self._clarification_question([]),
                unresolved_terms=[],
                query=None,
                notes=["model proposal rejected"],
                **_flag_dict(raw),
            ),
            category,
        )

    @staticmethod
    def _noted(interpretation: Interpretation, category: str) -> Interpretation:
        token = f"validation_category:{category}"
        notes = list(interpretation.notes)
        if token not in notes:
            notes.append(token)
        return interpretation.model_copy(update={"notes": notes})


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


_METADATA_KEYS = frozenset(
    {
        "explanation",
        "notes",
        "comment",
        "rationale",
        "confidence",
        "reasoning",
        "note",
        "description",
    }
)
_QUERY_KEYS = frozenset({"dataset_id", "measures", "group_by", "filters", "user_claim"})
_FILTER_KEYS = frozenset(
    {
        "sex",
        "age_bands",
        "bmi_bands",
        "exclude_bmi_bands",
        "breast_cancer",
        "coded",
        "regions",
        "timing",
    }
)
_CODED_KEYS = frozenset({"system", "code", "display", "source_text", "negated"})
_GROUP_BY = frozenset(
    {"sex", "age_band", "bmi_band", "breast_cancer", "measurement_timing", "region"}
)
_TIMING = frozenset({"before_diagnosis", "after_diagnosis", "unknown", "no_diagnosis"})
_SYSTEM_ALIASES = {"icd10": "ICD-10", "icd-10": "ICD-10", "loinc": "LOINC", "atc": "ATC"}


def _fold_alias(text: str) -> str:
    return " ".join(text.casefold().replace("≥", ">=").split())


def _compact(text: str) -> str:
    return _fold_alias(text).replace(" ", "")


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _drop_unknown(obj: dict, allowed: frozenset[str]) -> dict:
    cleaned: dict = {}
    for key, value in obj.items():
        if not isinstance(key, str):
            raise _ProposalRejected("unexpected_field")
        if key in _METADATA_KEYS:
            continue
        if key not in allowed:
            raise _ProposalRejected("unexpected_field")
        cleaned[key] = value
    return cleaned


def _alias_indexes(lexicon: dict) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, str], dict[str, str], dict[str, str]]:
    age: dict[str, list[str]] = {}
    for row in lexicon["age_expressions"]:
        for alias in row["aliases"]:
            age[_compact(alias)] = list(row["age_bands"])
    for band in ("18-39", "40-49", "50-59", "60-69", "70+"):
        age[_compact(band)] = [band]
    bmi: dict[str, list[str]] = {}
    for row in lexicon["bmi_bands"]:
        for alias in row["aliases"]:
            bmi[_compact(alias)] = [row["band"]]
    for band in ("underweight", "normal", "overweight", "obese", "unknown"):
        bmi.setdefault(_compact(band), [band])
    for alias in lexicon["elevated_bmi_aliases"]:
        bmi[_compact(alias)] = list(lexicon["elevated_bmi_bands"])
    sex = {"any": "any"}
    for row in lexicon["sex"]:
        for alias in row["aliases"]:
            sex[_compact(alias)] = row["sex"]
    regions = {_compact(name): name for name in lexicon["regions"]}
    measures: dict[str, str] = {}
    for row in lexicon["measure_cues"]:
        measures[_compact(row["measure"])] = row["measure"]
        for alias in row["aliases"]:
            measures[_compact(alias)] = row["measure"]
    for name in ("count", "rate_per_1000", "prevalence"):
        measures.setdefault(_compact(name), name)
    return age, bmi, sex, regions, measures


def _expand(values: object, index: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    for value in _as_list(values):
        if not isinstance(value, str) or not value.strip():
            raise _ProposalRejected("query_schema")
        match = index.get(_compact(value))
        if not match:
            raise _ProposalRejected("query_schema")
        for item in match:
            if item not in out:
                out.append(item)
    return out


def _lookup_code(lexicon: dict, system: str, code: str) -> dict | None:
    folded = code.casefold()
    for row in lexicon["codes"]:
        if row["system"] == system and str(row["code"]).casefold() == folded:
            return row
    return None


def _normalize_system(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _ProposalRejected("query_schema")
    folded = value.casefold().replace(" ", "").replace("_", "")
    mapped = _SYSTEM_ALIASES.get(folded)
    if mapped is None:
        raise _ProposalRejected("unlisted_code")
    return mapped


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str) and value.casefold() in {"true", "false", "yes", "no"}:
        return value.casefold() in {"true", "yes"}
    raise _ProposalRejected("query_schema")


def _canonical_coded(value: object, lexicon: dict) -> list[dict]:
    cleaned: list[dict] = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            raise _ProposalRejected("query_schema")
        item = _drop_unknown(item, _CODED_KEYS)
        system = _normalize_system(item.get("system"))
        code = item.get("code")
        if not isinstance(code, str) or not code.strip():
            raise _ProposalRejected("query_schema")
        row = _lookup_code(lexicon, system, code.strip())
        if row is None:
            raise _ProposalRejected("unlisted_code")
        source = item.get("source_text")
        if not isinstance(source, str) or not source.strip():
            source = row["aliases"][0]
        else:
            source = source.strip()
        negated = _as_bool(item.get("negated", False))
        cleaned.append(
            {
                "system": row["system"],
                "code": row["code"],
                "display": row["display"],
                "source_text": str(source)[:200],
                "negated": bool(negated),
            }
        )
    return cleaned


def _canonicalize_filters(filters: dict, lexicon: dict) -> dict:
    age, bmi, sex, regions, _measures = _alias_indexes(lexicon)
    if "sex" in filters:
        mapped = sex.get(_compact(filters["sex"])) if isinstance(filters["sex"], str) else None
        if mapped is None:
            raise _ProposalRejected("query_schema")
        filters["sex"] = mapped
    if "age_bands" in filters:
        filters["age_bands"] = _expand(filters["age_bands"], age)
    if "bmi_bands" in filters:
        filters["bmi_bands"] = _expand(filters["bmi_bands"], bmi)
    if "exclude_bmi_bands" in filters:
        filters["exclude_bmi_bands"] = _expand(filters["exclude_bmi_bands"], bmi)
    if "regions" in filters:
        mapped_regions: list[str] = []
        for value in _as_list(filters["regions"]):
            if not isinstance(value, str):
                raise _ProposalRejected("query_schema")
            name = regions.get(_compact(value))
            if name is None:
                raise _ProposalRejected("query_schema")
            if name not in mapped_regions:
                mapped_regions.append(name)
        filters["regions"] = mapped_regions
    if "timing" in filters:
        mapped_timing: list[str] = []
        for value in _as_list(filters["timing"]):
            if not isinstance(value, str) or value not in _TIMING:
                raise _ProposalRejected("query_schema")
            if value not in mapped_timing:
                mapped_timing.append(value)
        filters["timing"] = mapped_timing
    if "breast_cancer" in filters:
        filters["breast_cancer"] = _as_bool(filters["breast_cancer"])
    if "coded" in filters:
        filters["coded"] = _canonical_coded(filters["coded"], lexicon)
    return filters


def _normalize_query_dict(raw: dict, lexicon: dict) -> dict:
    query = raw.get("query")
    if isinstance(query, str):
        try:
            query = json.loads(query)
        except json.JSONDecodeError:
            raise _ProposalRejected("query_not_object") from None
    if query is None:
        lifted = {key: raw[key] for key in _FILTER_KEYS if key in raw}
        if not lifted and "measures" not in raw and "group_by" not in raw:
            if raw.get("needs_clarification") is True:
                raise _ProposalRejected("clarification_requested")
            raise _ProposalRejected("query_missing")
        query = {
            "measures": raw.get("measures"),
            "group_by": raw.get("group_by", []),
            "filters": lifted,
        }
    if not isinstance(query, dict):
        raise _ProposalRejected("query_not_object")
    query = dict(query)
    filters = query.get("filters") or {}
    if not isinstance(filters, dict):
        raise _ProposalRejected("query_schema")
    filters = dict(filters)
    for key in _FILTER_KEYS:
        if key in query and key not in filters:
            filters[key] = query.pop(key)
    query = _drop_unknown(query, _QUERY_KEYS)
    filters = _drop_unknown(filters, _FILTER_KEYS)
    query = {key: value for key, value in query.items() if key != "filters"}
    query["filters"] = _canonicalize_filters(filters, lexicon)
    if query.get("measures") in (None, [], ""):
        raise _ProposalRejected("query_schema")
    _age, _bmi, _sex, _regions, measures = _alias_indexes(lexicon)
    query["measures"] = [_one_measure(item, measures) for item in _as_list(query["measures"])]
    if not query["measures"]:
        raise _ProposalRejected("query_schema")
    if "group_by" in query:
        query["group_by"] = _canonical_group_by(query["group_by"])
    return query


def _one_measure(value: object, measures: dict[str, str]) -> str:
    if not isinstance(value, str):
        raise _ProposalRejected("query_schema")
    mapped = measures.get(_compact(value))
    if mapped is None:
        raise _ProposalRejected("query_schema")
    return mapped


def _canonical_group_by(values: object) -> list[str]:
    out: list[str] = []
    for value in _as_list(values):
        if not isinstance(value, str) or value not in _GROUP_BY:
            raise _ProposalRejected("query_schema")
        if value not in out:
            out.append(value)
    return out


def _derive_breast_cancer(query: StructuredQuery) -> StructuredQuery:
    if query.filters.breast_cancer is not None:
        return query
    if any(coded.code.startswith("C50") and not coded.negated for coded in query.filters.coded):
        filters = query.filters.model_copy(update={"breast_cancer": True})
        return query.model_copy(update={"filters": filters})
    return query
