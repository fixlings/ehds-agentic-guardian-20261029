"""Standalone English "not" is a lexicon negation cue and matches only as a whole token."""

from __future__ import annotations

import json
from pathlib import Path

from guardian.nlp.normalizer import Normalizer

ROOT = Path(__file__).resolve().parents[2]
LEXICON_PATH = ROOT / "guardian" / "contracts" / "lexicon.json"


def _lexicon() -> dict:
    return json.loads(LEXICON_PATH.read_text(encoding="utf-8"))


def test_lexicon_lists_standalone_not() -> None:
    cues = _lexicon()["negation_cues"]
    assert "not" in cues
    assert cues.count("not") == 1
    assert "not including" in cues
    assert "does not" not in cues


def test_standalone_not_excludes_obese_bmi() -> None:
    turn = Normalizer.from_default_lexicon().normalize_message(
        "count of breast cancer where BMI is not obese"
    )
    assert turn.filters.breast_cancer is True
    assert [band.value for band in turn.filters.exclude_bmi_bands] == ["obese"]
    assert turn.filters.bmi_bands == []


def test_does_not_phrase_negates_breast_cancer() -> None:
    turn = Normalizer.from_default_lexicon().normalize_message("women who do not have breast cancer")
    assert turn.filters.sex == "female"
    assert turn.filters.breast_cancer is False


def test_not_inside_another_is_not_negation() -> None:
    turn = Normalizer.from_default_lexicon().normalize_message("another breast cancer count")
    assert turn.filters.breast_cancer is True


def test_removing_standalone_not_cue_misses_negation() -> None:
    lexicon = _lexicon()
    lexicon["negation_cues"] = [cue for cue in lexicon["negation_cues"] if cue != "not"]
    turn = Normalizer(lexicon).normalize_message("count of breast cancer where BMI is not obese")
    assert turn.filters.exclude_bmi_bands == []
    assert [band.value for band in turn.filters.bmi_bands] == ["obese"]
