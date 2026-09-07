"""Tests for the Haiku trip paragraph and its guardrail (#602).

The guardrail exists because the deterministic layer already knows the answer:
the LLM can only ever make that answer *nicer to read*, never different. These
tests pin exactly that.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.digest.trip_summary import (
    MAX_SUMMARY_CHARS,
    build_context,
    check_guardrail,
    ensure_trip_ai_summary,
)
from weatherbrief.trips import TripLegInput, summarize_trip

NOW = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)


def _leg(flight_id, waypoints, days, **kwargs):
    return TripLegInput(
        flight_id=flight_id,
        waypoints=waypoints,
        departure_time=(NOW + timedelta(days=days)).replace(hour=9),
        duration_hours=2.0,
        **kwargs,
    )


@pytest.fixture
def summary():
    return summarize_trip(
        "t1",
        [
            _leg("a", ["EGTF", "LSGS"], 4, days_out=4, assessment="GREEN"),
            _leg("b", ["LSGS", "EGTF"], 6, days_out=6, assessment="RED"),
        ],
        name="Sion weekend",
        now=NOW,
    )


class TestPromptPayload:
    def test_context_carries_only_already_summarized_state(self, summary):
        context = build_context(summary)
        assert "EGTF → LSGS" in context
        assert "assessment=GREEN" in context
        assert "assessment=RED" in context
        assert "days_out=6" in context
        # The binding leg is handed over explicitly — that is what makes the
        # guardrail exact rather than a heuristic.
        assert "Deterministic binding leg" in context
        assert "LSGS → EGTF" in context

    def test_context_says_when_the_picture_is_incomplete(self):
        incomplete = summarize_trip(
            "t2",
            [
                _leg("a", ["EGTF", "LSGS"], 3, days_out=3, assessment="GREEN"),
                _leg("b", ["LSGS", "EGTF"], 40, pending_coverage=True),
            ],
            now=NOW,
        )
        context = build_context(incomplete)
        assert "no model reaches this date yet" in context


class TestGuardrail:
    def test_a_good_paragraph_passes(self, summary):
        text = (
            "Friday's EGTF to LSGS looks straightforward at green. "
            "The Sunday LSGS to EGTF return is the difficult one — it is red "
            "six days out, so the picture will still move."
        )
        assert check_guardrail(text, summary) is None

    @pytest.mark.parametrize("superlative", [
        "is the worst leg of this trip",
        # The prompt itself tells the model to describe "the difficult leg", so
        # this is the phrasing a mislabelled paragraph is most likely to use.
        "is the difficult leg here",
        "is the leg that decides this trip",
        "is the binding leg",
        "is the problem leg",
        "is the limiting leg",
    ])
    def test_naming_a_different_leg_as_the_worst_is_rejected(self, summary, superlative):
        text = f"EGTF to LSGS {superlative}. LSGS to EGTF is fine."
        assert check_guardrail(text, summary) is not None

    def test_a_later_sentence_mislabelling_a_leg_is_still_caught(self, summary):
        # The superlative check must look at *every* sentence naming the leg,
        # not just the first: a lead-in can mention a leg neutrally well before
        # the sentence that actually (wrongly) calls it the difficult one.
        text = (
            "This trip runs EGTF to LSGS and back. "
            "Sunday's LSGS to EGTF is red. "
            "EGTF to LSGS is the difficult leg."
        )
        assert check_guardrail(text, summary) is not None

    def test_mentioning_another_leg_neutrally_is_fine(self, summary):
        text = (
            "Friday's EGTF to LSGS is green and straightforward. "
            "Sunday's LSGS to EGTF is the difficult one, red at six days out."
        )
        assert check_guardrail(text, summary) is None

    def test_a_round_trip_does_not_confuse_its_two_legs(self, summary):
        """The regression the ordered match exists for.

        Both legs of a round trip carry the same two ICAO codes, just reversed.
        An unordered "mentions both codes" test scores every sentence about the
        return as also being about the outbound, so a perfectly correct
        paragraph gets rejected — and, worse, the reverse case slips through.
        """
        text = (
            "Friday's EGTF to LSGS looks fine at green. "
            "Sunday's LSGS to EGTF is the difficult leg, red at six days out."
        )
        assert check_guardrail(text, summary) is None

        # Same two codes, the claim attached to the wrong leg → rejected.
        wrong = (
            "Sunday's LSGS to EGTF looks fine at green. "
            "Friday's EGTF to LSGS is the difficult leg."
        )
        assert check_guardrail(wrong, summary) is not None

    def test_not_mentioning_the_binding_leg_at_all_is_rejected(self, summary):
        text = "Both legs of this trip look broadly similar."
        assert check_guardrail(text, summary) is not None

    @pytest.mark.parametrize("phrase", [
        "This is a no-go for Sunday's LSGS to EGTF.",
        "I would not fly Sunday's LSGS to EGTF.",
        "You should cancel Sunday's LSGS to EGTF.",
        "Sunday's LSGS to EGTF is unsafe.",
        "We recommend watching Sunday's LSGS to EGTF.",
    ])
    def test_go_no_go_vocabulary_is_rejected(self, summary, phrase):
        assert check_guardrail(phrase, summary) is not None

    def test_empty_output_is_rejected(self, summary):
        assert check_guardrail("", summary) == "empty"
        assert check_guardrail("   ", summary) == "empty"

    def test_an_over_long_paragraph_is_rejected(self, summary):
        assert check_guardrail("x" * (MAX_SUMMARY_CHARS + 1), summary) == "too_long"

    def test_no_binding_leg_means_nothing_to_check_against(self):
        # A trip entirely beyond coverage has no computed binding leg; the
        # guardrail then only polices vocabulary.
        pending = summarize_trip(
            "t3", [_leg("a", ["EGTF", "LSGS"], 60, pending_coverage=True)], now=NOW,
        )
        assert pending.binding_leg_id is None
        assert check_guardrail("No model reaches these dates yet.", pending) is None
        assert check_guardrail("This is a no-go.", pending) is not None


class TestGuardrailFallbackWiring:
    """The guardrail exists to *trigger a fallback* — pin that it actually does.

    Checking `check_guardrail` alone leaves the interesting half untested: that
    a rejected paragraph is not persisted and the caller is told why, so the
    page shows the deterministic sentence instead.
    """

    class _Row:
        def __init__(self):
            self.id = "t1"
            self.ai_summary_text = None
            self.ai_summary_key = None
            self.ai_summary_at = None

    def _member(self):
        from datetime import datetime as _dt

        from weatherbrief.models import Flight

        return Flight(
            id="a", user_id="u", route_name="egtf_lsgs", waypoints=["EGTF", "LSGS"],
            departure_time=_dt(2026, 9, 11, 9, tzinfo=timezone.utc),
            created_at=_dt(2026, 9, 1, tzinfo=timezone.utc),
        )

    def test_a_rejected_paragraph_is_not_persisted(self, summary, monkeypatch):
        from weatherbrief.digest import trip_summary as mod

        monkeypatch.setattr(mod, "legs_allow_ai", lambda *a, **k: True)
        monkeypatch.setattr(
            mod, "generate",
            lambda _s: ("You should not fly Sunday's LSGS to EGTF.", {}),
        )
        monkeypatch.setattr(mod, "_charge", lambda *a, **k: None)

        row = self._Row()
        result = ensure_trip_ai_summary(
            None, row, summary, [self._member()], user_id="u",
            leg_inputs=[],
        )
        assert result.unavailable_reason == "guardrail_rejected"
        assert result.text is None
        # Not stored: keeping a rejected paragraph would only invite showing it.
        assert row.ai_summary_text is None
        assert row.ai_summary_key is None

    def test_a_clean_paragraph_is_persisted_with_its_key(self, summary, monkeypatch):
        from weatherbrief.digest import trip_summary as mod

        good = (
            "Friday's EGTF to LSGS is green. Sunday's LSGS to EGTF is red at "
            "six days out, with the problem being convective."
        )
        monkeypatch.setattr(mod, "legs_allow_ai", lambda *a, **k: True)
        monkeypatch.setattr(mod, "generate", lambda _s: (good, {}))
        monkeypatch.setattr(mod, "_charge", lambda *a, **k: None)

        row = self._Row()
        result = ensure_trip_ai_summary(
            None, row, summary, [self._member()], user_id="u", leg_inputs=[],
        )
        assert result.text == good
        assert row.ai_summary_text == good
        assert row.ai_summary_key

    def test_ai_off_on_any_leg_clears_a_stale_paragraph(self, summary, monkeypatch):
        from weatherbrief.digest import trip_summary as mod

        monkeypatch.setattr(mod, "legs_allow_ai", lambda *a, **k: False)
        row = self._Row()
        row.ai_summary_text = "written before the pilot turned AI off"
        row.ai_summary_key = "stale"

        result = ensure_trip_ai_summary(
            None, row, summary, [self._member()], user_id="u", leg_inputs=[],
        )
        assert result.unavailable_reason == "ai_disabled"
        # A leg switched to AI-off must not keep being described by a paragraph
        # written before the switch.
        assert row.ai_summary_text is None
