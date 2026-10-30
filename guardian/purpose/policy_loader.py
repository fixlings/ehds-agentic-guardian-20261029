"""Load deterministic policy rules from the shared contract file."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

POLICY_PATH = Path(__file__).resolve().parents[1] / "contracts" / "policy_minimum.json"


@lru_cache(maxsize=1)
def load_policy_minimum() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))
