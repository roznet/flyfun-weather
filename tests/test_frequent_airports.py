"""Tests for the frequent-airports ranking (#419, B3).

``compute_frequent_airports`` derives the user's most-used departure and
destination airports from flight history (``FlightRow.waypoints_json`` first /
last waypoint), since there is no ``home_base`` column in the schema.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from flyfun_common.db import DEV_USER_ID
from weatherbrief.api.flights import compute_frequent_airports
from weatherbrief.models import Flight
from weatherbrief.storage.flights import save_flight


def _flight(fid: str, waypoints: list[str], user_id: str = DEV_USER_ID) -> Flight:
    return Flight(
        id=fid,
        user_id=user_id,
        route_name=fid,
        departure_time=datetime(2026, 2, 21, 9, tzinfo=timezone.utc),
        waypoints=waypoints,
        created_at=datetime(2026, 2, 14, 10, tzinfo=timezone.utc),
    )


class TestComputeFrequentAirports:
    def test_empty_when_no_flights(self, db_session, dev_user):
        result = compute_frequent_airports(db_session, dev_user)
        assert result.departures == []
        assert result.destinations == []

    def test_counts_and_ranks_ends(self, db_session, dev_user):
        # LFAT departs 3×, EGKA departs 1×; EGKA arrives 2×, LFAT arrives 1×.
        save_flight(db_session, _flight("f1", ["LFAT", "LFRK", "EGKA"]), dev_user)
        save_flight(db_session, _flight("f2", ["LFAT", "EGKA"]), dev_user)
        save_flight(db_session, _flight("f3", ["LFAT", "EGLL"]), dev_user)
        save_flight(db_session, _flight("f4", ["EGKA", "LFAT"]), dev_user)

        result = compute_frequent_airports(db_session, dev_user)

        assert result.departures[0].icao == "LFAT"
        assert result.departures[0].count == 3
        dep = {a.icao: a.count for a in result.departures}
        assert dep == {"LFAT": 3, "EGKA": 1}
        dest = {a.icao: a.count for a in result.destinations}
        assert dest == {"EGKA": 2, "EGLL": 1, "LFAT": 1}

    def test_skips_non_icao_waypoints_at_the_ends(self, db_session, dev_user):
        # A route that starts/ends on a navaid/fix (not a 4-letter ICAO) is not
        # counted as an airport departure/destination.
        save_flight(db_session, _flight("f1", ["KONAN", "EGKA"]), dev_user)  # dep = navaid
        save_flight(db_session, _flight("f2", ["LFAT", "REVTU"]), dev_user)  # dest = fix

        result = compute_frequent_airports(db_session, dev_user)
        assert {a.icao for a in result.departures} == {"LFAT"}
        assert {a.icao for a in result.destinations} == {"EGKA"}

    def test_single_waypoint_counts_departure_only(self, db_session, dev_user):
        save_flight(db_session, _flight("f1", ["LFAT"]), dev_user)
        result = compute_frequent_airports(db_session, dev_user)
        assert {a.icao for a in result.departures} == {"LFAT"}
        assert result.destinations == []

    def test_top_n_limit(self, db_session, dev_user):
        for i in range(7):
            save_flight(db_session, _flight(f"f{i}", [f"LFA{chr(65 + i)}", "EGKA"]), dev_user)
        result = compute_frequent_airports(db_session, dev_user, top_n=5)
        assert len(result.departures) == 5

    def test_scoped_to_the_user(self, db_session, dev_user):
        from flyfun_common.db.models import UserRow

        other = "other-user"
        db_session.add(UserRow(
            id=other, provider="local", provider_sub="other",
            email="other@localhost", display_name="Other", approved=True,
        ))
        db_session.flush()

        save_flight(db_session, _flight("mine", ["LFAT", "EGKA"]), dev_user)
        save_flight(db_session, _flight("theirs", ["EGLL", "EGKB"], user_id=other), other)

        result = compute_frequent_airports(db_session, dev_user)
        assert {a.icao for a in result.departures} == {"LFAT"}
        assert {a.icao for a in result.destinations} == {"EGKA"}
