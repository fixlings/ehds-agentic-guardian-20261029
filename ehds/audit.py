"""Append-only, tamper-evident JSONL audit log (one line per event, no patient-level data).

Each entry stores the hash of the previous entry and its own hash, like a seal chain:
changing or deleting any past line breaks the chain, and ``verify`` finds where.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

LOG = Path(os.environ.get("EHDS_LOG_DIR", Path(__file__).resolve().parent.parent / "logs")) / "audit.jsonl"
GENESIS = "0" * 64
_lock = threading.Lock()
_last: dict = {"key": None, "hash": GENESIS}


def _seal(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _lines() -> list[str]:
    return LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []


def log(event: str, **fields) -> None:
    with _lock:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        key = (str(LOG), LOG.stat().st_size if LOG.exists() else 0)
        if _last["key"] != key:
            lines = _lines()
            _last["hash"] = json.loads(lines[-1]).get("hash", GENESIS) if lines else GENESIS
        prev = _last["hash"]
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **fields,
                 "prev": prev}
        entry["hash"] = _seal(entry)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _last.update(key=(str(LOG), LOG.stat().st_size), hash=entry["hash"])


def verify() -> dict:
    """Checks the whole chain. Returns {"ok", "entries", "broken_at"} (broken_at = 1-based line)."""
    prev = GENESIS
    lines = _lines()
    for i, line in enumerate(lines, 1):
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            return {"ok": False, "entries": len(lines), "broken_at": i}
        if e.get("prev") != prev or e.get("hash") != _seal(e):
            return {"ok": False, "entries": len(lines), "broken_at": i}
        prev = e["hash"]
    return {"ok": True, "entries": len(lines), "broken_at": None}


def tail(n: int = 50) -> list[dict]:
    return [json.loads(x) for x in _lines()[-n:]]
