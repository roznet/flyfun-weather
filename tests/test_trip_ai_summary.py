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


class TestCharge:
    """The paragraph is billed at the trip model's own token rate.

    It was routed through the per-briefing ``compute_cost``, which adds a
    droplet/subscription share and margin to every call — ~$0.62 a paragraph,
    more than the briefing it summarises.
    """

    @staticmethod
    def _capture(monkeypatch) -> list[dict]:
        import flyfun_common.costs as costs_mod

        calls: list[dict] = []
        monkeypatch.setattr(
            costs_mod, "record_cost", lambda _db, _user_id, **kw: calls.append(kw),
        )
        return calls

    def test_the_trip_model_is_priced(self):
        # Structural: an unpriced trip model would make every charge raise,
        # which ``_charge`` logs and swallows — the cost would go invisible.
        from weatherbrief.costs import token_rates_for
        from weatherbrief.digest.llm_config import load_digest_config

        token_rates_for(load_digest_config().trip.model)

    def test_charged_at_token_cost_only(self, monkeypatch):
        from weatherbrief.digest.trip_summary import _charge

        calls = self._capture(monkeypatch)
        usage = {"model": "claude-haiku-4-5-20251001", "input_tokens": 1500, "output_tokens": 200}
        _charge(None, "u", "t1", "text", usage)

        assert len(calls) == 1
        assert calls[0]["action"] == "trip_summary"
        # 1.5k in at $0.001/1k + 0.2k out at $0.005/1k.
        assert calls[0]["cost"] == pytest.approx(0.0025)
        assert calls[0]["metadata"] == usage

    def test_a_call_that_returned_no_usage_records_nothing(self, monkeypatch):
        from weatherbrief.digest.trip_summary import _charge

        calls = self._capture(monkeypatch)
        _charge(None, "u", "t1", "", {})
        assert calls == []


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
    """The guardrail after #603 round 3: an id equality check, not a parse.

    The leg identity is a *field* now, so every previous test about superlative
    phrasing, clause boundaries and ordered route matching is gone with the
    machinery it exercised — that arms race is what the structured output
    removes. What is left is what genuinely lives in the prose: forbidden
    vocabulary and length.
    """

    def test_a_good_paragraph_naming_the_right_leg_passes(self, summary):
        text = (
            "Friday's EGTF to LSGS looks straightforward at green. "
            "The Sunday LSGS to EGTF return is the difficult one — it is red "
            "six days out, so the picture will still move."
        )
        assert check_guardrail(text, summary, worst_leg_id="b") is None

    def test_naming_a_different_leg_is_rejected(self, summary):
        # Phrasing is irrelevant now: the model declared the wrong leg.
        text = "Everything about this trip reads normally."
        assert check_guardrail(text, summary, worst_leg_id="a") is not None

    def test_an_unknown_leg_id_is_rejected(self, summary):
        assert check_guardrail("Fine.", summary, worst_leg_id="nope") is not None

    def test_an_empty_id_is_rejected_when_a_binding_leg_exists(self, summary):
        assert check_guardrail("Fine.", summary, worst_leg_id="") is not None

    def test_an_empty_id_is_correct_when_no_leg_is_gradeable(self):
        pending = summarize_trip(
            "t3", [_leg("a", ["EGTF", "LSGS"], 60, pending_coverage=True)], now=NOW,
        )
        assert pending.binding_leg_id is None
        assert check_guardrail("No model reaches these dates yet.", pending,
                               worst_leg_id="") is None
        # ...and claiming a leg binds when none does is still wrong.
        assert check_guardrail("Fine.", pending, worst_leg_id="a") is not None

    @pytest.mark.parametrize("phrase", [
        "This is a no-go for Sunday's leg.",
        "I would not fly Sunday's leg.",
        "You should cancel Sunday's leg.",
        "Sunday's leg is unsafe.",
        "We recommend watching Sunday's leg.",
        # Every one of these is a word `trip_v1.md` explicitly forbids, and each
        # slipped through the old compound-only patterns.
        "Avoid Sunday's leg.",
        "Friday's leg looks safe.",
        "Friday's leg is a go.",
        "Friday's leg is good to go.",
        # Bare "go" as a verdict — named in the prompt, and missing from the
        # guard until round 4 despite a commit message claiming otherwise.
        "Friday's leg is go for departure.",
        "Overall: go.",
    ])
    def test_go_no_go_vocabulary_is_rejected(self, summary, phrase):
        assert check_guardrail(phrase, summary, worst_leg_id="b") is not None

    @pytest.mark.parametrize("phrase", [
        "The weather is going to move through the afternoon.",
        "Conditions go from green to amber during the morning.",
        "It goes downhill after midday.",
    ])
    def test_ordinary_uses_of_go_are_not_rejected(self, summary, phrase):
        # The bare-"go" pattern is a verdict check, not a ban on the verb: a
        # lookahead spares "going to", "go from", "goes". Over-rejecting here
        # would silently disable the summary on perfectly descriptive prose.
        text = f"Sunday's LSGS to EGTF is red. {phrase}"
        assert check_guardrail(text, summary, worst_leg_id="b") is None

    def test_vocabulary_is_checked_even_without_a_declared_leg(self, summary):
        assert check_guardrail("Avoid this trip.", summary) is not None

    def test_hedged_prose_is_not_over_rejected(self, summary):
        # The old clause/superlative scan rejected this: "has no problem" put
        # `problem` in a clause naming the non-binding leg.
        text = (
            "Friday's EGTF to LSGS has no problem and looks green. "
            "Sunday's LSGS to EGTF is the difficult one at red."
        )
        assert check_guardrail(text, summary, worst_leg_id="b") is None

    def test_a_local_flight_leg_is_not_a_special_case_any_more(self):
        # A leg whose origin == destination used to need its ICAO code to
        # appear twice for the prose matcher; identity is a field now, so the
        # shape of the route is irrelevant.
        local = summarize_trip(
            "t5",
            [_leg("solo", ["EGTF"], 3, days_out=3, assessment="AMBER")],
            now=NOW,
        )
        assert local.binding_leg_id == "solo"
        assert check_guardrail("The local flight is amber.", local,
                               worst_leg_id="solo") is None

    def test_empty_output_is_rejected(self, summary):
        assert check_guardrail("", summary) == "empty"
        assert check_guardrail("   ", summary) == "empty"

    def test_an_over_long_paragraph_is_rejected(self, summary):
        assert check_guardrail("x" * (MAX_SUMMARY_CHARS + 1), summary) == "too_long"


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
            lambda _s: (
                mod.TripParagraph(
                    worst_leg_id="b",
                    paragraph="You should not fly Sunday's LSGS to EGTF.",
                ),
                {},
            ),
        )
        monkeypatch.setattr(mod, "_charge", lambda *a, **k: None)

        row = self._Row()
        result = ensure_trip_ai_summary(
            None, row, summary, [self._member()], user_id="u",
            leg_inputs=[],
        )
        assert result.unavailable_reason == "guardrail_rejected"
        assert result.text is None
        # The text is not stored — keeping a rejected paragraph would only
        # invite showing it — but the *key* is, so the same inputs are not
        # regenerated and re-charged on every page open.
        assert row.ai_summary_text is None
        assert row.ai_summary_key is not None

    def test_a_clean_paragraph_is_persisted_with_its_key(self, summary, monkeypatch):
        from weatherbrief.digest import trip_summary as mod

        good = (
            "Friday's EGTF to LSGS is green. Sunday's LSGS to EGTF is red at "
            "six days out, with the problem being convective."
        )
        monkeypatch.setattr(mod, "legs_allow_ai", lambda *a, **k: True)
        monkeypatch.setattr(
            mod, "generate",
            lambda _s: (mod.TripParagraph(worst_leg_id="b", paragraph=good), {}),
        )
        monkeypatch.setattr(mod, "_charge", lambda *a, **k: None)

        row = self._Row()
        result = ensure_trip_ai_summary(
            None, row, summary, [self._member()], user_id="u", leg_inputs=[],
        )
        assert result.text == good
        assert row.ai_summary_text == good
        assert row.ai_summary_key

    def test_the_ai_off_gate_runs_before_the_cache(self, summary, monkeypatch):
        """The round-3 Critical: a consent guarantee, not a cache nicety.

        `ai_summary_key` is built from packs and debriefs — `llm_digest_enabled`
        is in neither. So turning AI off on a leg without touching its pack
        leaves the key unchanged, and a gate placed *after* the cache check
        would never run: the stored LLM paragraph would keep being served to a
        pilot who had switched AI off.
        """
        from weatherbrief.digest import trip_summary as mod

        monkeypatch.setattr(mod, "legs_allow_ai", lambda *a, **k: False)
        monkeypatch.setattr(
            mod, "generate", lambda _s: pytest.fail("must not call the model"),
        )

        row = self._Row()
        row.ai_summary_text = "written while AI was still on"
        # The key deliberately MATCHES what the inputs would produce, so this
        # would be a cache hit if the gate were ordered after it.
        from weatherbrief.api.trips import ai_summary_key
        row.ai_summary_key = ai_summary_key([])

        result = ensure_trip_ai_summary(
            None, row, summary, [self._member()], user_id="u", leg_inputs=[],
        )
        assert result.unavailable_reason == "ai_disabled"
        assert result.text is None
        assert row.ai_summary_text is None

    def test_a_rejected_input_set_is_not_regenerated_or_recharged(
        self, summary, monkeypatch,
    ):
        from weatherbrief.digest import trip_summary as mod

        calls: list[int] = []
        monkeypatch.setattr(mod, "legs_allow_ai", lambda *a, **k: True)
        monkeypatch.setattr(mod, "_charge", lambda *a, **k: None)

        def _generate(_s):
            calls.append(1)
            return mod.TripParagraph(worst_leg_id="a", paragraph="Wrong leg."), {}

        monkeypatch.setattr(mod, "generate", _generate)

        row = self._Row()
        members = [self._member()]
        first = ensure_trip_ai_summary(
            None, row, summary, members, user_id="u", leg_inputs=[],
        )
        assert first.unavailable_reason == "guardrail_rejected"

        # Same inputs again: the key was recorded, so no second model call and
        # no second charge — "unchanged inputs never pay twice" holds even for
        # inputs that reliably fail.
        second = ensure_trip_ai_summary(
            None, row, summary, members, user_id="u", leg_inputs=[],
        )
        assert second.text is None
        assert len(calls) == 1

    def test_a_billed_but_empty_call_is_still_charged(self, summary, monkeypatch):
        from weatherbrief.digest import trip_summary as mod

        charged: list[dict] = []
        monkeypatch.setattr(mod, "legs_allow_ai", lambda *a, **k: True)
        monkeypatch.setattr(mod, "generate", lambda _s: (None, {"input_tokens": 400}))
        monkeypatch.setattr(
            mod, "_charge",
            lambda _db, _u, _t, _text, usage: charged.append(usage),
        )

        result = ensure_trip_ai_summary(
            None, self._Row(), summary, [self._member()], user_id="u", leg_inputs=[],
        )
        assert result.unavailable_reason == "generation_failed"
        # An invisible cost line is how a small cost becomes an unexplained one.
        assert charged == [{"input_tokens": 400}]

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
