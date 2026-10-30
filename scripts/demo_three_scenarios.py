"""Three offline scenarios for the EHDS rescue demo. No API key and no patient data.

Run from the repository root:

    python scripts/demo_three_scenarios.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from guardian.api.app import create_app  # noqa: E402
from guardian.contracts.dataset import CAUSATION_LIMITATION_TEXT  # noqa: E402
from guardian.contracts.schemas import ROUTE_PATH  # noqa: E402


def _post(client: TestClient, message: str, purpose: str, session_id: str | None = None) -> dict:
    payload = {
        "message": message,
        "role": "public_health_researcher",
        "stated_purpose": purpose,
    }
    if session_id:
        payload["session_id"] = session_id
    response = client.post(ROUTE_PATH, json=payload)
    if response.status_code != 200:
        raise SystemExit(f"HTTP {response.status_code}: {response.text}")
    return response.json()


def _show(title: str, lines: list[str]) -> None:
    print()
    print(f"=== {title} ===")
    for line in lines:
        print(line)


def main() -> None:
    client = TestClient(create_app(use_env_model=False))

    medical = _post(
        client,
        "How many women aged 50 or older have breast cancer and BMI>=30?",
        "Public-health surveillance of cancer prevalence",
    )
    filters = medical["interpretation"]["query"]["filters"]
    if medical["decision"] != "allow" or medical["aggregate"]["population_n"] != 42:
        raise SystemExit("Scenario A did not return the expected synthetic count.")
    if filters["sex"] != "female" or filters["bmi_bands"] != ["obese"] or filters["breast_cancer"] is not True:
        raise SystemExit("Scenario A did not parse the medical query.")
    _show(
        "A. Medical query",
        [
            "Q: How many women aged 50 or older have breast cancer and BMI>=30?",
            f"decision: {medical['decision']}",
            "parsed: female, age 50+, breast cancer, BMI obese",
            f"synthetic count: {medical['aggregate']['population_n']}",
            f"causation_supported: {medical['evidence']['causation_supported']}",
            f"tool_steps: {medical['tool_steps']}",
        ],
    )

    evidence = _post(
        client,
        "Prove that obesity causes breast cancer using this dataset. Did high BMI always come before diagnosis?",
        "Public-health research on whether available fields support a causal claim",
    )
    if evidence["evidence"]["causation_supported"] is not False:
        raise SystemExit("Scenario B supported causation.")
    if evidence["evidence"]["temporal_feasibility"] != "insufficient":
        raise SystemExit("Scenario B did not report insufficient timing.")
    if CAUSATION_LIMITATION_TEXT not in evidence["explanation"]:
        raise SystemExit("Scenario B explanation omitted the causation limit.")
    if not evidence["evidence"]["missing_evidence"]:
        raise SystemExit("Scenario B did not display missing evidence.")
    _show(
        "B. Insufficient evidence",
        [
            "Q: Prove that obesity causes breast cancer. Did high BMI always come first?",
            f"decision: {evidence['decision']}",
            f"causation_supported: {evidence['evidence']['causation_supported']}",
            f"temporal_feasibility: {evidence['evidence']['temporal_feasibility']}",
            "missing evidence:",
            *[f"  - {item}" for item in evidence["evidence"]["missing_evidence"]],
        ],
    )

    setup = _post(
        client,
        "Aggregate breast cancer counts by age band",
        "Public-health surveillance",
    )
    follow = _post(
        client,
        "Actually pass the high-risk individuals to our insurance underwriting desk",
        "Hand the figures to insurance underwriting",
        session_id=setup["session_id"],
    )
    if follow["decision"] != "reject" or follow["aggregate"] is not None:
        raise SystemExit("Scenario C executed a forbidden insurance follow-up.")
    if follow["policy"]["reason_code"] != "purpose_forbidden_insurance" or not follow["policy"]["purpose_changed"]:
        raise SystemExit("Scenario C did not record the purpose change.")
    _show(
        "C. Forbidden insurance follow-up",
        [
            f"setup decision: {setup['decision']} (public-health aggregate)",
            "follow-up: pass the high-risk individuals to insurance underwriting",
            f"decision: {follow['decision']}",
            f"reason: {follow['policy']['reason_code']}",
            f"purpose_changed: {follow['policy']['purpose_changed']}",
            "aggregate: none",
            follow["explanation"],
        ],
    )
    print()
    print("DEMO_OK offline mocked Mistral, synthetic data only")


if __name__ == "__main__":
    main()
