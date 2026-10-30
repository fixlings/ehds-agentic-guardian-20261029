"""What each applicant has already received: earlier requests and released patient groups.

Kept inside the application only, never in an answer. A new number whose patient group differs from an
earlier released group by 1-9 patients is held back, because subtracting the two would reveal a small group
(e.g. "Austria: 72" and "Austria without Burgenland: 71" reveal one woman in Burgenland).
"""

from __future__ import annotations

import re
import threading

from .privacy import MIN_CELL

_lock = threading.Lock()
_released: dict[str, list[dict]] = {}
_requests: dict[str, list[dict]] = {}


def reset() -> None:
    with _lock:
        _released.clear()
        _requests.clear()


def applicant(req: dict) -> str:
    org = (req.get("applicant") or {}).get("organisation", "")
    return re.sub(r"\W+", " ", org).strip().lower() or "unknown"


def history(req: dict) -> list[dict]:
    """Earlier requests by the same organisation (not this one)."""
    with _lock:
        return [h for h in _requests.get(applicant(req), []) if h["id"] != req.get("id")]


def remember_request(req: dict, decision: str, articles: list[str]) -> None:
    with _lock:
        hist = _requests.setdefault(applicant(req), [])
        hist[:] = [h for h in hist if h["id"] != req.get("id")]
        hist.append({"id": req.get("id"), "purpose": str(req.get("purpose", ""))[:300],
                     "requested_as": req.get("requested_as", ""), "decision": decision, "articles": articles})


def _suppress(answer: dict, path: tuple, key: str) -> None:
    node = answer
    for k in path:
        node = node[str(k)]
    node[key] = "suppressed"


def guard(req: dict, answer: dict, cells: list[tuple]) -> list[dict]:
    """Hold back cells that differ from an earlier release by 1-9 patients. Returns what was held back."""
    held, keep = [], []
    with _lock:
        prev = _released.setdefault(applicant(req), [])
        for path, key, ids in cells:
            label = " / ".join([*map(str, path), key])
            clash = next((p for p in prev if 0 < len(ids ^ p["ids"]) < MIN_CELL), None)
            if clash:
                _suppress(answer, path, key.split("#")[0])
                held.append({"cell": label, "earlier_request": clash["request_id"], "earlier_cell": clash["cell"]})
            else:
                keep.append({"request_id": req.get("id"), "cell": label, "ids": ids})
        if not held:
            prev.extend(keep)
        else:
            bad = {h["cell"].split("#")[0] for h in held}
            prev.extend(k for k in keep if k["cell"].split("#")[0] not in bad)
    return held
