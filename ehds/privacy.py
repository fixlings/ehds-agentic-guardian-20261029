"""The privacy gate: the only path a statistical number takes out of the app.

Small-cell rule (challenge README):
- every requested group is shown, even if empty
- counts below 10, including 0, become "<10"
- a percentage whose numerator or denominator is below 10 becomes "suppressed"
- a mean (or median) of fewer than 10 patients becomes "suppressed"
- one decimal, halves up
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

MIN_CELL = 10


def round1(x: Decimal | float | int) -> float:
    d = x if isinstance(x, Decimal) else Decimal(str(x))
    return float(d.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def count(n: int) -> int | str:
    return "<10" if n < MIN_CELL else int(n)


def pct(num: int, den: int) -> float | str:
    if num < MIN_CELL or den < MIN_CELL:
        return "suppressed"
    return round1(Decimal(num) * 100 / Decimal(den))


def mean(values: list) -> float | str:
    if len(values) < MIN_CELL:
        return "suppressed"
    total = sum((Decimal(str(v)) for v in values), Decimal(0))
    return round1(total / len(values))


def median(values: list) -> float | str:
    if len(values) < MIN_CELL:
        return "suppressed"
    s = sorted(Decimal(str(v)) for v in values)
    mid = len(s) // 2
    return round1(s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2)


def check_answer(answer) -> list[str]:
    """Independent re-check of a finished statistical answer. Returns problems found."""
    problems = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k.startswith("p-"):
                    problems.append(f"{path}: patient identifier in output")
                walk(v, f"{path}/{k}")
        elif isinstance(node, list):
            problems.append(f"{path}: list in output (possible record-level data)")
        elif isinstance(node, bool):
            return
        elif isinstance(node, int) and node < MIN_CELL:
            problems.append(f"{path}: count {node} below {MIN_CELL}")
        elif isinstance(node, str) and node.startswith("p-"):
            problems.append(f"{path}: patient identifier in output")

    walk(answer, "")
    return problems
