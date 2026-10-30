"""Executes a recipe: predicates -> SQL on the opt-out-filtered views -> grouped measures.

Raw counts never leave this module unformatted: ``run`` passes every value
through ``privacy`` before returning.
"""

from __future__ import annotations

import itertools

import pandas as pd

from . import data, privacy

ACTIVE = "m.start_date <= DATE '2025-12-31' AND (m.end_date IS NULL OR m.end_date >= DATE '2025-12-31')"


def _like(col: str, prefixes: list[str]) -> str:
    return "(" + " OR ".join(f"{col} LIKE '{p.replace(chr(39), '')}%'" for p in prefixes) + ")"


def _obs_value_sql(p: dict) -> str:
    order = "ASC" if p.get("which") == "first" else "DESC"
    return (f"(SELECT o.value FROM observations o WHERE o.patient_id = p.patient_id AND o.loinc = '{p['loinc']}' "
            f"ORDER BY o.date {order} LIMIT 1)")


def predicate_sql(p: dict) -> str:
    k = p["kind"]
    if k == "condition":
        s = f"EXISTS (SELECT 1 FROM conditions c WHERE c.patient_id = p.patient_id AND {_like('c.icd10', p['prefixes'])})"
    elif k == "medication":
        act = f" AND {ACTIVE}" if p.get("active") else ""
        s = f"EXISTS (SELECT 1 FROM medications m WHERE m.patient_id = p.patient_id AND {_like('m.atc', p['prefixes'])}{act})"
    elif k == "observation":
        if th := p.get("threshold"):
            s = f"coalesce({_obs_value_sql(p)} {th['op']} {float(th['value'])}, false)"
        else:
            s = f"EXISTS (SELECT 1 FROM observations o WHERE o.patient_id = p.patient_id AND o.loinc = '{p['loinc']}')"
    elif k == "age":
        parts = []
        if p.get("min") is not None:
            parts.append(f"p.age >= {int(p['min'])}")
        if p.get("max") is not None:
            parts.append(f"p.age <= {int(p['max'])}")
        s = " AND ".join(parts) or "true"
    elif k == "sex":
        s = f"p.sex = '{p['value']}'"
    elif k == "region":
        s = f"p.region = '{p['value']}'"
    elif k == "holder":
        s = f"p.data_holder = '{p['value']}'"
    elif k == "encounter":
        conds = ["e.patient_id = p.patient_id"] + _enc_conds(p)
        s = f"EXISTS (SELECT 1 FROM encounters e WHERE {' AND '.join(conds)})"
    else:
        raise ValueError(f"unknown predicate {k}")
    return f"NOT ({s})" if p.get("negate") else f"({s})"


def _enc_conds(p: dict) -> list[str]:
    c = []
    if p.get("type"):
        c.append(f"e.type = '{p['type']}'")
    if p.get("primary"):
        c.append(_like("e.primary_icd10", p["primary"]))
    if p.get("holder"):
        c.append(f"e.data_holder = '{p['holder']}'")
    return c


def _sig(p: dict) -> str:
    return repr(sorted((k, repr(v)) for k, v in p.items()))


def build_frame(recipe: dict) -> tuple[pd.DataFrame, str, dict]:
    """One row per patient (or per encounter) with a boolean column per predicate."""
    preds, values = {}, {}
    for it in recipe["items"]:
        for key in ("filters", "num", "den", "population"):
            for p in it.get(key) or []:
                if not (recipe["unit"] == "encounter" and p["kind"] == "encounter" and it.get("unit") == "encounter"):
                    preds.setdefault(_sig(p), p)
        if it["type"] in ("mean", "median"):
            values.setdefault(_sig(it["observation"]), it["observation"])
    cols = {f"f{i}": (sig, p) for i, (sig, p) in enumerate(preds.items())}
    vcols = {f"v{i}": (sig, p) for i, (sig, p) in enumerate(values.items())}
    select = ["p.patient_id", "p.age", "p.sex", "p.region", "p.data_holder"]
    select += [f"{predicate_sql(p)} AS {c}" for c, (_, p) in cols.items()]
    select += [f"{_obs_value_sql(p)} AS {c}" for c, (_, p) in vcols.items()]
    if recipe["unit"] == "encounter":
        enc = next((p for it in recipe["items"] for p in it.get("filters", []) if p["kind"] == "encounter"), {})
        conds = _enc_conds(enc)
        if recipe.get("encounter_years"):
            conds.append("year(e.start_date) IN (" + ",".join(str(int(y)) for y in recipe["encounter_years"]) + ")")
        select[4] = "e.data_holder"
        select.insert(1, "year(e.start_date) AS year")
        sql = (f"SELECT e.encounter_id, {', '.join(select)} FROM encounters e JOIN patients p USING (patient_id)"
               + (f" WHERE {' AND '.join(conds)}" if conds else ""))
    else:
        sql = f"SELECT {', '.join(select)} FROM patients p"
    df = data.query(sql)
    colmap = {sig: c for c, (sig, _) in cols.items()}
    colmap.update({"value:" + sig: c for c, (sig, _) in vcols.items()})
    return df, sql, colmap


def _mask(df: pd.DataFrame, filters: list[dict], colmap: dict, unit: str, item_unit: str | None) -> pd.Series:
    m = pd.Series(True, index=df.index)
    for p in filters:
        if unit == "encounter" and p["kind"] == "encounter" and item_unit == "encounter":
            continue
        m &= df[colmap[_sig(p)]].astype(bool)
    return m


def _group_keys(recipe: dict, df: pd.DataFrame):
    dims, domains = [], []
    for g in recipe["groups"]:
        if g["dim"] == "age_band":
            labels = []
            col = pd.Series(None, index=df.index, dtype=object)
            for b in g["bands"]:
                sel = (df.age >= b["min"]) & ((df.age <= b["max"]) if b["max"] is not None else True)
                col[sel & col.isna()] = b["label"]
                labels.append(b["label"])
            df["age_band"] = col
            dims.append("age_band")
            domains.append(labels)
        elif g["dim"] == "year":
            dims.append("year")
            domains.append(sorted(int(y) for y in (recipe.get("encounter_years") or [2023, 2024, 2025])))
        else:
            dims.append(g["dim"])
            domains.append(data.domain(g["dim"]))
    return dims, domains


def run(recipe: dict) -> tuple[dict, dict]:
    df, sql, colmap = build_frame(recipe)
    dims, domains = _group_keys(recipe, df)
    unit = recipe["unit"]
    pdf = df.drop_duplicates("patient_id") if unit == "encounter" else df
    answer: dict = {}
    raw: dict = {}
    for combo in itertools.product(*domains) if dims else [()]:
        sel = pd.Series(True, index=df.index)
        for d, v in zip(dims, combo):
            sel &= df[d] == v
        cell, rcell = {}, {}
        for it in recipe["items"]:
            iu = it.get("unit")
            frame = df if (unit == "encounter" and iu == "encounter") else pdf
            s = sel.reindex(frame.index)
            if it["type"] == "count":
                n = int((s & _mask(frame, it["filters"], colmap, unit, iu)).sum())
                cell[it["key"]], rcell[it["key"]] = privacy.count(n), n
            elif it["type"] == "pct":
                num = int((s & _mask(frame, it["num"], colmap, unit, iu)).sum())
                den = int((s & _mask(frame, it["den"], colmap, unit, iu)).sum())
                cell[it["key"]], rcell[it["key"]] = privacy.pct(num, den), [num, den]
            else:
                vals = frame.loc[s & _mask(frame, it["population"], colmap, unit, iu), colmap["value:" + _sig(it["observation"])]].dropna()
                fn = privacy.mean if it["type"] == "mean" else privacy.median
                cell[it["key"]], rcell[it["key"]] = fn(list(vals)), len(vals)
        _put(answer, combo, cell)
        _put(raw, combo, rcell)
    return answer, {"sql": sql, "raw_counts_staff_only": raw}


def _put(tree: dict, combo: tuple, cell: dict):
    if not combo:
        tree.update(cell)
        return
    node = tree
    for k in combo[:-1]:
        node = node.setdefault(str(k), {})
    node[str(combo[-1])] = cell


def cohort_ids(recipe: dict) -> list[str]:
    """Patient IDs for an SPE cohort (first count item). Used only to fill the secure workspace."""
    df, _, colmap = build_frame(recipe)
    it = next(i for i in recipe["items"] if i["type"] == "count")
    return sorted(df.loc[_mask(df, it["filters"], colmap, recipe["unit"], it.get("unit")), "patient_id"].unique())
