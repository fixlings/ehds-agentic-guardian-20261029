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


def test_validated_proposal_replaces_model_display() -> None:
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
                        }
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
    coded = result.query.filters.coded[0]
    assert coded.code == "C50"
    assert coded.display == "Malignant neoplasm of breast"
    assert coded.source_text == "bad boob cancer"
    assert "sql" not in result.query.model_dump()


def test_invented_code_does_not_widen_the_cohort() -> None:
    """An unlisted code must not fall through to a broader permitted filter such as sex."""
    client = _Proposal(
        {
            "proposed_purpose": "public_health_research",
            "needs_clarification": False,
            "query": {
                "measures": ["count"],
                "filters": {
                    "sex": "female",
                    "coded": [
                        {
                            "system": "ICD-10",
                            "code": "XYZ999",
                            "display": "supercancer",
                            "source_text": "supercancer",
                            "negated": False,
                        }
                    ],
                },
            },
        }
    )
    interpreter = Interpreter(client=client, propose_query=True)
    result = interpreter.interpret("women with supercancer", _session())
    assert interpreter.validated_live_response is False
    assert result.needs_clarification is True
    assert result.query is None
    assert "XYZ999" not in result.model_dump_json()


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


def test_flag_merge_model_error_is_not_a_successful_interpretation() -> None:
    interpreter = Interpreter(client=_Proposal(RuntimeError("timeout")), propose_query=False)
    try:
        interpreter.interpret("How many women have breast cancer?", _session())
    except ModelCallError as exc:
        assert str(exc) == "EU Mistral did not return JSON"
        assert interpreter.validated_live_response is False
        return
    raise AssertionError("a failed flag call must not return the lexicon interpretation")


def test_scenario_a_near_miss_json_validates() -> None:
    """A hedged Scenario A proposal used to become query=None before schema validation."""
    client = _Proposal(
        {
            "proposed_purpose": "public_health_research",
            "needs_clarification": True,
            "query": {
                "measures": "count",
                "explanation": "women 50+ with breast cancer and high BMI",
                "filters": {
                    "sex": "female",
                    "age_bands": ["50+"],
                    "bmi_bands": ["BMI >=30"],
                    "coded": [{"system": "ICD10", "code": "C50"}],
                },
            },
        }
    )
    interpreter = Interpreter(client=client, propose_query=True)
    result = interpreter.interpret(
        "How many women aged 50 or older have breast cancer and BMI >=30?",
        _session(),
    )
    assert interpreter.validated_live_response is True
    assert result.needs_clarification is False
    assert result.query is not None
    filters = result.query.filters
    assert filters.sex == "female"
    assert [band.value for band in filters.age_bands] == ["50-59", "60-69", "70+"]
    assert [band.value for band in filters.bmi_bands] == ["obese"]
    assert filters.breast_cancer is True
    coded = filters.coded[0]
    assert coded.code == "C50"
    assert coded.system.value == "ICD-10"
    assert coded.display == "Malignant neoplasm of breast"
    assert "validation_category:ok" in result.notes
    assert "XYZ999" not in result.model_dump_json()


def test_age_band_prose_validates_scenario_a() -> None:
    """The prompt's age sentence is not an age-band enum and used to be query_schema."""
    client = _Proposal(
        {
            "proposed_purpose": "public_health_research",
            "needs_clarification": True,
            "query": {
                "measures": ["count"],
                "group_by": [],
                "filters": {
                    "sex": "female",
                    "age_bands": "50-59, 60-69, and 70+",
                    "bmi_bands": ["obese"],
                    "breast_cancer": True,
                    "coded": [
                        {
                            "system": "ICD-10",
                            "code": "C50",
                            "display": "Malignant neoplasm of breast",
                            "source_text": "breast cancer",
                            "negated": False,
                        }
                    ],
                },
            },
        }
    )
    interpreter = Interpreter(client=client, propose_query=True)
    result = interpreter.interpret(
        "How many women aged 50 or older have breast cancer and BMI >=30?",
        _session(),
    )
    assert interpreter.validated_live_response is True
    assert result.query is not None
    filters = result.query.filters
    assert [item.value for item in result.query.measures] == ["count"]
    assert filters.sex == "female"
    assert [band.value for band in filters.age_bands] == ["50-59", "60-69", "70+"]
    assert [band.value for band in filters.bmi_bands] == ["obese"]
    assert filters.breast_cancer is True
    assert "validation_category:ok" in result.notes

    prose = _Proposal(
        {
            "proposed_purpose": "public_health_research",
            "query": {
                "measures": "count",
                "filters": {
                    "sex": ["female"],
                    "age_bands": ["50-59", "60-69", "and 70+"],
                    "bmi_bands": "obese only",
                    "breast_cancer": "breast cancer",
                    "coded": [{"system": "ICD-10", "code": "C50"}],
                },
            },
        }
    )
    second = Interpreter(client=prose, propose_query=True).interpret(
        "How many women aged 50 or older have breast cancer and BMI >=30?",
        _session(),
    )
    assert second.query is not None
    assert second.query.filters.sex == "female"
    assert [band.value for band in second.query.filters.age_bands] == ["50-59", "60-69", "70+"]
    assert [band.value for band in second.query.filters.bmi_bands] == ["obese"]
    assert second.query.filters.breast_cancer is True
    assert [item.value for item in second.query.measures] == ["count"]


def test_rejected_proposal_names_the_field() -> None:
    invented = _Proposal(
        {
            "proposed_purpose": "public_health_research",
            "query": {
                "measures": ["count"],
                "filters": {
                    "sex": "female",
                    "coded": [
                        {
                            "system": "ICD-10",
                            "code": "XYZ999",
                            "display": "supercancer",
                            "source_text": "supercancer",
                            "negated": False,
                        }
                    ],
                },
            },
        }
    )
    unexpected = _Proposal(
        {
            "proposed_purpose": "public_health_research",
            "query": {
                "measures": ["count"],
                "filters": {"sex": "female", "breast_cancer": True, "age_min": 50},
            },
        }
    )
    sql = _Proposal(
        {
            "proposed_purpose": "public_health_research",
            "query": {
                "sql": "select * from patients",
                "measures": ["count"],
                "filters": {"sex": "female", "breast_cancer": True},
            },
        }
    )
    message = "How many women have breast cancer?"
    invented_result = Interpreter(client=invented, propose_query=True).interpret(message, _session())
    unexpected_result = Interpreter(client=unexpected, propose_query=True).interpret(message, _session())
    sql_result = Interpreter(client=sql, propose_query=True).interpret(message, _session())
    diagnosis = _Proposal(
        {
            "proposed_purpose": "public_health_research",
            "query": {
                "measures": ["count"],
                "filters": {"sex": "female", "breast_cancer": "supercancer"},
            },
        }
    )
    diagnosis_result = Interpreter(client=diagnosis, propose_query=True).interpret(message, _session())
    assert invented_result.query is None
    assert unexpected_result.query is None
    assert sql_result.query is None
    assert diagnosis_result.query is None
    assert "validation_category:unlisted_code.filters.coded: expected allowlisted code" in invented_result.notes
    assert (
        "validation_category:unexpected_field.filters.age_min: expected no extra field"
        in unexpected_result.notes
    )
    assert "validation_category:forbidden_field.query.sql: expected no executable field" in sql_result.notes
    assert (
        "validation_category:query_schema.filters.breast_cancer: expected boolean"
        in diagnosis_result.notes
    )
    assert "XYZ999" not in invented_result.model_dump_json()
    assert "select * from patients" not in sql_result.model_dump_json()


def test_live_response_format_is_strict_query_schema() -> None:
    from mistralai.client.models.responseformat import ResponseFormat

    from guardian.nlp.interpreter import _live_response_format

    fmt = _live_response_format()
    ResponseFormat.model_validate(fmt)
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    age = fmt["json_schema"]["schema"]["properties"]["query"]["properties"]["filters"]["properties"]["age_bands"]
    assert age["type"] == "array"
    assert age["items"]["enum"] == ["18-39", "40-49", "50-59", "60-69", "70+"]
    breast = fmt["json_schema"]["schema"]["properties"]["query"]["properties"]["filters"]["properties"]["breast_cancer"]
    assert breast["type"] == "boolean"


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
