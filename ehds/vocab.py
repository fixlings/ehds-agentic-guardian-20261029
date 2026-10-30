"""Local, offline terminology: plain-English names -> ICD-10 / ATC / LOINC.

Used when a definition names a condition, drug or measurement without giving
its code. Definitions that give codes always win. Everything here is static,
so the mapping is reproducible and reviewable.
"""

from __future__ import annotations

import re

CONDITIONS = {
    "type 2 diabetes": ["E11"], "diabetes type 2": ["E11"], "t2d": ["E11"], "diabetes": ["E11"],
    "hypertension": ["I10"], "high blood pressure": ["I10"],
    "obesity": ["E66"], "asthma": ["J45"], "depression": ["F32"],
    "chronic kidney disease": ["N18"], "ckd": ["N18"], "kidney disease": ["N18"],
    "atrial fibrillation": ["I48"], "copd": ["J44"], "chronic obstructive pulmonary disease": ["J44"],
    "heart failure": ["I50"], "stroke": ["I63"], "cerebral infarction": ["I63"], "breast cancer": ["C50"],
    "appendicitis": ["K35"], "urinary tract infection": ["N39"], "uti": ["N39"], "pneumonia": ["J18"],
    "hip fracture": ["S72"], "femur fracture": ["S72"], "fracture of femur": ["S72"],
}

DRUGS = {
    "statin": ["C10AA"], "ace inhibitor": ["C09AA"], "arb": ["C09CA"],
    "angiotensin receptor blocker": ["C09CA"], "sglt2 inhibitor": ["A10BK"], "metformin": ["A10BA02"],
    "oral anticoagulant": ["B01AA", "B01AF"], "anticoagulant": ["B01AA", "B01AF"], "doac": ["B01AF"],
    "vitamin k antagonist": ["B01AA"], "beta blocker": ["C07AB"], "calcium channel blocker": ["C08CA"],
    "ssri": ["N06AB"], "antidepressant": ["N06AB"], "insulin": ["A10AE"], "glp-1 receptor agonist": ["A10BJ"],
    "glp-1 agonist": ["A10BJ"], "loop diuretic": ["C03CA"], "laba/ics inhaler": ["R03AK"],
    "inhaled corticosteroid": ["R03BB"], "antidiabetic": ["A10"], "blood thinner": ["B01AA", "B01AF"],
}

OBSERVATIONS = {
    "hba1c": "4548-4", "bmi": "39156-5", "body mass index": "39156-5",
    "systolic blood pressure": "8480-6", "blood pressure": "8480-6", "egfr": "62238-1",
    "kidney function": "62238-1",
}

REGIONS = ["Burgenland", "Kärnten", "Niederösterreich", "Oberösterreich", "Salzburg", "Steiermark", "Tirol",
           "Vorarlberg", "Wien"]
HOSPITALS = ["Klinikum Nord", "Klinikum Süd", "Klinikum West"]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().strip())


def lookup_condition(name: str) -> list[str] | None:
    n = _norm(name)
    return CONDITIONS.get(n) or CONDITIONS.get(n.rstrip("s"))


def lookup_drug(name: str) -> list[str] | None:
    n = _norm(name)
    return DRUGS.get(n) or DRUGS.get(n.rstrip("s"))


def lookup_observation(name: str) -> str | None:
    return OBSERVATIONS.get(_norm(name))
