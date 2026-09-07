"""Tests for the pure trip summary — the binding-leg rule (#602).

The rule is expected to need iteration, so these tests pin the *properties* the
design commits to rather than exact wording: an outlook never competes with a
traffic light, the chain status covers only gradeable remaining legs, and the
page never produces a colour for the trip as a whole.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from weatherbrief.models import AdvisoryChip, AdvisorySummary
from weatherbrief.trips import (
    SORTIE_GAP_HOURS,
    TripLegInput,
    summarize_trip,
)

NOW = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)


def leg(
    flight_id: str,
    waypoints: list[str],
    *,
    days: float,
    hour: int = 9,
    duration: float = 2.0,
    **kwargs,
) -> TripLegInput:
    return TripLegInput(
        flight_id=flight_id,
        waypoints=waypoints,
        departure_time=(NOW + timedelta(days=days)).replace(hour=hour, minute=0),
        duration_hours=duration,
        **kwargs,
    )


class TestChainDerivation:
    def test_order_comes_from_departure_time_not_input_order(self):
        legs = [
            leg("c", ["LFAT", "EGTF"], days=6, hour=13, days_out=6, assessment="GREEN"),
            leg("a", ["EGTF", "LSGS"], days=4, days_out=4, assessment="GREEN"),
            leg("b", ["LSGS", "LFAT"], days=6, hour=10, days_out=6, assessment="GREEN"),
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert [l.flight_id for l in summary.legs] == ["a", "b", "c"]

    def test_chain_label_and_round_trip(self):
        legs = [
            leg("a", ["EGTF", "LSGS"], days=4, days_out=4, assessment="GREEN"),
            leg("b", ["LSGS", "LFAT"], days=6, hour=10, days_out=6, assessment="GREEN"),
            leg("c", ["LFAT", "EGTF"], days=6, hour=13, days_out=6, assessment="GREEN"),
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert summary.chain_label == "EGTF → LSGS → LFAT → EGTF"
        assert summary.is_round_trip is True

    def test_single_leg_trip_is_valid_and_not_a_round_trip(self):
        summary = summarize_trip(
            "t", [leg("a", ["EGTF", "LSGS"], days=3, days_out=3, assessment="GREEN")],
            now=NOW,
        )
        assert summary.total_legs == 1
        assert summary.remaining_legs == 1
        assert summary.is_round_trip is False

    def test_short_gap_is_one_sortie_long_gap_is_not(self):
        legs = [
            leg("a", ["LFQA", "LFAT"], days=3, hour=9, duration=1.0),
            # Lands 10:00, departs 12:00 — a fuel stop, not a decision point.
            leg("b", ["LFAT", "EGTF"], days=3, hour=12, duration=1.0),
            leg("c", ["EGTF", "LFQA"], days=5, hour=9, duration=1.0),
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert summary.legs[1].gap_hours_before == 2.0
        assert summary.legs[1].same_sortie_as_previous is True
        assert summary.legs[2].gap_hours_before > SORTIE_GAP_HOURS
        assert summary.legs[2].same_sortie_as_previous is False

    def test_continuity_break_is_reported_not_blocked(self):
        legs = [
            leg("a", ["EGTF", "LSGS"], days=3, days_out=3, assessment="GREEN"),
            leg("b", ["LFAT", "EGTF"], days=5, days_out=5, assessment="GREEN"),
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert len(summary.continuity_warnings) == 1
        warning = summary.continuity_warnings[0]
        assert (warning.arrives, warning.departs) == ("LSGS", "LFAT")
        # A broken chain is a warning, never a refusal to summarize.
        assert summary.binding_leg_id is not None
        # And the label must not quietly drop the airport the second leg
        # actually departs from — reporting the gap while printing a route
        # string that hides it is worse than either alone.
        assert summary.chain_label == "EGTF → LSGS → LFAT → EGTF"


class TestBindingLeg:
    def test_worst_gradeable_remaining_leg_wins(self):
        legs = [
            leg("a", ["EGTF", "LSGS"], days=4, days_out=4, assessment="GREEN"),
            leg("b", ["LSGS", "LFAT"], days=6, hour=10, days_out=6, assessment="AMBER"),
            leg("c", ["LFAT", "EGTF"], days=6, hour=13, days_out=6, assessment="RED"),
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert summary.binding_leg_id == "c"
        assert summary.binding_basis == "assessment"
        assert summary.chain_status == "RED"

    def test_ties_break_to_the_earliest_departure(self):
        legs = [
            leg("late", ["LSGS", "EGTF"], days=6, days_out=6, assessment="AMBER"),
            leg("early", ["EGTF", "LSGS"], days=4, days_out=4, assessment="AMBER"),
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert summary.binding_leg_id == "early"

    def test_flown_legs_never_bind(self):
        legs = [
            # Behind us and RED — must not decide anything any more.
            leg("flown", ["EGTF", "LSGS"], days=-2, days_out=0, assessment="RED"),
            leg("ahead", ["LSGS", "EGTF"], days=3, days_out=3, assessment="GREEN"),
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert summary.legs[0].state == "flown"
        assert summary.binding_leg_id == "ahead"
        assert summary.chain_status == "GREEN"
        assert summary.remaining_legs == 1

    def test_debrief_cancelled_removes_a_future_leg_from_remaining(self):
        legs = [
            leg("a", ["EGTF", "LSGS"], days=3, days_out=3, assessment="RED",
                debrief_decision="cancelled"),
            leg("b", ["LSGS", "EGTF"], days=5, days_out=5, assessment="GREEN"),
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert summary.legs[0].state == "cancelled"
        assert summary.binding_leg_id == "b"

    def test_unavailable_is_not_a_rung_on_the_ladder(self):
        legs = [
            leg("a", ["EGTF", "LSGS"], days=3, days_out=3, assessment="UNAVAILABLE"),
            leg("b", ["LSGS", "EGTF"], days=5, days_out=5, assessment="AMBER"),
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert summary.binding_leg_id == "b"
        assert summary.chain_status == "AMBER"
        assert summary.unavailable_leg_ids == ["a"]

    def test_all_remaining_legs_unavailable_does_not_claim_they_are_unbriefed(self):
        # "No leg has a briefing yet" would be simply false: these legs WERE
        # briefed, the forecast just came back ungradeable. One says wait for
        # coverage, the other says the data we got could not be assessed.
        legs = [
            leg("a", ["EGTF", "LSGS"], days=3, days_out=3, assessment="UNAVAILABLE"),
            leg("b", ["LSGS", "EGTF"], days=5, days_out=5, assessment="UNAVAILABLE"),
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert summary.binding_leg_id is None
        assert summary.chain_status is None
        assert set(summary.unavailable_leg_ids) == {"a", "b"}
        # Disjoint from *every* other non-gradeable set, not just one of them.
        assert summary.needs_briefing_leg_ids == []
        assert summary.pending_coverage_leg_ids == []
        assert summary.beyond_horizon_leg_ids == []
        assert "no briefing" not in summary.headline.lower()
        assert "could not be assessed" in summary.headline

    def test_a_mix_of_unavailable_and_unbriefed_describes_both(self):
        # The partial fix trapped here: "unavailable AND NOT needs_briefing"
        # let a mixed trip fall through to "no leg has a briefing yet", which
        # is false for the legs that were briefed and came back ungradeable.
        legs = [
            leg("a", ["EGTF", "LSGS"], days=3),  # never briefed
            leg("b", ["LSGS", "LFAT"], days=5, days_out=5, assessment="UNAVAILABLE"),
            leg("c", ["LFAT", "EGTF"], days=6),  # never briefed
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert summary.binding_leg_id is None
        assert summary.unavailable_leg_ids == ["b"]
        assert set(summary.needs_briefing_leg_ids) == {"a", "c"}
        headline = summary.headline.lower()
        # Both facts stated: the ungradeable leg and the never-briefed ones.
        # Asserting on meaning rather than exact wording — the sentence is
        # expected to be reworded, the two claims are not.
        assert "could not be assessed" in headline
        assert "2 legs still need a briefing" in headline


    def test_a_monitoring_leg_never_binds(self):
        # The taxonomy's third debrief value. Treating it as ordinary
        # "remaining" let a flight created purely to watch the weather become
        # the leg that "decides the trip" — and drag chain_status with it.
        legs = [
            leg("watch", ["EGTF", "LSGS"], days=3, days_out=3, assessment="RED",
                debrief_decision="monitoring"),
            leg("real", ["LSGS", "EGTF"], days=5, days_out=5, assessment="GREEN"),
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert summary.legs[0].state == "monitoring"
        assert summary.binding_leg_id == "real"
        assert summary.chain_status == "GREEN"
        assert summary.remaining_legs == 1


class TestTwoAggregationsNeverOne:
    def test_an_outlook_never_competes_with_a_traffic_light(self):
        # This is the failure this design exists to prevent: folding a D-8
        # TRENDING_UNSETTLED into the traffic light would make a green trip
        # read as unsettled, every trip more than four days out amber-or-red,
        # and the signal would die.
        legs = [
            leg("near", ["EGTF", "LSGS"], days=2, days_out=2, assessment="GREEN"),
            leg("far", ["LSGS", "EGTF"], days=12, days_out=12,
                outlook="TRENDING_UNSETTLED"),
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert summary.binding_leg_id == "near"
        assert summary.binding_basis == "assessment"
        assert summary.chain_status == "GREEN"
        # Reported separately, so the page can still surface it.
        assert summary.beyond_horizon_leg_ids == ["far"]

    def test_outlook_binds_only_when_nothing_is_gradeable(self):
        legs = [
            leg("a", ["EGTF", "LSGS"], days=11, days_out=11, outlook="TRENDING_SETTLED"),
            leg("b", ["LSGS", "EGTF"], days=13, days_out=13, outlook="MIXED_SIGNALS"),
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert summary.binding_leg_id == "b"
        assert summary.binding_basis == "outlook"
        # Still no colour — an outlook is a tendency, not a verdict.
        assert summary.chain_status is None
        assert summary.decidable_from is not None
        assert summary.decidable_from < summary.legs[1].departure_time.date()

    def test_pending_coverage_is_a_third_state(self):
        legs = [
            leg("a", ["EGTF", "LSGS"], days=60, pending_coverage=True),
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert summary.pending_coverage_leg_ids == ["a"]
        assert summary.beyond_horizon_leg_ids == []
        assert summary.binding_leg_id is None
        assert summary.chain_status is None

    def test_a_moved_leg_with_no_pack_needs_a_briefing_not_unavailable(self):
        # /move recreates the row and its packs are gone, which is correct for
        # a new date. The summary must say "needs a briefing", never UNAVAILABLE.
        legs = [leg("a", ["EGTF", "LSGS"], days=3)]
        summary = summarize_trip("t", legs, now=NOW)
        assert summary.needs_briefing_leg_ids == ["a"]
        assert summary.legs[0].grade_kind == "needs_briefing"


class TestHeadline:
    def test_headline_names_the_binding_leg_and_never_a_trip_verdict(self):
        legs = [
            leg("a", ["EGTF", "LSGS"], days=4, days_out=4, assessment="GREEN"),
            leg("b", ["LSGS", "EGTF"], days=6, days_out=6, assessment="RED"),
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert "LSGS → EGTF" in summary.headline
        assert "decides this trip" in summary.headline
        # It ranks and directs attention; it does not recommend.
        lowered = summary.headline.lower()
        for banned in ("no-go", "should fly", "recommend", "cancel"):
            assert banned not in lowered

    def test_headline_survives_an_empty_trip(self):
        summary = summarize_trip("t", [], now=NOW)
        assert summary.headline
        assert summary.total_legs == 0

    def test_headline_when_every_leg_is_behind_you(self):
        legs = [leg("a", ["EGTF", "LSGS"], days=-5, days_out=0, assessment="GREEN")]
        summary = summarize_trip("t", legs, now=NOW)
        assert summary.remaining_legs == 0
        assert "behind you" in summary.headline


class TestAdvisoryPassThrough:
    def test_advisory_chips_ride_along_untouched(self):
        chips = AdvisorySummary(
            red=1, amber=2, top=[AdvisoryChip(status="RED", name="Convection")],
        )
        legs = [
            leg("a", ["EGTF", "LSGS"], days=3, days_out=3, assessment="RED",
                advisory_summary=chips),
        ]
        summary = summarize_trip("t", legs, now=NOW)
        assert summary.legs[0].advisory_summary is not None
        assert summary.legs[0].advisory_summary.top[0].name == "Convection"
