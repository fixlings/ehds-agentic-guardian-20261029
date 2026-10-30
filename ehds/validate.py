"""One-command validation report (IQ / OQ / PQ + traceability): python -m ehds.validate

IQ  installation: Python version, pinned packages, data files match the manifest.
OQ  operation:    the full test suite.
PQ  performance:  the challenge metrics and a stress test of generated requests.
Writes docs/validation_report.md. Shows the method; it is not a formal validation.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import platform
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

import pytest

from . import certificate, evaluate, stress
from .trace import REQUIREMENTS

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "docs" / "validation_report.md"


def iq() -> list[tuple[str, bool, str]]:
    rows = [("Python 3.10 or newer", sys.version_info >= (3, 10), platform.python_version())]
    for line in (ROOT / "requirements.txt").read_text().split():
        name, want = line.split("==")
        have = metadata.version(name)
        rows.append((f"{name} pinned to {want}", have == want, have))
    for line in (ROOT / "data" / "MANIFEST.sha256").read_text().splitlines():
        digest, name = line.split()
        have = hashlib.sha256((ROOT / "data" / name).read_bytes()).hexdigest()
        rows.append((f"data/{name} unchanged", have == digest, have[:12]))
    return rows


class _Collect:
    def __init__(self):
        self.results: dict[str, bool] = {}

    def pytest_runtest_logreport(self, report):
        if report.when == "call" or report.failed:
            name = report.nodeid.split("::")[-1].split("[")[0]
            self.results[name] = self.results.get(name, True) and report.passed


def oq() -> dict[str, bool]:
    c = _Collect()
    with contextlib.redirect_stdout(io.StringIO()):
        pytest.main(["-q", "-p", "no:cacheprovider", str(ROOT / "tests")], plugins=[c])
    return c.results


def pq(n: int) -> tuple[str, dict]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        evaluate.main()
    return buf.getvalue().split("\n\n")[-1].strip(), stress.run(n)


def main(argv: list[str]) -> int:
    n = int(argv[0]) if argv else 300
    i, tests = iq(), oq()
    metrics, s = pq(n)
    trace = [(rid, text, src, code, ts, all(tests.get(t, False) for t in ts)) for rid, text, src, code, ts in REQUIREMENTS]
    ok = all(r[1] for r in i) and all(tests.values()) and not s["failures"] and all(r[5] for r in trace)
    mark = {True: "PASS", False: "FAIL"}
    out = [
        "# Validation report", "",
        f"- Date: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        f"- Code fingerprint: `{certificate.code_hash()[:16]}`",
        f"- Data fingerprint: `{certificate.data_hash()[:16]}`",
        f"- **Overall: {mark[ok]}**", "",
        "Shows the validation method (IQ / OQ / PQ, traceability). It is not a formal validation or certification.", "",
        "## 1. Installation (IQ)", "", "| Check | Result | Found |", "|---|---|---|",
        *[f"| {a} | {mark[b]} | `{c}` |" for a, b, c in i], "",
        "## 2. Operation (OQ): test suite", "",
        f"{sum(tests.values())} of {len(tests)} tests passed, with network access disabled.", "",
        "## 3. Performance (PQ)", "", "Challenge metrics on the 10 example requests:", "", "```", metrics, "```", "",
        f"Stress test: **{s['requests']} generated requests** (seed {s['seed']}), decisions {s['decisions']}. "
        f"Each checked for correct decision, full understanding, double computation agreement and privacy; "
        f"{s['reruns']} rerun for identical results. **Failures: {len(s['failures'])}.**", "",
        "## 4. Traceability: requirement → code → tests", "",
        "| ID | Requirement | Source | Implemented in | Proven by | Status |", "|---|---|---|---|---|---|",
        *[f"| {r[0]} | {r[1]} | {r[2]} | `{r[3]}` | {', '.join(f'`{t}`' for t in r[4])} | {mark[r[5]]} |" for r in trace],
        "",
    ]
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(out), encoding="utf-8")
    print(f"IQ {mark[all(r[1] for r in i)]} | OQ {sum(tests.values())}/{len(tests)} tests | "
          f"PQ {s['requests']} generated requests, {len(s['failures'])} failures | "
          f"traceability {sum(r[5] for r in trace)}/{len(trace)} | OVERALL {mark[ok]}")
    print(f"Report: {REPORT.relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
