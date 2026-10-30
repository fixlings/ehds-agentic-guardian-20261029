"""NLP interpreter tests (offline fake client + optional live EU call)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from guardian.contracts.schemas import PurposeCategory, SessionContext, TurnRecord
from guardian.contracts.budget import DEFAULT_MODEL, MISTRAL_EU_BASE_URL
from guardian.nlp.interpreter import Interpreter
from guardian.nlp.mistral_client import MistralEuClient

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = json.loads((ROOT / "tests" / "acceptance" / "prompts.json").read_text(encoding="utf-8"))
PROMPTS_BY_ID = {p["id"]: p for p in PROMPTS}

NLP_PROMPT_IDS = [
    "P01",
    "P02",
    "P03",
    "P04",
    "P05",
    "P08",
    "P09",
    "P10",
    "P11",
    "P12",
    "P21",
    "P22",
]


class FakeChatClient:
    def list_models(self) -> list[str]:
        return ["mistral-small-2603"]

    def complete_json(self, **kwargs: object) -> dict:
        return {"proposed_purpose": "public_health_research"}


def _session_from_turns(turn_defs: list[dict], prior_records: list[TurnRecord]) -> SessionContext:
    last = turn_defs[len(prior_records) - 1] if prior_records else turn_defs[0]
    return SessionContext(
        session_id="test-session",
        role=last["role"],
        stated_purpose=last["stated_purpose"],
        purpose_category=PurposeCategory.unknown,
        turns=prior_records,
    )


def _assert_expect(interpretation, expect: dict) -> None:
    if "needs_clarification" in expect:
        assert interpretation.needs_clarification is expect["needs_clarification"]
    if expect.get("needs_clarification"):
        assert interpretation.query is None

    if "unresolved_substrings" in expect:
        unresolved_fold = " ".join(t.casefold() for t in interpretation.unresolved_terms)
        for sub in expect["unresolved_substrings"]:
            assert sub.casefold() in unresolved_fold, f"missing unresolved {sub!r} in {interpretation.unresolved_terms}"

    if "forbidden_codes" in expect and interpretation.query is not None:
        codes = {c.code for c in interpretation.query.filters.coded}
        for code in expect["forbidden_codes"]:
            assert code not in codes

    if interpretation.query is None:
        return

    q = interpretation.query
    if "sex" in expect:
        assert q.filters.sex == expect["sex"]
    if "breast_cancer" in expect:
        assert q.filters.breast_cancer is expect["breast_cancer"]
    if "age_bands" in expect:
        assert [b.value for b in q.filters.age_bands] == expect["age_bands"]
    if "bmi_bands" in expect:
        assert [b.value for b in q.filters.bmi_bands] == expect["bmi_bands"]
    if "exclude_bmi_bands" in expect:
        assert [b.value for b in q.filters.exclude_bmi_bands] == expect["exclude_bmi_bands"]
    if "regions" in expect:
        assert [r.value for r in q.filters.regions] == expect["regions"]
    if "measures_include" in expect:
        assert any(m.value == expect["measures_include"] for m in q.measures)
    if "group_by_include" in expect:
        assert any(g.value == expect["group_by_include"] for g in q.group_by)
    if "user_claim_set" in expect and expect["user_claim_set"]:
        assert q.user_claim is not None and len(q.user_claim) > 0
    if "coded" in expect:
        for expected in expect["coded"]:
            match = next(
                (
                    c
                    for c in q.filters.coded
                    if c.system.value == expected["system"]
                    and c.code == expected["code"]
                    and c.negated == expected["negated"]
                ),
                None,
            )
            assert match is not None, f"missing coded filter {expected}"


@pytest.fixture
def interpreter() -> Interpreter:
    return Interpreter(client=FakeChatClient())


@pytest.mark.parametrize("prompt_id", NLP_PROMPT_IDS)
def test_acceptance_prompt_interpretation(interpreter: Interpreter, prompt_id: str) -> None:
    item = PROMPTS_BY_ID[prompt_id]
    turns = item["turns"]
    message = turns[0]["message"]
    session = _session_from_turns(turns, [])
    result = interpreter.interpret(message, session)
    _assert_expect(result, item["expect"])


def test_p06_follow_up_women(interpreter: Interpreter) -> None:
    item = PROMPTS_BY_ID["P06"]
    turn1 = item["turns"][0]
    session = SessionContext(
        session_id="p06",
        role=turn1["role"],
        stated_purpose=turn1["stated_purpose"],
        turns=[],
    )
    first = interpreter.interpret(turn1["message"], session)
    assert first.query is not None
    session.turns.append(
        TurnRecord(
            message=turn1["message"],
            decision="allow",
            purpose_category=PurposeCategory.public_health_research,
            query=first.query,
        )
    )
    turn2 = item["turns"][1]
    second = interpreter.interpret(turn2["message"], session)
    _assert_expect(second, item["expect"])


def test_p07_follow_up_wien(interpreter: Interpreter) -> None:
    item = PROMPTS_BY_ID["P07"]
    turn1 = item["turns"][0]
    session = SessionContext(
        session_id="p07",
        role=turn1["role"],
        stated_purpose=turn1["stated_purpose"],
        turns=[],
    )
    first = interpreter.interpret(turn1["message"], session)
    assert first.query is not None
    session.turns.append(
        TurnRecord(
            message=turn1["message"],
            decision="allow",
            purpose_category=PurposeCategory.public_health_research,
            query=first.query,
        )
    )
    turn2 = item["turns"][1]
    second = interpreter.interpret(turn2["message"], session)
    _assert_expect(second, item["expect"])


def test_interpreter_has_no_sql_in_query(interpreter: Interpreter) -> None:
    item = PROMPTS_BY_ID["P01"]
    turn = item["turns"][0]
    session = _session_from_turns([turn], [])
    result = interpreter.interpret(turn["message"], session)
    assert result.query is not None
    dumped = result.query.model_dump()
    assert "sql" not in dumped


def test_mistral_client_rejects_non_eu_host() -> None:
    with pytest.raises(ValueError, match="EU host"):
        MistralEuClient(api_key="not-a-real-credential", base_url="https://api.mistral.ai")
    assert MISTRAL_EU_BASE_URL == "https://api.eu.mistral.ai"


def _fail_live(exc: BaseException, api_key: str) -> None:
    if api_key and (api_key in str(exc) or api_key in repr(exc)):
        pytest.fail("Live EU Mistral call failed. Credential redacted from this message.")
    pytest.fail(f"Live EU Mistral call failed ({type(exc).__name__}).")


@pytest.mark.live
def test_mistral_eu_live_json_completion() -> None:
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        pytest.skip("MISTRAL_API_KEY not set. Live EU Mistral test was not executed.")

    try:
        client = MistralEuClient(api_key=api_key)
    except Exception as exc:
        _fail_live(exc, api_key)
    try:
        models = client.list_models()
    except Exception as exc:
        _fail_live(exc, api_key)
    if DEFAULT_MODEL not in models:
        pytest.fail("mistral-small-2603 was not listed by the EU host")
    start = time.perf_counter()
    try:
        payload = client.complete_json(
            model=DEFAULT_MODEL,
            system='Respond with JSON: {"ok": true}',
            user="ping",
            timeout_s=20.0,
        )
    except Exception as exc:
        _fail_live(exc, api_key)
    latency_ms = int((time.perf_counter() - start) * 1000)
    assert isinstance(payload, dict)
    assert MISTRAL_EU_BASE_URL == "https://api.eu.mistral.ai"
    note = client.model_substitution_note or f"model={DEFAULT_MODEL}"
    pytest.live_result = {"latency_ms": latency_ms, "model": DEFAULT_MODEL, "note": note}  # type: ignore[attr-defined]
