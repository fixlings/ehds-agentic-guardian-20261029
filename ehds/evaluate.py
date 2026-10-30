"""Scores the prototype on the challenge's five metrics: python -m ehds.evaluate"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from . import privacy
from .pipeline import handle

ROOT = Path(__file__).resolve().parent.parent


def _leaves(x):
    if isinstance(x, dict):
        return [v for k in x for v in _leaves(x[k])]
    return [x]


def numbers_match(got, exp) -> bool:
    """Same groups, same values in the same order; our own column names are ignored."""
    if isinstance(exp, dict) and isinstance(got, dict) and set(exp) <= set(got):
        return all(numbers_match(got[k], exp[k]) for k in exp)
    return _leaves(got) == _leaves(exp)


def main(req_dir: Path = ROOT / "requests") -> int:
    expected = json.loads((req_dir / "expected.json").read_text(encoding="utf-8"))
    rows, dec_ok, num_ok, num_total, leaks, consistent, slowest = [], 0, 0, 0, 0, 0, 0.0
    for f in sorted(req_dir.glob("R*.json")):
        req = json.loads(f.read_text(encoding="utf-8"))
        e = expected[req["id"]]
        t0 = time.perf_counter()
        a = handle(req)
        slowest = max(slowest, time.perf_counter() - t0)
        b = handle(req)
        same = all(a.get(k) == b.get(k) for k in ("decision", "articles", "answer", "patient_count"))
        consistent += same
        d_ok = a["decision"] == e["decision"] and a["articles"] == e["articles"]
        dec_ok += d_ok
        n_ok = None
        if e.get("answer") is not None:
            num_total += 1
            n_ok = numbers_match(a.get("answer"), e["answer"])
            num_ok += n_ok
        leak = privacy.check_answer(a.get("answer", {}))
        leaks += bool(leak)
        rows.append((req["id"], a["decision"], d_ok, n_ok, not leak, same))
    print(f"{'id':5} {'decision':20} {'dec':5} {'nums':5} {'priv':5} {'same':5}")
    for r in rows:
        print(f"{r[0]:5} {r[1]:20} {str(r[2]):5} {str(r[3]):5} {str(r[4]):5} {str(r[5]):5}")
    print(f"\nCorrect decisions: {dec_ok}/{len(rows)}")
    print(f"Correct numbers:   {num_ok}/{num_total}")
    print(f"Privacy leaks:     {leaks}")
    print(f"Consistency:       {consistent}/{len(rows)}")
    print(f"Slowest request:   {slowest:.2f} s")
    return 0 if dec_ok == len(rows) and num_ok == num_total and leaks == 0 and consistent == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
