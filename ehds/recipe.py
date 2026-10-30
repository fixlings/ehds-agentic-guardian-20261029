"""Turns a request's plain-English definitions into a structured recipe.

Deterministic, rule-based parsing (no AI). The recipe is plain JSON:
concepts (named filters), groups (dimensions), items (measures). Anything the
parser can not interpret is listed in ``unparsed`` so staff can see it.
"""

from __future__ import annotations

import re

from . import vocab

ICD = r"[A-Z]\d{2}(?:\.\d+)?"
ATC = r"[A-Z]\d{2}[A-Z]{0,2}\d{0,2}"
LOINC = r"\d{1,6}-\d"
SEX_WORDS = {"women": "female", "woman": "female", "females": "female", "female": "female",
             "men": "male", "man": "male", "males": "male", "male": "male"}
FORMAT_ONLY = re.compile(r"^(?:with )?(?:one|1) decimals?$|^rounded.*$|^in %$", re.I)


def _codes(pattern: str, text: str) -> list[str]:
    after = re.split(r"starting with|code[s]?\s+(?:is|are|=)?", text, maxsplit=1)
    seg = after[1] if len(after) > 1 else text
    return list(dict.fromkeys(re.findall(rf"\b{pattern}\b", seg)))


def _bands(text: str) -> list[dict]:
    bands = []
    for tok in re.split(r",\s*|\s+and\s+", text.split(":", 1)[1] if ":" in text else text):
        tok = tok.strip().rstrip(".")
        if m := re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", tok):
            bands.append({"label": tok.replace(" ", ""), "min": int(m[1]), "max": int(m[2])})
        elif m := re.fullmatch(r"(\d+)\s*\+|(\d+) (?:and|or) (?:over|older)", tok):
            n = int(m[1] or m[2])
            bands.append({"label": f"{n}+", "min": n, "max": None})
        elif m := re.fullmatch(r"(?:under|<|below)\s*(\d+)", tok):
            bands.append({"label": tok, "min": 0, "max": int(m[1]) - 1})
    return bands


def _age_filter(text: str) -> dict | None:
    t = text.lower()
    if m := re.search(r"aged (\d+)\s*(?:-|–|to)\s*(\d+)", t):
        return {"kind": "age", "min": int(m[1]), "max": int(m[2])}
    if m := re.search(r"(?:aged )?(\d+) (?:or|and) (?:older|over|above)|aged (?:over|above) (\d+)|aged at least (\d+)", t):
        n = int(next(g for g in m.groups() if g))
        return {"kind": "age", "min": n + (1 if "aged over" in m[0] or "aged above" in m[0] else 0), "max": None}
    if m := re.search(r"aged (?:under|below|younger than) (\d+)|younger than (\d+)", t):
        return {"kind": "age", "min": None, "max": int(m[1] or m[2]) - 1}
    return None


class Parser:
    def __init__(self, req: dict):
        self.req = req
        self.concepts: dict[str, dict] = {}
        self.aliases: dict[str, str] = {}
        self.bands: list[dict] = []
        self.latest: dict[str, str] = {}
        self.encounter_years: list[int] = []
        self.count_encounters = False
        self.report: str | None = None
        self.unparsed: list[str] = []
        self.notes: list[str] = []

    # ---------- concepts ----------
    def add(self, name: str, pred: dict, source: str):
        key = re.sub(r"\s+", " ", re.sub(r"\(.*?\)", "", name)).strip().lower()
        self.concepts[key] = {**pred, "name": key, "source": source}
        self.aliases[key] = key
        for abbr in re.findall(r"\(([^)]+)\)", name):
            if not re.search(r"loinc|icd|atc", abbr, re.I):
                self.aliases[abbr.strip().lower()] = key
        return key

    def _inline_loincs(self, text: str):
        for m in re.finditer(rf"([\w/ -]+?)\s*\(LOINC ({LOINC})\)", text):
            words = re.sub(r"\s+values?$", "", m[1].strip(), flags=re.I).split()
            name = words[-1] if words else ""
            for n in range(min(4, len(words)), 0, -1):
                cand = " ".join(words[-n:])
                if vocab.lookup_observation(cand):
                    name = cand
                    break
            if name and name.lower() not in self.concepts:
                self.add(name, {"kind": "observation", "loinc": m[2]}, f"inline LOINC in: {text}")

    def define(self, name: str, body: str, sentence: str):
        b = body.strip().rstrip(".")
        low = b.lower()
        if "/" in b and re.search(r"×|x\s*100|\*\s*100|per cent|percent", low):
            num, den = b.split("/", 1)
            den = re.sub(r"[×x*]\s*100.*$", "", den).strip(" ,")
            self.add(name, {"kind": "formula", "num": num.strip(), "den": den}, sentence)
            return
        if re.search(r"\b(?:who|without|and)\b|\bwith (?!an? (?:icd|atc|loinc))", low) and not re.search(
                r"code[s]? (?:starting with|is|are)|\btype '", low):
            self.add(name, {"kind": "composite", "phrase": b}, sentence)
            return
        if "icd" in low and ("main diagnosis" in low or "primary" in low):
            self.add(name, {"kind": "encounter", "type": _q(low, "type"), "primary": _codes(ICD, b),
                            "holder": _holder(b)}, sentence)
            return
        if "encounter" in low or re.search(r"\btype '(?:inpatient|outpatient|emergency)'", low):
            self.add(name, {"kind": "encounter", "type": _q(low, "type"), "primary": _codes(ICD, b) if "icd" in low else [],
                            "holder": _holder(b)}, sentence)
            return
        if "icd" in low:
            self.add(name, {"kind": "condition", "prefixes": _codes(ICD, b)}, sentence)
            return
        if "atc" in low:
            self.add(name, {"kind": "medication", "prefixes": _codes(ATC, b)}, sentence)
            return
        if "loinc" in low:
            code = re.search(LOINC, b)
            self.add(name, {"kind": "observation", "loinc": code[0]}, sentence)
            return
        if m := re.search(r"sex\s*'?(female|male)'?", low):
            self.add(name, {"kind": "sex", "value": m[1]}, sentence)
            return
        if (af := _age_filter(b)) and not re.search(r"\bwith\b|\bwho\b", low):
            self.add(name, af, sentence)
            return
        if codes := vocab.lookup_condition(name):
            self.add(name, {"kind": "condition", "prefixes": codes, "mapped": True}, sentence)
            self.notes.append(f"'{name}' mapped to ICD-10 {', '.join(codes)} from the local vocabulary.")
            return
        if codes := vocab.lookup_drug(name):
            self.add(name, {"kind": "medication", "prefixes": codes, "mapped": True}, sentence)
            self.notes.append(f"'{name}' mapped to ATC {', '.join(codes)} from the local vocabulary.")
            return
        # composite: a phrase built from other concepts, resolved later
        self.add(name, {"kind": "composite", "phrase": b}, sentence)

    # ---------- sentences ----------
    def parse(self) -> dict:
        defs = self.req.get("definitions", [])
        for s in defs:
            self._inline_loincs(s)
        for s in defs:
            self.sentence(s.strip())
        return self.build()

    def sentence(self, s: str):
        low = s.lower()
        if low.startswith("report"):
            self.report = s
            return
        if low.startswith("age bands"):
            self.bands = _bands(s)
            return
        if re.match(r"a medication is active", low):
            self.notes.append("Active medication: start_date on or before 31 Dec 2025 and end_date empty or on/after it.")
            return
        if m := re.match(r"use the (latest|first|earliest|most recent) (.+?)(?: \(loinc|\s+per patient|;|$)", low):
            name = re.sub(r"\s+values?$", "", m[2]).strip()
            which = "first" if m[1] in ("first", "earliest") else "latest"
            self.latest[name] = which
            if "left out" in low or "excluded" in low:
                self.notes.append(f"Patients without a {name} are left out.")
            return
        if m := re.match(r"count (\w+) \(not patients\)(.*)", low):
            self.count_encounters = True
            self.encounter_years += [int(y) for y in re.findall(r"\b(20\d\d)\b", m[2])]
            return
        parts = [p.strip() for p in s.split(";")] if s.count("=") > 1 else [s]
        handled = False
        for p in parts:
            if "=" in p:
                name, body = p.split("=", 1)
                self.define(name.strip(), body, s)
                handled = True
        if not handled:
            self.unparsed.append(s)

    # ---------- phrase resolution ----------
    def _mentions(self, phrase: str) -> list[tuple[int, int, str]]:
        low = phrase.lower()
        names = dict(self.aliases)
        for n in list(vocab.CONDITIONS) + list(vocab.DRUGS) + list(vocab.OBSERVATIONS):
            names.setdefault(n, n)
        found, taken = [], set()
        for alias in sorted(names, key=len, reverse=True):
            for m in re.finditer(rf"(?<![\w-]){re.escape(alias)}(?:s|es)?(?![\w-])", low):
                span = set(range(m.start(), m.end()))
                if span & taken:
                    continue
                taken |= span
                found.append((m.start(), m.end(), names[alias]))
        return sorted(found)

    def concept(self, key: str) -> dict:
        if key in self.concepts:
            return self.concepts[key]
        if codes := vocab.lookup_condition(key):
            return {"kind": "condition", "prefixes": codes, "name": key, "source": "local vocabulary"}
        if codes := vocab.lookup_drug(key):
            return {"kind": "medication", "prefixes": codes, "name": key, "source": "local vocabulary"}
        if code := vocab.lookup_observation(key):
            return {"kind": "observation", "loinc": code, "name": key, "source": "local vocabulary"}
        raise KeyError(key)

    def resolve(self, phrase: str, depth: int = 0) -> list[dict]:
        """Phrase -> list of filter predicates (ANDed)."""
        preds: list[dict] = []
        low = phrase.lower()
        for start, end, key in self._mentions(phrase):
            c = self.concept(key)
            before = low[max(0, start - 30):start]
            after = low[end:end + 40]
            neg = bool(re.search(r"\b(?:no|without|not|never)\b[\w\s-]*$", before))
            kind = c["kind"]
            if kind == "composite":
                if depth > 5:
                    raise ValueError(f"circular definition: {key}")
                sub = self.resolve(c["phrase"], depth + 1)
                preds += [{**p, "negate": not p.get("negate", False)} for p in sub] if neg else sub
                continue
            if kind == "formula":
                continue
            p = {k: v for k, v in c.items() if k not in ("source", "mapped")}
            p["negate"] = neg
            if kind == "medication":
                p["active"] = bool(re.search(r"\bactive\b", before)) or "active" in after[:15] or "active" in low
            if kind == "observation":
                if m := re.match(r"\s*(?:values?\s*)?(above|over|greater than|>=|>|at least|below|under|less than|<=|<)\s*(\d+(?:\.\d+)?)", after):
                    op = {"above": ">", "over": ">", "greater than": ">", "at least": ">=", "below": "<",
                          "under": "<", "less than": "<"}.get(m[1], m[1])
                    p["threshold"] = {"op": op, "value": float(m[2])}
                p["which"] = self.latest.get(key, "latest")
            preds.append(p)
        for word, sex in SEX_WORDS.items():
            if re.search(rf"\b{word}\b", low) and not any(p["kind"] == "sex" for p in preds):
                preds.append({"kind": "sex", "value": sex, "negate": False})
        if (af := _age_filter(phrase)) and not any(p["kind"] == "age" for p in preds):
            preds.append({**af, "negate": False})
        for r in vocab.REGIONS:
            if re.search(rf"\b(?:except|excluding|outside(?: of)?|without|other than|not in|apart from|but not(?: in)?)\s+"
                         rf"(?:the\s+)?(?:region\s+(?:of\s+)?)?{r.lower()}\b", low):
                preds.append({"kind": "region", "value": r, "negate": True})
            elif re.search(rf"\bin {r.lower()}\b", low):
                preds.append({"kind": "region", "value": r, "negate": False})
        for h in vocab.HOSPITALS:
            if re.search(rf"\b(?:at|in|from) {h.lower()}\b", low):
                preds.append({"kind": "holder", "value": h, "negate": False})
        return preds

    # ---------- report ----------
    def build(self) -> dict:
        groups, items, errors = [], [], []
        rep = self.report or ""
        head, _, body = rep.partition(":")
        hl = head.lower()
        if re.search(r"\bregion", hl):
            groups.append({"dim": "region"})
        if re.search(r"\bband|\bage group", hl):
            groups.append({"dim": "age_band", "bands": self.bands})
        if re.search(r"\bsex\b|\bgender", hl):
            groups.append({"dim": "sex"})
        if re.search(r"hospital|data holder|data_holder|clinic|klinikum", hl):
            groups.append({"dim": "data_holder"})
        if re.search(r"\byear\b", hl):
            groups.append({"dim": "year"})
        if groups and "region" in hl and "band" in hl and hl.index("band") < hl.index("region"):
            groups.reverse()
        if not body and re.search(r"size of the cohort|cohort size", rep, re.I):
            body = "cohort"
        if not rep:
            errors.append("No 'Report ...' sentence found.")

        question = self.req.get("question", "")
        ctx = [p for p in self.resolve(question)
               if p["kind"] in ("condition", "sex", "age", "region", "holder") and (not p.get("negate") or p["kind"] == "region")]

        raw = [t.strip(" .") for t in re.split(
            r",\s*|\s+and\s+(?=(?:the|that|their|mean|average|median|%|number|share|how many|percentage|proportion)\b)",
            body) if t.strip(" .")]
        raw = [t for t in raw if not FORMAT_ONLY.match(t)]
        prev_count = None
        for text in raw:
            try:
                item = self.item(text, prev_count, items)
            except (KeyError, ValueError) as e:
                errors.append(f"Could not interpret '{text}': {e}")
                continue
            items.append(item)
            if item["type"] == "count":
                prev_count = item
        # base items: a count whose filters are a strict subset of a sibling's do not inherit the question context
        counts = [it for it in items if it["type"] == "count"]
        for it in counts:
            sig = {_sig(p) for p in it["filters"]}
            is_base = it.get("no_inherit") or any(sig < {_sig(p) for p in o["filters"]} for o in counts if o is not it)
            if not is_base:
                have = {(p["kind"], p.get("name")) for p in it["filters"]}
                it["filters"] += [p for p in ctx if (p["kind"], p.get("name")) not in have
                                  and not (p["kind"] in ("sex", "age") and any(q["kind"] == p["kind"] for q in it["filters"]))]
        for it in items:
            for ref in ("population", "num", "den"):
                if isinstance(it.get(ref), int):
                    it[ref] = items[it[ref]]["filters"]
        unit = "encounter" if self.count_encounters or any(it.get("unit") == "encounter" for it in items) else "patient"
        return {
            "unit": unit,
            "encounter_years": sorted(set(self.encounter_years)),
            "groups": groups,
            "items": items,
            "concepts": self.concepts,
            "notes": self.notes,
            "unparsed": self.unparsed,
            "errors": errors,
        }

    def item(self, text: str, prev_count: dict | None, items: list) -> dict:
        t = text.strip()
        low = t.lower()
        key = _slug(t)
        idx = {id(it): i for i, it in enumerate(items)}
        # defined formula, e.g. "the prevalence"
        for name, c in self.concepts.items():
            if c["kind"] == "formula" and re.search(rf"\b{re.escape(name)}\b", low):
                return {"type": "pct", "key": _slug(name), "label": t,
                        "num": self._filters(c["num"]), "den": self._filters(c["den"], explicit=True)}
        if re.match(r"(?:the )?(?:mean|average|median)\b|their (?:mean|average|median)", low):
            stat = "median" if "median" in low else "mean"
            obs = [p for p in self.resolve(t) if p["kind"] == "observation"]
            if not obs:
                raise ValueError("no measurement named")
            pop = idx[id(prev_count)] if prev_count else None
            return {"type": stat, "key": key, "label": t, "observation": obs[0],
                    "population": pop if pop is not None else []}
        if re.match(r"%|percent|percentage|proportion|share|that share", low):
            if re.match(r"that share|share$|the share", low) or "that share" in low:
                if not prev_count:
                    raise ValueError("share without a preceding count")
                return {"type": "pct", "key": key, "label": t, "num": idx[id(prev_count)],
                        "den": prev_count.get("parent_filters", [])}
            base = next((it for it in items if it["type"] == "count"), None)
            extra = self.resolve(re.sub(r"^(?:%|percent(?:age)?|proportion|share)\s*(?:of them\s*)?", "", t, flags=re.I))
            base_f = base["filters"] if base else []
            return {"type": "pct", "key": key, "label": t, "num": base_f + extra,
                    "den": idx[id(base)] if base else []}
        # count
        parent = None
        if re.search(r"\bof them\b|\bthereof\b|\bamong them\b", low) and prev_count:
            parent = prev_count
        phrase = re.sub(r"^(?:number of|count of|how many(?: of them)?(?: have| had| are)?)\s+", "", t, flags=re.I)
        enc_head = self._encounter_head(phrase)
        filters = self.resolve(phrase)
        if parent:
            filters = parent["filters"] + filters
        item = {"type": "count", "key": key, "label": t, "filters": filters,
                "parent_filters": parent["filters"] if parent else []}
        if re.match(r"all\b", low):
            item["no_inherit"] = True
        if parent:
            item["no_inherit"] = True
        if enc_head:
            item["unit"] = "encounter"
        return item

    def _filters(self, phrase: str, explicit: bool = False) -> list[dict]:
        phrase = re.sub(r"\bin the (?:region|band|age band|hospital|group)\b", "", phrase, flags=re.I)
        return self.resolve(phrase)

    def _encounter_head(self, phrase: str) -> bool:
        m = self._mentions(phrase)
        return bool(m) and m[0][0] == 0 and self.concept(m[0][2])["kind"] == "encounter" or bool(
            re.match(r"(?:inpatient |outpatient |emergency )?(?:stays|encounters|visits|admissions)\b", phrase, re.I))


def _q(low: str, field: str) -> str | None:
    m = re.search(rf"{field}\s*'(\w+)'", low) or re.search(r"\b(inpatient|outpatient|emergency)\b", low)
    return m[1] if m else None


def _holder(text: str) -> str | None:
    return next((h for h in vocab.HOSPITALS if h.lower() in text.lower()), None)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9%]+", "_", text.lower().replace("%", "pct")).strip("_")
    return s[:60] or "value"


def _sig(p: dict) -> str:
    return repr(sorted((k, repr(v)) for k, v in p.items()))


def parse(req: dict) -> dict:
    return Parser(req).parse()
