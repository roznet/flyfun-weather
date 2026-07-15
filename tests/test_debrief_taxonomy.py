"""Tests for the debrief tag taxonomy + keyword matcher."""

from __future__ import annotations

import pytest

from weatherbrief.debriefs.taxonomy import (
    ADVISORY_TAG_MAP,
    OUTCOME_CATEGORIES,
    ConditionTag,
    Decision,
    OutcomeValue,
    build_taxonomy_catalog,
    flagged_tags_from_advisories,
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


class TestServedCatalog:
    def test_catalog_shape(self):
        cat = build_taxonomy_catalog()
        assert [d["id"] for d in cat["decisions"]] == ["flown", "cancelled", "monitoring"]
        # Every tag carries id/label/description + outcome flag.
        ids = {t["id"] for t in cat["tags"]}
        assert ids == {t.value for t in ConditionTag}
        for t in cat["tags"]:
            assert {"id", "label", "description", "outcome_category"} <= set(t)
        # OPS is the only non-outcome category.
        non_outcome = {t["id"] for t in cat["tags"] if not t["outcome_category"]}
        assert non_outcome == {"OPS"}
        assert cat["note_max_length"] == 300

    def test_advisory_map_serialises_tag_values(self):
        cat = build_taxonomy_catalog()
        assert cat["advisory_tag_map"]["icing_escape"] == "ICE"
        # Every mapped value is a real ConditionTag value.
        valid = {t.value for t in ConditionTag}
        assert all(v in valid for v in cat["advisory_tag_map"].values())


class TestFlaggedTags:
    def test_amber_red_flag_their_tag(self):
        flagged = flagged_tags_from_advisories(
            [("icing_escape", "red"), ("turbulence", "amber"), ("vmc_cruise", "green")]
        )
        assert ConditionTag.ICE in flagged
        assert ConditionTag.TURB in flagged
        assert ConditionTag.IMC not in flagged  # green → not flagged

    def test_unknown_advisory_ignored(self):
        assert flagged_tags_from_advisories([("model_quality", "red")]) == []

    def test_result_in_condition_tag_order(self):
        flagged = flagged_tags_from_advisories(
            [("convective", "red"), ("icing_escape", "red")]
        )
        # ICE precedes TS in ConditionTag declaration order.
        assert flagged == [ConditionTag.ICE, ConditionTag.TS]

    def test_every_mapped_id_resolves(self):
        # Guards the map against a typo'd tag.
        for tag in ADVISORY_TAG_MAP.values():
            assert isinstance(tag, ConditionTag)


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
