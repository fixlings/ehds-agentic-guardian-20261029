"""Independent second computation ("double programming").

Recomputes every statistical answer from the raw CSV files with plain Python:
no DuckDB, no SQL, no pandas, no shared code with ``engine``. A number is only
released when both computations agree exactly. Like pharma's independent
double programming of study tables.
"""

from __future__ import annotations

import csv
import itertools
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from . import privacy

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DAY = "2025-12-31"


def _read(name: str) -> list[dict]:
    with (DATA_DIR / f"{name}.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@lru_cache(maxsize=1)
def _db() -> dict:
    raw = _read("patients")
    pats = {r["patient_id"]: {**r, "age": 2025 - int(r["birth_date"][:4])}
            for r in raw if r["opted_out"].strip().lower() != "true"}
    by = {t: defaultdict(list) for t in ("conditions", "medications", "observations", "encounters")}
    for t in by:
        for r in _read(t):
            if r["patient_id"] in pats:
                by[t][r["patient_id"]].append(r)
    for rows in by["observations"].values():
        rows.sort(key=lambda r: r["date"])
    domains = {c: sorted({r[c] for r in raw}) for c in ("region", "sex", "data_holder")}
    return {"patients": pats, **by, "domains": domains}


def _starts(code: str, prefixes: list[str]) -> bool:
    return any(code.startswith(p) for p in prefixes)


def _obs_value(pid: str, p: dict):
    rows = [r for r in _db()["observations"][pid] if r["loinc"] == p["loinc"]]
    if not rows:
        return None
    return float((rows[0] if p.get("which") == "first" else rows[-1])["value"])


OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
       "=": lambda a, b: a == b}


def _enc_ok(e: dict, p: dict) -> bool:
    return ((not p.get("type") or e["type"] == p["type"])
            and (not p.get("primary") or _starts(e["primary_icd10"], p["primary"]))
            and (not p.get("holder") or e["data_holder"] == p["holder"]))


def holds(pid: str, p: dict) -> bool:
    db, pat, k = _db(), _db()["patients"][pid], p["kind"]
    if k == "condition":
        r = any(_starts(c["icd10"], p["prefixes"]) for c in db["conditions"][pid])
    elif k == "medication":
        r = any(_starts(m["atc"], p["prefixes"])
                and (not p.get("active") or (m["start_date"] <= DAY and (not m["end_date"] or m["end_date"] >= DAY)))
                for m in db["medications"][pid])
    elif k == "observation":
        if th := p.get("threshold"):
            v = _obs_value(pid, p)
            r = v is not None and OPS[th["op"]](v, float(th["value"]))
        else:
            r = any(o["loinc"] == p["loinc"] for o in db["observations"][pid])
    elif k == "age":
        r = (p.get("min") is None or pat["age"] >= p["min"]) and (p.get("max") is None or pat["age"] <= p["max"])
    elif k in ("sex", "region"):
        r = pat[k] == p["value"]
    elif k == "holder":
        r = pat["data_holder"] == p["value"]
    elif k == "encounter":
        r = any(_enc_ok(e, p) for e in db["encounters"][pid])
    else:
        raise ValueError(f"unknown predicate {k}")
    return not r if p.get("negate") else r


def _rows(recipe: dict) -> tuple[list[dict], list[dict]]:
    """(rows, patient_rows): one row per counted unit with its group attributes."""
    pats = _db()["patients"]
    prow = [{"pid": pid, "region": p["region"], "sex": p["sex"], "data_holder": p["data_holder"], "age": p["age"]}
            for pid, p in pats.items()]
    if recipe["unit"] != "encounter":
        return prow, prow
    enc = next((p for it in recipe["items"] for p in it.get("filters", []) if p["kind"] == "encounter"), {})
    years = {str(y) for y in recipe.get("encounter_years") or []}
    rows = []
    for pid, es in _db()["encounters"].items():
        p = pats[pid]
        for e in es:
            if _enc_ok(e, enc) and (not years or e["start_date"][:4] in years):
                rows.append({"pid": pid, "region": p["region"], "sex": p["sex"], "data_holder": e["data_holder"],
                             "age": p["age"], "year": int(e["start_date"][:4])})
    return rows, prow


def _band(age: int, bands: list[dict]):
    return next((b["label"] for b in bands if age >= b["min"] and (b["max"] is None or age <= b["max"])), None)


def _match(pid: str, filters: list[dict], skip_encounter: bool) -> bool:
    return all(holds(pid, p) for p in filters if not (skip_encounter and p["kind"] == "encounter"))


def compute(recipe: dict) -> tuple[dict, dict]:
    """Returns (answer, raw) in the same shape as ``engine.run``."""
    rows, prow = _rows(recipe)
    dims, domains = [], []
    for g in recipe["groups"]:
        if g["dim"] == "age_band":
            dims.append(("age_band", g["bands"]))
            domains.append([b["label"] for b in g["bands"]])
        elif g["dim"] == "year":
            dims.append(("year", None))
            domains.append(sorted(int(y) for y in (recipe.get("encounter_years") or [2023, 2024, 2025])))
        else:
            dims.append((g["dim"], None))
            domains.append(_db()["domains"][g["dim"]])
    if recipe["unit"] == "encounter" and any(it.get("unit") != "encounter" for it in recipe["items"]) \
            and any(d in ("data_holder", "year") for d, _ in dims):
        raise NotImplementedError("patient counts grouped by stay attributes")

    def key(r):
        return tuple(_band(r["age"], b) if d == "age_band" else r[d] for d, b in dims)

    enc_mode = recipe["unit"] == "encounter"
    buckets: dict = {"stay": defaultdict(list), "patient": defaultdict(list)}
    for name, frame in (("stay", rows), ("patient", prow)):
        for r in frame:
            buckets[name][key(r)].append(r)
    memo: dict = {}

    def ok(pid, fs, per_stay):
        sig = (pid, repr(fs), per_stay)
        if sig not in memo:
            memo[sig] = _match(pid, fs, per_stay)
        return memo[sig]

    answer, raw = {}, {}
    for combo in itertools.product(*domains) if dims else [()]:
        cell, rcell = {}, {}
        for it in recipe["items"]:
            per_stay = enc_mode and it.get("unit") == "encounter"
            frame = buckets["stay" if per_stay else "patient"].get(combo, [])

            def n(fs, frame=frame, per_stay=per_stay):
                return sum(ok(r["pid"], fs, per_stay) for r in frame)
            if it["type"] == "count":
                k = n(it["filters"])
                cell[it["key"]], rcell[it["key"]] = privacy.count(k), k
            elif it["type"] == "pct":
                a, b = n(it["num"]), n(it["den"])
                cell[it["key"]], rcell[it["key"]] = privacy.pct(a, b), [a, b]
            else:
                vals = [v for r in frame if ok(r["pid"], it["population"], per_stay)
                        and (v := _obs_value(r["pid"], it["observation"])) is not None]
                fn = privacy.mean if it["type"] == "mean" else privacy.median
                cell[it["key"]], rcell[it["key"]] = fn(vals), len(vals)
        for tree, c in ((answer, cell), (raw, rcell)):
            node = tree
            for k in combo[:-1]:
                node = node.setdefault(str(k), {})
            (node.setdefault(str(combo[-1]), {}) if combo else node).update(c)
    return answer, raw


def cohort(recipe: dict) -> list[str]:
    it = next(i for i in recipe["items"] if i["type"] == "count")
    return sorted(pid for pid in _db()["patients"] if _match(pid, it["filters"], False))


def verify_statistics(recipe: dict, answer: dict, raw: dict) -> dict:
    try:
        ref_answer, ref_raw = compute(recipe)
    except NotImplementedError as e:
        return {"status": "not verified", "detail": f"Second computation does not cover {e}; staff review needed."}
    if ref_answer == answer and ref_raw == raw:
        return {"status": "match", "detail": "Two independent computations agree on every number."}
    return {"status": "mismatch", "detail": "The two computations disagree; the answer is withheld."}


def verify_cohort(recipe: dict, ids: list[str]) -> dict:
    if cohort(recipe) == sorted(ids):
        return {"status": "match", "detail": f"Two independent computations select the same {len(ids)} patients."}
    return {"status": "mismatch", "detail": "The two computations select different patients; workspace not created."}
