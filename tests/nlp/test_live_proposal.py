"""Live proposal validation. No network. Offline flag-merge behavior is not used here."""

from __future__ import annotations

from guardian.contracts.budget import DEFAULT_MODEL
from guardian.contracts.schemas import PurposeCategory, RequesterRole, SessionContext
from guardian.nlp.interpreter import Interpreter, ModelCallError


def _session() -> SessionContext:
    return SessionContext(
        session_id="live-proposal",
        role=RequesterRole.public_health_researcher,
        stated_purpose="Public-health surveillance of cancer prevalence",
    )


class _Proposal:
    def __init__(self, payload: dict | Exception) -> None:
        self.payload = payload
        self.models: list[str] = []

    def complete_json(self, **kwargs: object) -> dict:
        self.models.append(str(kwargs.get("model")))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_validated_proposal_replaces_model_display_and_drops_invented_code() -> None:
    client = _Proposal(
        {
            "proposed_purpose": "public_health_research",
            "injection_suspected": False,
            "identity_disclosure_suspected": False,
            "purpose_change_suspected": False,
            "needs_clarification": False,
            "query": {
                "measures": ["count"],
                "filters": {
                    "sex": "female",
                    "age_bands": ["50-59", "60-69", "70+"],
                    "bmi_bands": ["obese"],
                    "breast_cancer": True,
                    "coded": [
                        {
                            "system": "ICD-10",
                            "code": "C50",
                            "display": "bad boob cancer",
                            "source_text": "bad boob cancer",
                            "negated": False,
                        },
                        {
                            "system": "ICD-10",
                            "code": "XYZ999",
                            "display": "invented",
                            "source_text": "supercancer",
                            "negated": False,
                        },
                    ],
                },
            },
        }
    )
    interpreter = Interpreter(client=client, propose_query=True)
    result = interpreter.interpret(
        "how many ladies over fifty with the bad boob cancer are really heavy",
        _session(),
    )
    assert interpreter.validated_live_response is True
    assert client.models == [DEFAULT_MODEL]
    assert result.query is not None
    assert result.proposed_purpose == PurposeCategory.public_health_research
    codes = {(item.code, item.display, item.source_text) for item in result.query.filters.coded}
    assert ("C50", "Malignant neoplasm of breast", "bad boob cancer") in codes
    assert "XYZ999" not in {item.code for item in result.query.filters.coded}
    assert "sql" not in result.query.model_dump()


def test_sql_proposal_is_a_safe_refusal() -> None:
    client = _Proposal(
        {
            "proposed_purpose": "public_health_research",
            "needs_clarification": False,
            "query": {
                "sql": "select * from patients",
                "measures": ["count"],
                "filters": {"sex": "female", "breast_cancer": True},
            },
        }
    )
    interpreter = Interpreter(client=client, propose_query=True)
    result = interpreter.interpret("How many women have breast cancer?", _session())
    assert interpreter.validated_live_response is False
    assert result.needs_clarification is True
    assert result.query is None
    assert "select * from patients" not in result.model_dump_json()


def test_model_exception_is_not_swallowed() -> None:
    interpreter = Interpreter(
        client=_Proposal(RuntimeError("SUPER_SECRET_KEY_SENTINEL timeout")),
        propose_query=True,
    )
    try:
        interpreter.interpret("How many women have breast cancer?", _session())
    except ModelCallError as exc:
        assert "SUPER_SECRET_KEY_SENTINEL" not in str(exc)
        assert interpreter.validated_live_response is False
        return
    raise AssertionError("model failure must not become a normal interpretation")
