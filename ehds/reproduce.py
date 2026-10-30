"""Reproduce a released result: python -m ehds.reproduce requests/R01.json [certificate-id]"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .pipeline import handle


def main(argv: list[str]) -> int:
    req = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    cert = handle(req, create_workspace=False)["certificate"]
    for k, v in cert.items():
        print(f"{k:13} {v}")
    if len(argv) > 1:
        same = cert["id"] == argv[1]
        print("\nREPRODUCED: identical result" if same else "\nNOT REPRODUCED: result differs")
        return 0 if same else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
