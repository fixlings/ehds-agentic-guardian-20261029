"""Unseen, jury-style requests checked against independent hand-written SQL."""

import json
from pathlib import Path

from ehds import data, privacy
from ehds.pipeline import handle

J = Path(__file__).parent / "jury"


def load(name):
    return json.loads((J / f"{name}.json").read_text(encoding="utf-8"))


def test_primary_diagnosis_stays_by_hospital():
    out = handle(load("J01"))
    assert out["decision"] == "APPROVE_STATISTICAL"
    ref = data.query("""SELECT data_holder, count(*) n FROM encounters
        WHERE type='inpatient' AND primary_icd10 LIKE 'I50%' AND year(start_date)=2024 GROUP BY 1""")
    ref = dict(zip(ref.data_holder, ref.n))
    for h in data.domain("data_holder"):
        assert list(out["answer"][h].values()) == [privacy.count(int(ref.get(h, 0)))]


def test_active_statin_share_by_hospital():
    out = handle(load("J02"))
    ref = data.query("""
        SELECT p.data_holder, count(*) n,
          count(*) FILTER (WHERE EXISTS (SELECT 1 FROM medications m WHERE m.patient_id=p.patient_id
            AND m.atc LIKE 'C10AA%' AND m.start_date <= DATE '2025-12-31'
            AND (m.end_date IS NULL OR m.end_date >= DATE '2025-12-31'))) k
        FROM patients p WHERE p.age >= 65
          AND EXISTS (SELECT 1 FROM conditions c WHERE c.patient_id=p.patient_id AND c.icd10 LIKE 'J44%')
        GROUP BY 1""").set_index("data_holder")
    for h in data.domain("data_holder"):
        n, k = int(ref.n.get(h, 0)), int(ref.k.get(h, 0))
        assert list(out["answer"][h].values()) == [privacy.count(n), privacy.count(k), privacy.pct(k, n)]


def test_group_by_sex():
    out = handle(load("J03"))
    ref = data.query("""SELECT sex, count(*) n FROM patients p WHERE EXISTS
        (SELECT 1 FROM conditions c WHERE c.patient_id=p.patient_id AND c.icd10 LIKE 'J45%') GROUP BY 1""")
    ref = dict(zip(ref.sex, ref.n))
    assert {s: list(v.values())[0] for s, v in out["answer"].items()} == {s: privacy.count(int(n)) for s, n in ref.items()}


def test_plain_english_without_codes():
    req = load("J03")
    req["definitions"] = ["Report per sex: patients with asthma."]
    assert handle(req)["answer"] == handle(load("J03"))["answer"]
