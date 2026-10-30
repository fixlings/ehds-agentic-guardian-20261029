"""Validation features: double computation, result certificates, tamper-evident audit log, stress test."""

import json
from pathlib import Path

from ehds import audit, reference, stress
from ehds.pipeline import handle

ROOT = Path(__file__).resolve().parents[2]
FILES = sorted((ROOT / "requests").glob("R*.json")) + sorted((ROOT / "tests" / "engine" / "jury").glob("J*.json"))


def load(f):
    return json.loads(Path(f).read_text(encoding="utf-8"))


def test_double_computation_agrees_on_every_request():
    for f in FILES:
        out = handle(load(f), create_workspace=False)
        if out["decision"] != "REJECT":
            assert out["verification"]["status"] == "match", f.name


def test_disagreement_withholds_the_answer(monkeypatch):
    monkeypatch.setattr(reference, "compute", lambda rc: ({"x": 1}, {"x": 1}))
    out = handle(load(ROOT / "requests" / "R01.json"))
    assert out["verification"]["status"] == "mismatch"
    assert "error" in out["answer"] and out["staff"]["flags"]


def test_cohort_disagreement_blocks_the_workspace(monkeypatch):
    monkeypatch.setattr(reference, "cohort", lambda rc: [])
    out = handle(load(ROOT / "requests" / "R05.json"))
    assert out["verification"]["status"] == "mismatch" and "workspace" not in out


def test_certificate_is_reproducible_and_specific():
    r = load(ROOT / "requests" / "R01.json")
    a, b = handle(r, create_workspace=False), handle(r, create_workspace=False)
    assert a["certificate"]["id"] == b["certificate"]["id"]
    r["definitions"][0] = "Adult = aged 21 or older on 31 December 2025."
    assert handle(r, create_workspace=False)["certificate"]["id"] != a["certificate"]["id"]


def test_audit_chain_detects_tampering():
    for i in range(3):
        audit.log("test", n=i)
    assert audit.verify() == {"ok": True, "entries": 3, "broken_at": None}
    lines = audit.LOG.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace('"n": 1', '"n": 7')
    audit.LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert audit.verify()["broken_at"] == 2


def test_generated_requests_small_sample():
    s = stress.run(40, seed=7)
    assert s["failures"] == [], s["failures"][:3]
