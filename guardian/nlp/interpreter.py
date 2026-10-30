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
    AgeBand,
    BmiBand,
    CodeSystem,
    CodedFilter,
    GroupBy,
    Interpretation,
    Measure,
    PopulationFilters,
    PurposeCategory,
    Region,
    SessionContext,
    StructuredQuery,
    Timing,
)
from guardian.nlp.normalizer import Normalizer

_SYSTEM_PROMPT = """You assist a public-health statistics agent. Respond with one JSON object only.
You may set: proposed_purpose (one of public_health_research, insurance, employment, marketing, identity_disclosure, unknown),
purpose_change_suspected, identity_disclosure_suspected, injection_suspected (booleans).
Do not include medical codes, filters, SQL, or query fields. Those are filled locally."""

_FORBIDDEN_KEYS = {"sql", "statement", "script", "exec"}


def _enum_json(values: list[str]) -> dict:
    return {"type": "string", "enum": values}


def _array_json(item: dict) -> dict:
    return {"type": "array", "items": item}


def _object_json(properties: dict) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _live_response_format() -> dict:
    """Strict JSON schema for the EU Mistral proposal. Enums match StructuredQuery."""
    coded = _object_json(
        {
            "system": _enum_json([item.value for item in CodeSystem]),
            "code": {"type": "string"},
            "display": {"type": "string"},
            "source_text": {"type": "string"},
            "negated": {"type": "boolean"},
        }
    )
    filters = _object_json(
        {
            "sex": _enum_json(["female", "male", "any"]),
            "age_bands": _array_json(_enum_json([item.value for item in AgeBand])),
            "bmi_bands": _array_json(_enum_json([item.value for item in BmiBand])),
            "exclude_bmi_bands": _array_json(_enum_json([item.value for item in BmiBand])),
            "breast_cancer": {"type": "boolean"},
            "coded": _array_json(coded),
            "regions": _array_json(_enum_json([item.value for item in Region])),
            "timing": _array_json(_enum_json([item.value for item in Timing])),
        }
    )
    query = _object_json(
        {
            "measures": _array_json(_enum_json([item.value for item in Measure])),
            "group_by": _array_json(_enum_json([item.value for item in GroupBy])),
            "filters": filters,
            "user_claim": {"type": "string"},
        }
    )
    root = _object_json(
        {
            "proposed_purpose": _enum_json([item.value for item in PurposeCategory]),
            "purpose_change_suspected": {"type": "boolean"},
            "identity_disclosure_suspected": {"type": "boolean"},
            "injection_suspected": {"type": "boolean"},
            "needs_clarification": {"type": "boolean"},
            "unresolved_terms": _array_json({"type": "string"}),
            "query": query,
        }
    )
    return {
        "type": "json_schema",
        "json_schema": {"name": "EhdsQueryProposal", "schema": root, "strict": True},
    }


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
                response_format=_live_response_format(),
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
            "JSON types are mandatory. measures, age_bands, bmi_bands, exclude_bmi_bands, "
            "group_by, regions, timing, and coded are arrays. "
            "sex is one string, not an array. breast_cancer is a boolean, not a diagnosis name. "
            "Do not join enums into one comma-separated string. Do not prefix a band with and. "
            "Example: {\"proposed_purpose\":\"public_health_research\",\"needs_clarification\":false,"
            "\"purpose_change_suspected\":false,\"identity_disclosure_suspected\":false,"
            "\"injection_suspected\":false,\"unresolved_terms\":[],"
            "\"query\":{\"measures\":[\"count\"],\"group_by\":[],\"user_claim\":\"\","
            "\"filters\":{\"sex\":\"female\",\"age_bands\":[\"50-59\",\"60-69\",\"70+\"],"
            "\"bmi_bands\":[\"obese\"],\"exclude_bmi_bands\":[],\"breast_cancer\":true,"
            "\"coded\":[{\"system\":\"ICD-10\",\"code\":\"C50\","
            "\"display\":\"Malignant neoplasm of breast\",\"source_text\":\"breast cancer\","
            "\"negated\":false}],\"regions\":[],\"timing\":[]}}}. "
            "Never add a sql field or any executable text."
        )

    def _validate_proposal(self, raw: dict, message: str) -> Interpretation:
        forbidden = _forbidden_path(raw)
        if forbidden is not None:
            raise _ProposalRejected(f"forbidden_field.{forbidden}: expected no executable field")
        if "proposed_purpose" in raw and raw.get("proposed_purpose") not in (None, ""):
            proposed = _purpose(raw.get("proposed_purpose"))
            if proposed is None:
                raise _ProposalRejected("purpose_invalid.proposed_purpose: expected purpose enum")
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
        except ValidationError as exc:
            raise _ProposalRejected(_schema_from_validation(exc)) from None
        query = self._enforce_vocabulary(query, message)
        query = _derive_breast_cancer(query)
        if model_claim or self._message_has_causal_cue(message):
            query = query.model_copy(update={"user_claim": message})
        if not _has_population_constraint(query.filters):
            raise _ProposalRejected("no_population_constraint.filters: expected a population constraint")
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
                raise _ProposalRejected("unlisted_code.filters.coded: expected allowlisted code")
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


def _forbidden_path(value: object, prefix: str = "") -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            token = key if isinstance(key, str) and key.isidentifier() and len(key) <= 40 else "field"
            path = f"{prefix}.{token}" if prefix else token
            if not isinstance(key, str) or key.casefold() in _FORBIDDEN_KEYS or "sql" in key.casefold():
                return path
            found = _forbidden_path(child, path)
            if found is not None:
                return found
        return None
    if isinstance(value, list):
        for item in value:
            found = _forbidden_path(item, prefix)
            if found is not None:
                return found
    return None


def _schema(path: str, expected: str) -> str:
    return f"query_schema.{path}: expected {expected}"


def _field_token(key: object) -> str:
    if isinstance(key, str) and key.isidentifier() and len(key) <= 40:
        return key
    return "field"


_SCHEMA_EXPECTED = {
    "measures": "array of measure enum",
    "group_by": "array of group-by enum",
    "filters": "object",
    "sex": "sex enum",
    "age_bands": "array of age-band enum",
    "bmi_bands": "array of bmi-band enum",
    "exclude_bmi_bands": "array of bmi-band enum",
    "breast_cancer": "boolean",
    "coded": "array of coded filters",
    "regions": "array of region enum",
    "timing": "array of timing enum",
    "system": "code system enum",
    "code": "allowlisted code",
    "display": "string",
    "source_text": "string",
    "negated": "boolean",
    "user_claim": "string",
    "query": "object",
}


def _schema_from_validation(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "query_schema.query: expected structured query"
    loc = [str(part) for part in errors[0].get("loc", ()) if not isinstance(part, int)]
    field = loc[-1] if loc else "query"
    path = ".".join(loc) if loc else "query"
    return _schema(path, _SCHEMA_EXPECTED.get(field, "schema value"))


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


def _drop_unknown(obj: dict, allowed: frozenset[str], *, path: str) -> dict:
    cleaned: dict = {}
    for key, value in obj.items():
        if not isinstance(key, str):
            raise _ProposalRejected(f"unexpected_field.{path}: expected no extra field")
        if key in _METADATA_KEYS:
            continue
        if key not in allowed:
            raise _ProposalRejected(
                f"unexpected_field.{path}.{_field_token(key)}: expected no extra field"
            )
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


def _strip_leading_and(text: str) -> str:
    stripped = text.strip()
    if stripped.casefold().startswith("and "):
        return stripped[4:].strip()
    return stripped


def _comma_pieces(text: str) -> list[str]:
    pieces: list[str] = []
    for piece in text.split(","):
        cleaned = _strip_leading_and(piece)
        if cleaned:
            pieces.append(cleaned)
    return pieces


def _lookup_band(text: str, index: dict[str, list[str]], *, strip_only: bool) -> list[str] | None:
    cleaned = _strip_leading_and(text)
    match = index.get(_compact(cleaned))
    if match is not None:
        return match
    if strip_only:
        folded = _fold_alias(cleaned)
        if folded.endswith(" only"):
            return index.get(_compact(folded[: -len(" only")]))
    return None


def _expand(
    values: object,
    index: dict[str, list[str]],
    *,
    path: str,
    expected: str,
    strip_only: bool = False,
) -> list[str]:
    out: list[str] = []
    for value in _as_list(values):
        if isinstance(value, str):
            pieces = _comma_pieces(value)
        else:
            raise _ProposalRejected(_schema(path, expected))
        if isinstance(value, str) and value.strip() and not pieces:
            raise _ProposalRejected(_schema(path, expected))
        for text in pieces:
            match = _lookup_band(text, index, strip_only=strip_only)
            if not match:
                raise _ProposalRejected(_schema(path, expected))
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
        raise _ProposalRejected(_schema("filters.coded.system", "code system enum"))
    folded = value.casefold().replace(" ", "").replace("_", "")
    mapped = _SYSTEM_ALIASES.get(folded)
    if mapped is None:
        raise _ProposalRejected("unlisted_code.filters.coded.system: expected allowlisted code system")
    return mapped


def _as_bool(value: object, *, path: str, lexicon: dict | None = None) -> bool | None:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        folded = value.casefold().strip()
        if folded in {"true", "yes"}:
            return True
        if folded in {"false", "no"}:
            return False
        if lexicon is not None and _is_breast_cancer_alias(value, lexicon):
            return True
    raise _ProposalRejected(_schema(path, "boolean"))


def _is_breast_cancer_alias(value: str, lexicon: dict) -> bool:
    compact = _compact(value)
    if not compact:
        return False
    for row in lexicon["codes"]:
        if row.get("system") != "ICD-10" or not str(row.get("code", "")).startswith("C50"):
            continue
        if compact == _compact(str(row["code"])):
            return True
        if any(_compact(str(alias)) == compact for alias in row.get("aliases", [])):
            return True
    return False


def _canonical_coded(value: object, lexicon: dict) -> list[dict]:
    cleaned: list[dict] = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            raise _ProposalRejected(_schema("filters.coded", "array of coded filters"))
        item = _drop_unknown(item, _CODED_KEYS, path="filters.coded")
        system = _normalize_system(item.get("system"))
        code = item.get("code")
        if not isinstance(code, str) or not code.strip():
            raise _ProposalRejected(_schema("filters.coded.code", "allowlisted code"))
        row = _lookup_code(lexicon, system, code.strip())
        if row is None:
            raise _ProposalRejected("unlisted_code.filters.coded: expected allowlisted code")
        source = item.get("source_text")
        if not isinstance(source, str) or not source.strip():
            source = row["aliases"][0]
        else:
            source = source.strip()
        negated = _as_bool(item.get("negated", False), path="filters.coded.negated")
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


def _one_sex(value: object, sex: dict[str, str]) -> str:
    if isinstance(value, list):
        if len(value) != 1:
            raise _ProposalRejected(_schema("filters.sex", "sex enum"))
        value = value[0]
    mapped = sex.get(_compact(value)) if isinstance(value, str) else None
    if mapped is None:
        raise _ProposalRejected(_schema("filters.sex", "sex enum"))
    return mapped


def _canonicalize_filters(filters: dict, lexicon: dict) -> dict:
    age, bmi, sex, regions, _measures = _alias_indexes(lexicon)
    if "sex" in filters:
        filters["sex"] = _one_sex(filters["sex"], sex)
    if "age_bands" in filters:
        filters["age_bands"] = _expand(
            filters["age_bands"],
            age,
            path="filters.age_bands",
            expected="array of age-band enum",
        )
    if "bmi_bands" in filters:
        filters["bmi_bands"] = _expand(
            filters["bmi_bands"],
            bmi,
            path="filters.bmi_bands",
            expected="array of bmi-band enum",
            strip_only=True,
        )
    if "exclude_bmi_bands" in filters:
        filters["exclude_bmi_bands"] = _expand(
            filters["exclude_bmi_bands"],
            bmi,
            path="filters.exclude_bmi_bands",
            expected="array of bmi-band enum",
            strip_only=True,
        )
    if "regions" in filters:
        filters["regions"] = _enum_pieces(
            filters["regions"],
            regions,
            path="filters.regions",
            expected="array of region enum",
        )
    if "timing" in filters:
        filters["timing"] = _enum_pieces(
            filters["timing"],
            {name: name for name in _TIMING},
            path="filters.timing",
            expected="array of timing enum",
        )
    if "breast_cancer" in filters:
        filters["breast_cancer"] = _as_bool(
            filters["breast_cancer"],
            path="filters.breast_cancer",
            lexicon=lexicon,
        )
    if "coded" in filters:
        filters["coded"] = _canonical_coded(filters["coded"], lexicon)
    return filters


def _enum_pieces(values: object, index: dict[str, str], *, path: str, expected: str) -> list[str]:
    out: list[str] = []
    for value in _as_list(values):
        if not isinstance(value, str):
            raise _ProposalRejected(_schema(path, expected))
        pieces = _comma_pieces(value) or ([value.strip()] if value.strip() else [])
        if value.strip() and not pieces:
            raise _ProposalRejected(_schema(path, expected))
        for text in pieces:
            name = index.get(_compact(text))
            if name is None:
                raise _ProposalRejected(_schema(path, expected))
            if name not in out:
                out.append(name)
    return out


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
        raise _ProposalRejected(_schema("filters", "object"))
    filters = dict(filters)
    for key in _FILTER_KEYS:
        if key in query and key not in filters:
            filters[key] = query.pop(key)
    query = _drop_unknown(query, _QUERY_KEYS, path="query")
    filters = _drop_unknown(filters, _FILTER_KEYS, path="filters")
    query = {key: value for key, value in query.items() if key != "filters"}
    query["filters"] = _canonicalize_filters(filters, lexicon)
    if query.get("measures") in (None, [], ""):
        raise _ProposalRejected(_schema("measures", "array of measure enum"))
    _age, _bmi, _sex, _regions, measures = _alias_indexes(lexicon)
    query["measures"] = [_one_measure(item, measures) for item in _as_list(query["measures"])]
    if not query["measures"]:
        raise _ProposalRejected(_schema("measures", "array of measure enum"))
    if "group_by" in query:
        query["group_by"] = _canonical_group_by(query["group_by"])
    return query


def _one_measure(value: object, measures: dict[str, str]) -> str:
    if isinstance(value, str):
        pieces = _comma_pieces(value) or [value]
        if len(pieces) != 1:
            raise _ProposalRejected(_schema("measures", "array of measure enum"))
        value = pieces[0]
    if not isinstance(value, str):
        raise _ProposalRejected(_schema("measures", "array of measure enum"))
    mapped = measures.get(_compact(value))
    if mapped is None:
        raise _ProposalRejected(_schema("measures", "array of measure enum"))
    return mapped


def _canonical_group_by(values: object) -> list[str]:
    out: list[str] = []
    for value in _as_list(values):
        if not isinstance(value, str):
            raise _ProposalRejected(_schema("group_by", "array of group-by enum"))
        pieces = _comma_pieces(value)
        if value.strip() and not pieces:
            raise _ProposalRejected(_schema("group_by", "array of group-by enum"))
        for text in pieces:
            if text not in _GROUP_BY:
                raise _ProposalRejected(_schema("group_by", "array of group-by enum"))
            if text not in out:
                out.append(text)
    return out


def _derive_breast_cancer(query: StructuredQuery) -> StructuredQuery:
    if query.filters.breast_cancer is not None:
        return query
    if any(coded.code.startswith("C50") and not coded.negated for coded in query.filters.coded):
        filters = query.filters.model_copy(update={"breast_cancer": True})
        return query.model_copy(update={"filters": filters})
    return query
