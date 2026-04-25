"""Tests for the debrief stats aggregator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.debriefs.stats import compute_stats
from weatherbrief.debriefs.taxonomy import ConditionTag, Decision, OutcomeValue
from weatherbrief.models import Flight, FlightDebrief


NOW = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)


def _flight(idx: int, days_ago: int) -> Flight:
    dep = NOW - timedelta(days=days_ago)
    return Flight(
        id=f"flt-{idx}",
        user_id="u1",
        route_name=f"r{idx}",
        departure_time=dep,
        cruise_altitude_ft=8000,
        flight_duration_hours=1.0,
        created_at=dep - timedelta(days=1),
    )


def _debrief(
    flight: Flight,
    decision: Decision,
    *,
    reasons=None,
    outcomes=None,
) -> FlightDebrief:
    return FlightDebrief(
        flight_id=flight.id,
        decision=decision,
        reasons=reasons or [],
        outcomes=outcomes or {},
        created_at=flight.departure_time + timedelta(hours=2),
        updated_at=flight.departure_time + timedelta(hours=2),
    )


class TestStatsBasics:
    def test_empty(self):
        s = compute_stats([], [], now=NOW)
        assert s.flown_count == 0
        assert s.cancelled_count == 0
        assert s.pending_debrief_count == 0
        assert s.total_flights_in_window == 0

    def test_future_flights_excluded(self):
        future = Flight(
            id="future",
            user_id="u1",
            route_name="r",
            departure_time=NOW + timedelta(days=1),
            cruise_altitude_ft=8000,
            flight_duration_hours=1.0,
            created_at=NOW,
        )
        s = compute_stats([future], [], now=NOW)
        assert s.total_flights_in_window == 0

    def test_window_filter(self):
        old = _flight(1, days_ago=200)  # outside default 90d window
        recent = _flight(2, days_ago=10)
        s = compute_stats([old, recent], [], now=NOW)
        assert s.total_flights_in_window == 1

    def test_pending_count(self):
        f1 = _flight(1, days_ago=10)
        f2 = _flight(2, days_ago=20)
        s = compute_stats([f1, f2], [], now=NOW)
        assert s.pending_debrief_count == 2


class TestCancellationStats:
    def test_cancellation_reasons_aggregate(self):
        f1 = _flight(1, days_ago=5)
        f2 = _flight(2, days_ago=10)
        f3 = _flight(3, days_ago=15)
        debriefs = [
            _debrief(f1, Decision.CANCELLED, reasons=[ConditionTag.IMC, ConditionTag.WIND]),
            _debrief(f2, Decision.CANCELLED, reasons=[ConditionTag.IMC]),
            _debrief(f3, Decision.CANCELLED, reasons=[ConditionTag.OPS]),
        ]
        s = compute_stats([f1, f2, f3], debriefs, now=NOW)
        assert s.cancelled_count == 3
        assert s.cancellation_reasons[ConditionTag.IMC] == 2
        assert s.cancellation_reasons[ConditionTag.WIND] == 1
        assert s.cancellation_reasons[ConditionTag.OPS] == 1

    def test_ops_excluded_from_accuracy(self):
        # OPS-only cancellation must not influence category_accuracy.
        f = _flight(1, days_ago=5)
        d = _debrief(f, Decision.CANCELLED, reasons=[ConditionTag.OPS])
        s = compute_stats([f], [d], now=NOW)
        assert s.category_accuracy == {}


class TestMonitoringStats:
    def test_monitoring_counted_separately(self):
        f1 = _flight(1, days_ago=5)
        f2 = _flight(2, days_ago=10)
        debriefs = [
            _debrief(f1, Decision.MONITORING),
            _debrief(f2, Decision.FLOWN),
        ]
        s = compute_stats([f1, f2], debriefs, now=NOW)
        assert s.monitoring_count == 1
        assert s.flown_count == 1
        assert s.cancelled_count == 0
        assert s.pending_debrief_count == 0

    def test_monitoring_excluded_from_reasons_and_accuracy(self):
        f = _flight(1, days_ago=5)
        # Stats input must come from validated FlightDebrief models, but a
        # belt-and-braces test on the aggregator: even if a monitoring
        # debrief somehow had reasons or outcomes, the aggregator wouldn't
        # touch them. Test by passing a flown alongside.
        debriefs = [_debrief(f, Decision.MONITORING)]
        s = compute_stats([f], debriefs, now=NOW)
        assert s.cancellation_reasons == {}
        assert s.category_accuracy == {}

    def test_monitoring_does_not_count_as_pending(self):
        # Three flights — one monitoring, two pending → pending=2 not 3.
        f1, f2, f3 = _flight(1, 1), _flight(2, 2), _flight(3, 3)
        debriefs = [_debrief(f1, Decision.MONITORING)]
        s = compute_stats([f1, f2, f3], debriefs, now=NOW)
        assert s.monitoring_count == 1
        assert s.pending_debrief_count == 2


class TestCategoryAccuracy:
    def test_basic_aggregation(self):
        f1, f2, f3 = _flight(1, 5), _flight(2, 10), _flight(3, 20)
        debriefs = [
            _debrief(f1, Decision.FLOWN, outcomes={
                ConditionTag.ICE: OutcomeValue.CONSISTENT,
                ConditionTag.IMC: OutcomeValue.WORSE,
            }),
            _debrief(f2, Decision.FLOWN, outcomes={
                ConditionTag.ICE: OutcomeValue.WORSE,
            }),
            _debrief(f3, Decision.FLOWN, outcomes={
                ConditionTag.ICE: OutcomeValue.BETTER,
            }),
        ]
        s = compute_stats([f1, f2, f3], debriefs, now=NOW)
        ice = s.category_accuracy[ConditionTag.ICE]
        assert ice.queried_count == 3
        assert ice.consistent == 1
        assert ice.better == 1
        assert ice.worse == 1

        imc = s.category_accuracy[ConditionTag.IMC]
        assert imc.queried_count == 1
        assert imc.worse == 1

    def test_unqueried_categories_pruned(self):
        # If no flight queries TURB, it shouldn't appear in the dict.
        f = _flight(1, 5)
        d = _debrief(f, Decision.FLOWN, outcomes={ConditionTag.ICE: OutcomeValue.CONSISTENT})
        s = compute_stats([f], [d], now=NOW)
        assert ConditionTag.ICE in s.category_accuracy
        assert ConditionTag.TURB not in s.category_accuracy


class TestWindowSelector:
    def test_custom_window_30d(self):
        old = _flight(1, days_ago=60)  # outside 30d
        recent = _flight(2, days_ago=10)
        s = compute_stats([old, recent], [], window_days=30, now=NOW)
        assert s.total_flights_in_window == 1
        assert s.window_days == 30

    def test_window_365d(self):
        flights = [_flight(i, days_ago=300) for i in range(3)]
        s = compute_stats(flights, [], window_days=365, now=NOW)
        assert s.total_flights_in_window == 3
