"""Deterministic lexicon normalizer. No model calls."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from guardian.contracts import SYNTHETIC_DATASET_ID
from guardian.contracts.schemas import (
    AgeBand,
    BmiBand,
    CodedFilter,
    CodeSystem,
    GroupBy,
    Measure,
    PopulationFilters,
    Region,
    StructuredQuery,
)

_LEXICON_PATH = Path(__file__).resolve().parents[1] / "contracts" / "lexicon.json"

_WORD = r"[\w\u00c0-\u024f]+"


def _is_word_char(ch: str) -> bool:
    return len(ch) == 1 and (ch.isalnum() or ch in "_")


@dataclass
class NormalizedTurn:
    """Structured pieces extracted from one message (before session merge)."""

    filters: PopulationFilters = field(default_factory=PopulationFilters)
    measures: list[Measure] = field(default_factory=list)
    group_by: list[GroupBy] = field(default_factory=list)
    user_claim: str | None = None
    unresolved_terms: list[str] = field(default_factory=list)
    clinical_match: bool = False
    breast_cancer_without: bool = False


class Normalizer:
    def __init__(self, lexicon: dict) -> None:
        self._lex = lexicon
        self._negation_cues = [self._fold(c) for c in lexicon["negation_cues"]]
        if "not" not in self._negation_cues:
            # P22 ("BMI is not obese"): standalone "not" is absent from lexicon negation_cues.
            self._negation_cues.append("not")
        self._unknown_code_re = re.compile(lexicon["unknown_code_pattern"])
        self._generic = [self._fold(g) for g in lexicon["generic_unresolved"]]
        self._allowlisted_codes = {
            (row["system"], row["code"]) for row in lexicon["codes"]
        }
        self._code_by_alias: dict[str, dict] = {}
        for row in lexicon["codes"]:
            for alias in row["aliases"]:
                self._code_by_alias[self._fold(alias)] = row
        self._bmi_by_alias: dict[str, str] = {}
        for row in lexicon["bmi_bands"]:
            for alias in row["aliases"]:
                self._bmi_by_alias[self._fold(alias)] = row["band"]
        self._elevated = [self._fold(a) for a in lexicon["elevated_bmi_aliases"]]
        self._elevated_bands = lexicon["elevated_bmi_bands"]
        self._sex_by_alias: dict[str, str] = {}
        for row in lexicon["sex"]:
            for alias in row["aliases"]:
                self._sex_by_alias[self._fold(alias)] = row["sex"]
        self._age_by_alias: dict[str, list[str]] = {}
        for row in lexicon["age_expressions"]:
            for alias in row["aliases"]:
                self._age_by_alias[self._fold(alias)] = row["age_bands"]
        self._measure_by_alias: dict[str, Measure] = {}
        for row in lexicon["measure_cues"]:
            measure = Measure(row["measure"])
            for alias in row["aliases"]:
                self._measure_by_alias[self._fold(alias)] = measure
        self._group_by_alias: dict[str, GroupBy] = {}
        for row in lexicon["group_by_cues"]:
            gb = GroupBy(row["group_by"])
            for alias in row["aliases"]:
                self._group_by_alias[self._fold(alias)] = gb
        self._regions = {self._fold(r): r for r in lexicon["regions"]}
        self._causal = [self._fold(c) for c in lexicon["causal_claim_cues"]]
        self._alias_entries: list[tuple[str, str, str]] = []
        self._register_aliases("code", self._code_by_alias.keys())
        self._register_aliases("bmi", self._bmi_by_alias.keys())
        self._register_aliases("elevated", self._elevated)
        self._register_aliases("sex", self._sex_by_alias.keys())
        self._register_aliases("age", self._age_by_alias.keys())
        self._register_aliases("measure", self._measure_by_alias.keys())
        self._register_aliases("group", self._group_by_alias.keys())
        self._register_aliases("region", self._regions.keys())
        self._alias_entries.sort(key=lambda x: (-len(x[0]), x[0]))

    @classmethod
    def from_default_lexicon(cls) -> Normalizer:
        data = json.loads(_LEXICON_PATH.read_text(encoding="utf-8"))
        return cls(data)

    @staticmethod
    def _fold(text: str) -> str:
        return text.casefold()

    def _register_aliases(self, kind: str, aliases: list[str] | dict) -> None:
        for alias in aliases:
            folded = self._fold(alias) if isinstance(alias, str) else alias
            self._alias_entries.append((folded, kind, folded))

    def _phrase_pattern(self, phrase: str) -> re.Pattern[str]:
        parts = phrase.split()
        if len(parts) == 1:
            return re.compile(re.escape(parts[0]), re.IGNORECASE | re.UNICODE)
        inner = r"\s+".join(re.escape(p) for p in parts)
        return re.compile(inner, re.IGNORECASE | re.UNICODE)

    def _iter_boundary_matches(self, folded: str, phrase: str) -> list[tuple[int, int]]:
        pat = self._phrase_pattern(phrase)
        spans: list[tuple[int, int]] = []
        for m in pat.finditer(folded):
            start, end = m.start(), m.end()
            if start > 0 and _is_word_char(folded[start - 1]):
                continue
            if end < len(folded) and _is_word_char(folded[end]):
                continue
            spans.append((start, end))
        return spans

    def _find_matches(self, message: str) -> list[tuple[int, int, str, str, str]]:
        """start, end, kind, alias_key, matched_text."""
        folded = self._fold(message)
        found: list[tuple[int, int, str, str, str]] = []
        for alias_key, kind, _ in self._alias_entries:
            for start, end in self._iter_boundary_matches(folded, alias_key):
                found.append((start, end, kind, alias_key, message[start:end]))
        found.sort(key=lambda x: (-(x[1] - x[0]), x[0]))
        selected: list[tuple[int, int, str, str, str]] = []
        occupied: list[tuple[int, int]] = []
        for item in found:
            s, e = item[0], item[1]
            if any(not (e <= os or s >= oe) for os, oe in occupied):
                continue
            selected.append(item)
            occupied.append((s, e))
        selected.sort(key=lambda x: x[0])
        return selected

    def _is_negated(self, folded_message: str, start: int) -> bool:
        window = folded_message[max(0, start - 80) : start]
        for sep in (",", ";", " und ", " and "):
            if sep in window:
                window = window.rsplit(sep, 1)[-1]
        for cue in sorted(self._negation_cues, key=len, reverse=True):
            idx = window.rfind(cue)
            if idx == -1:
                continue
            between = window[idx + len(cue) :].strip()
            if not between or len(between.split()) <= 6:
                return True
        return False

    def normalize_message(self, message: str) -> NormalizedTurn:
        result = NormalizedTurn()
        folded = self._fold(message)
        matches = self._find_matches(message)

        coded: list[CodedFilter] = []
        bmi_bands: list[BmiBand] = []
        exclude_bmi: list[BmiBand] = []
        age_bands: list[AgeBand] = []
        regions: list[Region] = []
        sex: str | None = None
        negated_male = False
        negated_female = False
        breast_cancer_without = False
        elevated_hit = False

        for start, _end, kind, alias_key, matched_text in matches:
            negated = self._is_negated(folded, start)
            if kind == "code":
                row = self._code_by_alias[alias_key]
                if row["code"] == "C50" and negated:
                    breast_cancer_without = True
                    continue
                if row["code"] == "C50" and row["system"] == "ICD-10":
                    result.clinical_match = True
                system = CodeSystem.icd10 if row["system"] == "ICD-10" else (
                    CodeSystem.loinc if row["system"] == "LOINC" else CodeSystem.atc
                )
                coded.append(
                    CodedFilter(
                        system=system,
                        code=row["code"],
                        display=row["display"],
                        source_text=matched_text,
                        negated=negated,
                    )
                )
                if not negated:
                    result.clinical_match = True
            elif kind == "bmi":
                band = BmiBand(self._bmi_by_alias[alias_key])
                if negated:
                    if band not in exclude_bmi:
                        exclude_bmi.append(band)
                else:
                    if band not in bmi_bands:
                        bmi_bands.append(band)
                result.clinical_match = True
            elif kind == "elevated":
                elevated_hit = True
                for band_name in self._elevated_bands:
                    band = BmiBand(band_name)
                    if band not in bmi_bands:
                        bmi_bands.append(band)
                result.clinical_match = True
            elif kind == "sex":
                target = self._sex_by_alias[alias_key]
                if negated:
                    if target == "male":
                        negated_male = True
                    elif target == "female":
                        negated_female = True
                else:
                    sex = target
                result.clinical_match = True
            elif kind == "age":
                for ab in self._age_by_alias[alias_key]:
                    band = AgeBand(ab)
                    if band not in age_bands:
                        age_bands.append(band)
                result.clinical_match = True
            elif kind == "region":
                reg = Region(self._regions[alias_key])
                if reg not in regions:
                    regions.append(reg)
                result.clinical_match = True
            elif kind == "group":
                gb = self._group_by_alias[alias_key]
                if gb not in result.group_by:
                    result.group_by.append(gb)
                result.clinical_match = True

        if negated_male and sex is None:
            sex = "female"
        if negated_female and sex is None:
            sex = "male"

        if elevated_hit:
            present = set(bmi_bands)
            bmi_bands = [BmiBand(b) for b in self._elevated_bands if BmiBand(b) in present]
            if not bmi_bands:
                bmi_bands = [BmiBand(b) for b in self._elevated_bands]

        measures: list[Measure] = []
        rate_per_1000 = False
        for alias_key, measure in self._measure_by_alias.items():
            if self._iter_boundary_matches(folded, alias_key):
                if measure == Measure.rate_per_1000:
                    rate_per_1000 = True
                elif not rate_per_1000:
                    if measure not in measures:
                        measures.append(measure)
        if rate_per_1000:
            measures = [Measure.rate_per_1000]
        elif not measures:
            measures = [Measure.count]
        else:
            measures = [measures[0]]

        for cue in self._causal:
            if cue in folded:
                result.user_claim = message
                break

        breast_cancer: bool | None = None
        if breast_cancer_without:
            breast_cancer = False
        elif any(c.code.startswith("C50") and not c.negated for c in coded):
            breast_cancer = True

        result.filters = PopulationFilters(
            sex=sex if sex else "any",
            age_bands=age_bands,
            bmi_bands=bmi_bands,
            exclude_bmi_bands=exclude_bmi,
            breast_cancer=breast_cancer,
            coded=self._dedupe_coded(coded),
            regions=regions,
        )
        result.measures = measures
        result.breast_cancer_without = breast_cancer_without

        consumed = set()
        for start, end, *_ in matches:
            consumed.update(range(start, end))

        for generic in self._generic:
            for start, end in self._iter_boundary_matches(folded, generic):
                if any(i in consumed for i in range(start, end)):
                    continue
                term = message[start:end]
                if term not in result.unresolved_terms:
                    result.unresolved_terms.append(term)

        allowlisted_alias_codes = {row["code"] for row in self._lex["codes"]}
        for m in self._unknown_code_re.finditer(message):
            token = m.group(0)
            folded_token = self._fold(token)
            if any(folded_token == self._fold(a) for row in self._lex["codes"] for a in row["aliases"]):
                continue
            if token.upper() not in {c.upper() for c in allowlisted_alias_codes}:
                if token not in result.unresolved_terms:
                    result.unresolved_terms.append(token)

        return result

    @staticmethod
    def _dedupe_coded(coded: list[CodedFilter]) -> list[CodedFilter]:
        seen: set[tuple[str, str, bool]] = set()
        out: list[CodedFilter] = []
        for c in coded:
            key = (c.system.value, c.code, c.negated)
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
        return out

    def merge_with_prior(
        self,
        turn: NormalizedTurn,
        prior: StructuredQuery | None,
        *,
        dataset_id: str = SYNTHETIC_DATASET_ID,
    ) -> tuple[StructuredQuery | None, bool, list[str]]:
        """Return query, needs_clarification, unresolved."""
        unresolved = list(turn.unresolved_terms)
        if prior is None:
            if not turn.clinical_match:
                return None, True, unresolved
            query = StructuredQuery(
                dataset_id=dataset_id,
                measures=turn.measures,
                group_by=turn.group_by,
                filters=turn.filters,
                user_claim=turn.user_claim,
            )
            if unresolved and not turn.clinical_match:
                return None, True, unresolved
            return query, False, unresolved

        base_filters = prior.filters.model_copy(deep=True)
        new = turn.filters
        if new.sex != "any":
            base_filters.sex = new.sex
        if new.age_bands:
            base_filters.age_bands = new.age_bands
        if new.bmi_bands:
            base_filters.bmi_bands = new.bmi_bands
        if new.exclude_bmi_bands:
            base_filters.exclude_bmi_bands = new.exclude_bmi_bands
        if new.breast_cancer is not None:
            base_filters.breast_cancer = new.breast_cancer
        if new.regions:
            base_filters.regions = new.regions
        if new.coded:
            base_filters.coded = self._merge_coded(base_filters.coded, new.coded)
        if new.timing:
            base_filters.timing = new.timing

        group_by = list(prior.group_by)
        for gb in turn.group_by:
            if gb not in group_by:
                group_by.append(gb)

        measures = turn.measures if turn.measures != [Measure.count] or not prior.measures else prior.measures
        if turn.measures and turn.measures != [Measure.count]:
            measures = turn.measures

        user_claim = turn.user_claim or prior.user_claim
        clinical = turn.clinical_match or prior is not None

        query = StructuredQuery(
            dataset_id=prior.dataset_id,
            measures=measures,
            group_by=group_by,
            filters=base_filters,
            user_claim=user_claim,
        )
        return query, False, unresolved

    @staticmethod
    def _merge_coded(existing: list[CodedFilter], new: list[CodedFilter]) -> list[CodedFilter]:
        if not new:
            return existing
        by_key = {(c.system.value, c.code): c for c in existing}
        for c in new:
            by_key[(c.system.value, c.code)] = c
        return list(by_key.values())
