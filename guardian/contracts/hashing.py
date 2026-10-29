"""Canonical JSON and query fingerprints shared by the executor and tests."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel


def canonical_dumps(model: BaseModel) -> str:
    payload = model.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def query_fingerprint(query: BaseModel, fixture_version: str) -> str:
    body = fixture_version + "\n" + canonical_dumps(query)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
