"""Plain-language explanations of a decision and a recipe, for applicants and staff."""

from __future__ import annotations

import re

from . import judge

DIM_LABELS = {"region": "Region", "age_band": "Age band", "sex": "Sex", "data_holder": "Hospital", "year": "Year"}


def filter_text(p: dict) -> str:
    k = p["kind"]
    if k == "condition":
        s = f"diagnosis with ICD-10 code {' / '.join(p['prefixes'])}*"
    elif k == "medication":
        s = ("active " if p.get("active") else "") + f"medication with ATC code {' / '.join(p['prefixes'])}*"
    elif k == "observation":
        s = f"{p.get('which', 'latest')} {p.get('name', '')} (LOINC {p['loinc']})".replace("  ", " ")
        if th := p.get("threshold"):
            s += f" {th['op']} {th['value']:g}"
    elif k == "age":
        lo, hi = p.get("min"), p.get("max")
        s = f"aged {lo}–{hi}" if lo is not None and hi is not None else (f"aged {lo}+" if lo is not None else f"aged up to {hi}")
    elif k == "sex":
        s = p["value"]
    elif k in ("region", "holder"):
        s = f"at {p['value']}"
    elif k == "encounter":
        s = f"{p.get('type') or ''} stay".strip()
        if p.get("primary"):
            s += f" with main diagnosis {' / '.join(p['primary'])}*"
    else:
        s = p.get("phrase", k)
    return f"not {s}" if p.get("negate") else s


def filters_text(ps: list[dict]) -> str:
    return " and ".join(filter_text(p) for p in ps) or "all patients"


def concept_text(c: dict) -> str:
    if c["kind"] == "formula":
        return f"{c['num']} ÷ {c['den']} × 100"
    if c["kind"] == "composite":
        return c["phrase"]
    return filter_text(c)


def measure_text(it: dict) -> str:
    t = it["type"]
    if t == "count":
        return "Number of " + ("hospital stays" if it.get("unit") == "encounter" else "patients") + ": " + filters_text(it["filters"])
    if t == "pct":
        return f"% = ({filters_text(it['num'])}) ÷ ({filters_text(it['den'])})"
    return f"{t.capitalize()} of {filter_text(it['observation'])} among: {filters_text(it['population'])}"


def recipe_summary(rc: dict) -> dict:
    return {
        "grouped_by": [DIM_LABELS.get(g["dim"], g["dim"]) for g in rc["groups"]],
        "counting": "hospital stays" if rc["unit"] == "encounter" else "patients",
        "measures": [{"Measure": it["label"][:1].upper() + it["label"][1:], "How it is computed": measure_text(it)}
                     for it in rc["items"]],
        "terms": [{"Term": n, "Meaning": concept_text(c),
                   "Source": "local code list" if c.get("mapped") else "applicant's definition"}
                  for n, c in rc["concepts"].items()],
        "notes": rc["notes"],
        "problems": rc["errors"] + [f"Not understood: {u}" for u in rc["unparsed"]],
    }


def decision_steps(req: dict, out: dict) -> list[dict]:
    """The rule checks in the order the README prescribes, with evidence."""
    text = " ".join([req.get("purpose", ""), req.get("question", "")])
    steps = []
    ai = out.get("ai_check") or {}
    if ai.get("status") == "ok":
        verdict = f"forbidden: {ai['category']}" if ai["prohibited"] else f"purpose: {ai['allowed_purpose']}"
        steps.append({"check": f"AI check ({ai['model']})", "ok": not ai["prohibited"],
                      "result": f"{verdict}. {ai['reason']}", "evidence": f'"{ai["quote"]}"' if ai["quote"] else ""})
        for c in ai.get("checks", []):
            if c["risk"] != "low":
                steps.append({"check": f"AI: {c['label']}", "ok": False, "result": f"{c['risk']} risk. {c['finding']}",
                              "evidence": f'"{c["quote"]}"' + ("" if c["quote_verified"] else " (not in the request, ignored)")
                              if c["quote"] else ""})
    else:
        steps.append({"check": "AI check", "ok": True, "result": ai.get("status", "off") + ": rules decide alone", "evidence": ""})
    hits = judge.prohibited_hits(text)
    steps.append({"check": "Forbidden purpose? (Art. 54)", "ok": not hits,
                  "result": "Yes: " + "; ".join(h["label"] for h in hits) if hits else "No",
                  "evidence": ", ".join(f'"{q}"' for h in hits for q in h["quotes"])})
    if hits:
        return steps
    allowed = [label for pat, label in judge.ALLOWED_PURPOSE_HINTS if re.search(pat, text, re.I)]
    steps.append({"check": "Allowed purpose (Art. 53)", "ok": bool(allowed),
                  "result": ", ".join(dict.fromkeys(allowed)) or "not clearly recognised", "evidence": ""})
    for h in out.get("held_back", []):
        steps.append({"check": "Release memory (differencing)", "ok": False,
                      "result": f"{h['cell']} held back: differs from {h['earlier_request']} ({h['earlier_cell']}) by fewer than 10 patients",
                      "evidence": ""})
    if not allowed or out.get("decision") == "REJECT":
        steps.append({"check": "Decision", "ok": False, "result": out.get("reason", ""), "evidence": ""})
        return steps
    kind = req.get("requested_as", "")
    steps.append({"check": "What was asked for?", "ok": True,
                  "result": "Statistics (health data request)" if kind == "health data request"
                  else "Records (health data access application)", "evidence": ""})
    if kind != "health data request":
        m = judge.SPE_JUSTIFICATION.search(text)
        steps.append({"check": "Explains why statistics are not enough? (Art. 66)", "ok": bool(m),
                      "result": "Yes" if m else "No: records not justified, statistics instead",
                      "evidence": f'"{m.group(0)}"' if m else ""})
    return steps


def privacy_summary(answer: dict) -> dict:
    vals = []

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        else:
            vals.append(x)
    walk(answer or {})
    hidden = sum(v in ("<10", "suppressed") for v in vals)
    return {"cells": len(vals), "hidden": hidden}
