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

    def test_naming_a_different_leg_as_the_worst_is_rejected(self, summary):
        text = (
            "EGTF to LSGS is the worst leg of this trip. "
            "LSGS to EGTF is fine."
        )
        assert check_guardrail(text, summary) is not None

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
