"""Tests for the debrief tag taxonomy + keyword matcher."""

from __future__ import annotations

import pytest

from weatherbrief.debriefs.taxonomy import (
    OUTCOME_CATEGORIES,
    ConditionTag,
    Decision,
    OutcomeValue,
    match_tags_in_text,
)


class TestEnums:
    def test_decision_values(self):
        assert Decision.CANCELLED.value == "cancelled"
        assert Decision.FLOWN.value == "flown"

    def test_outcome_values(self):
        assert {v.value for v in OutcomeValue} == {"consistent", "better", "worse"}

    def test_outcome_categories_excludes_ops(self):
        assert ConditionTag.OPS not in OUTCOME_CATEGORIES
        # All other tags are present
        for t in ConditionTag:
            if t is not ConditionTag.OPS:
                assert t in OUTCOME_CATEGORIES


class TestKeywordMatching:
    def test_empty_text(self):
        assert match_tags_in_text("") == set()
        assert match_tags_in_text(None) == set()  # type: ignore[arg-type]

    def test_single_keyword(self):
        assert ConditionTag.ICE in match_tags_in_text("icing forecast at FL080")

    def test_multiple_keywords(self):
        tags = match_tags_in_text("icy and gusty crosswind, fog at destination")
        assert ConditionTag.WIND in tags
        assert ConditionTag.VIS in tags

    def test_case_insensitive(self):
        assert ConditionTag.IMC in match_tags_in_text("IMC FORECAST")
        assert ConditionTag.IMC in match_tags_in_text("imc forecast")

    def test_word_boundary(self):
        # "ice" matches but "thrice" should NOT (word-boundary check)
        assert ConditionTag.ICE in match_tags_in_text("ice on wings")
        assert ConditionTag.ICE not in match_tags_in_text("thrice over")

    def test_multi_word_phrase(self):
        assert ConditionTag.FRZ in match_tags_in_text("freezing rain reported")
        assert ConditionTag.IMC in match_tags_in_text("low ceiling all day")

    def test_no_match(self):
        assert match_tags_in_text("calm sunny CAVOK") == set()

    def test_ops_keywords(self):
        assert ConditionTag.OPS in match_tags_in_text("aircraft maintenance issue")
        assert ConditionTag.OPS in match_tags_in_text("notam restricted")

    @pytest.mark.parametrize("text,expected", [
        ("thunderstorm activity", ConditionTag.TS),
        ("turbulence at FL100", ConditionTag.TURB),
        ("rough air", ConditionTag.TURB),
        ("very gusty", ConditionTag.WIND),
        ("dense fog", ConditionTag.VIS),
        ("rime icing", ConditionTag.ICE),
    ])
    def test_phrases(self, text, expected):
        assert expected in match_tags_in_text(text)
