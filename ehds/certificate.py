"""Result certificates: a fingerprint that lets anyone reproduce a released result exactly.

The certificate hashes the code version, the data files, the request, the recipe
(how the request was read) and the result. Rerunning the same request on the same
code and data must give the same certificate (``python -m ehds.reproduce``).
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULT_FIELDS = ("decision", "articles", "answer", "patient_count", "record_types")


def canon(x) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def sha(x) -> str:
    b = x if isinstance(x, bytes) else canon(x).encode("utf-8")
    return hashlib.sha256(b).hexdigest()


def _files_hash(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


@lru_cache(maxsize=1)
def code_hash() -> str:
    return _files_hash(list((ROOT / "ehds").glob("*.py")))


@lru_cache(maxsize=1)
def data_hash() -> str:
    return _files_hash(list((ROOT / "data").glob("*.csv")))


def issue(req: dict, out: dict) -> dict:
    cert = {
        "code": code_hash(),
        "data": data_hash(),
        "request": sha(req),
        "recipe": sha(out.get("staff", {}).get("recipe")),
        "result": sha({k: out.get(k) for k in RESULT_FIELDS}),
        "double_check": out.get("verification", {}).get("status", "not applicable"),
    }
    cert["id"] = sha(cert)[:16]
    return cert
