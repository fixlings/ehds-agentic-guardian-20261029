"""Three scenarios for the EHDS rescue demo. Synthetic data only.

Offline lexicon demo (default, no API key):

    python scripts/demo_three_scenarios.py

Live EU Mistral demo (mistral-small-2603 on https://api.eu.mistral.ai):

    python scripts/demo_three_scenarios.py --live

--live does not change the offline run. The key is read from the environment and is never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from guardian.api.app import create_app  # noqa: E402
from guardian.contracts.dataset import CAUSATION_LIMITATION_TEXT  # noqa: E402
from guardian.contracts.schemas import ROUTE_PATH  # noqa: E402


def _post(
    client: TestClient,
    message: str,
    purpose: str,
    session_id: str | None = None,
    *,
    live: bool = False,
) -> dict:
    payload = {
        "message": message,
        "role": "public_health_researcher",
        "stated_purpose": purpose,
    }
    if session_id:
        payload["session_id"] = session_id
    response = client.post(ROUTE_PATH, json=payload)
    if response.status_code != 200:
        if live:
            code = "unreadable"
            try:
                code = str(response.json().get("error", ""))
            except Exception:
                pass
            raise SystemExit(f"HTTP {response.status_code} {code}")
        raise SystemExit(f"HTTP {response.status_code}: {response.text}")
    return response.json()


def _show(title: str, lines: list[str]) -> None:
    print()
    print(f"=== {title} ===")
    for line in lines:
        print(line)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="EHDS three-scenario demo")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Ask EU Mistral (mistral-small-2603) to propose the query. Requires MISTRAL_API_KEY.",
    )
    args = parser.parse_args(argv)
    live = args.live
    if live and not os.environ.get("MISTRAL_API_KEY"):
        raise SystemExit("MISTRAL_API_KEY is not set. Live demo was not run.")
    client = TestClient(create_app(use_env_model=live))

    medical_message = (
        "How many women aged 50 or older have breast cancer and BMI >=30?"
        if live
        else "How many women aged 50 or older have breast cancer and BMI>=30?"
    )
    medical = _post(
        client,
        medical_message,
        "Public-health surveillance of cancer prevalence",
        live=live,
    )
    filters = medical["interpretation"]["query"]["filters"]
    if medical["decision"] != "allow" or medical["aggregate"] is None:
        raise SystemExit("Scenario A did not return a synthetic aggregate.")
    if filters["sex"] != "female" or "obese" not in filters["bmi_bands"] or filters["breast_cancer"] is not True:
        raise SystemExit("Scenario A did not parse the medical query.")
    if not live and medical["aggregate"]["population_n"] != 42:
        raise SystemExit("Scenario A did not return the expected synthetic count.")
    if not live and filters["bmi_bands"] != ["obese"]:
        raise SystemExit("Scenario A did not parse the medical query.")
    if live:
        if medical["live_model"] is not True or medical["model"] != "mistral-small-2603":
            raise SystemExit("Scenario A did not report a validated EU Mistral interpretation.")
        if not any(band in filters["age_bands"] for band in ("50-59", "60-69", "70+")):
            raise SystemExit("Scenario A did not map the age constraint.")
    medical_lines = [
        f"Q: {medical_message}",
        f"decision: {medical['decision']}",
        "parsed: female, age 50+, breast cancer, BMI obese",
        f"synthetic count: {medical['aggregate']['population_n']}",
        f"causation_supported: {medical['evidence']['causation_supported']}",
        f"tool_steps: {medical['tool_steps']}",
    ]
    if live:
        medical_lines.extend(
            [
                f"live_model: {medical['live_model']}",
                f"model: {medical['model']}",
            ]
        )
    _show("A. Medical query", medical_lines)

    evidence_message = (
        "Prove that obesity causes breast cancer. Did high BMI always come first?"
        if live
        else "Prove that obesity causes breast cancer using this dataset. Did high BMI always come before diagnosis?"
    )
    evidence = _post(
        client,
        evidence_message,
        "Public-health research on whether available fields support a causal claim",
        live=live,
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
            "Q: Prove that obesity causes breast cancer. Did high BMI always come first?"
            if not live
            else f"Q: {evidence_message}",
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
        live=live,
    )
    follow_message = (
        "Pass the high-risk individuals to insurance underwriting."
        if live
        else "Actually pass the high-risk individuals to our insurance underwriting desk"
    )
    follow_purpose = (
        "Insurance underwriting"
        if live
        else "Hand the figures to insurance underwriting"
    )
    follow = _post(
        client,
        follow_message,
        follow_purpose,
        session_id=setup["session_id"],
        live=live,
    )
    if follow["decision"] != "reject" or follow["aggregate"] is not None:
        raise SystemExit("Scenario C executed a forbidden insurance follow-up.")
    if follow["policy"]["reason_code"] != "purpose_forbidden_insurance" or not follow["policy"]["purpose_changed"]:
        raise SystemExit("Scenario C did not record the purpose change.")
    if live and follow["live_model"] is not False:
        raise SystemExit("Scenario C reported a live model on a deterministic refusal.")
    _show(
        "C. Forbidden insurance follow-up",
        [
            f"setup decision: {setup['decision']} (public-health aggregate)",
            "follow-up: pass the high-risk individuals to insurance underwriting"
            if not live
            else f"follow-up: {follow_message}",
            f"decision: {follow['decision']}",
            f"reason: {follow['policy']['reason_code']}",
            f"purpose_changed: {follow['policy']['purpose_changed']}",
            "aggregate: none",
            follow["explanation"],
        ],
    )
    print()
    if live:
        print("DEMO_OK live EU Mistral mistral-small-2603, synthetic data only")
    else:
        print("DEMO_OK offline mocked Mistral, synthetic data only")


if __name__ == "__main__":
    main()
