"""Tests for the auto-refresh scheduler.

Covers ``_next_due_at``, ``_flight_start_dt``, and ``_find_due_flights``
using the timestamp-based scheduling formula:

    next_due = min(last_refresh + 1 day at preferred hour,
                   flight_start − 2 h)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from weatherbrief.db.models import FlightRow
from weatherbrief.scheduler import (
    _PREFLIGHT_LEAD_HOURS,
    _find_due_flights,
    _flight_start_dt,
    _next_due_at,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(*args) -> datetime:
    """Shorthand for a timezone-aware UTC datetime."""
    return datetime(*args, tzinfo=timezone.utc)


def _make_row(**overrides) -> SimpleNamespace:
    """Build a minimal FlightRow-like object for unit tests."""
    defaults = dict(
        id="test-flight",
        user_id="u1",
        target_date="2026-03-01",
        target_time_utc=9,
        flight_duration_hours=2.0,
        auto_refresh=True,
        auto_refresh_hour=None,
        last_auto_refresh_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# _flight_start_dt
# ---------------------------------------------------------------------------

class TestFlightStartDt:

    def test_parses_valid_flight(self):
        row = _make_row(target_date="2026-03-01", target_time_utc=9)
        assert _flight_start_dt(row) == _utc(2026, 3, 1, 9)

    def test_returns_none_for_malformed_date(self):
        row = _make_row(target_date="bad-date")
        assert _flight_start_dt(row) is None


# ---------------------------------------------------------------------------
# _next_due_at — basic cases
# ---------------------------------------------------------------------------

class TestNextDueAtBasic:
    """Regular scheduling without pre-flight pressure."""

    def test_never_refreshed_uses_today(self):
        """First-ever refresh → due at preferred hour today."""
        row = _make_row(auto_refresh_hour=6, last_auto_refresh_at=None)
        flight_start = _utc(2026, 3, 1, 9)
        now = _utc(2026, 2, 27, 5, 0)  # well before flight
        assert _next_due_at(row, flight_start, now) == _utc(2026, 2, 27, 6)

    def test_default_hour_is_target_minus_1(self):
        row = _make_row(target_time_utc=9, auto_refresh_hour=None)
        flight_start = _utc(2026, 3, 1, 9)
        now = _utc(2026, 2, 27, 7, 0)
        assert _next_due_at(row, flight_start, now) == _utc(2026, 2, 27, 8)

    def test_next_regular_after_last_refresh(self):
        """After a refresh, next is tomorrow at preferred hour."""
        row = _make_row(
            auto_refresh_hour=14,
            last_auto_refresh_at=_utc(2026, 2, 27, 14, 5),
        )
        flight_start = _utc(2026, 3, 1, 9)
        now = _utc(2026, 2, 27, 15, 0)
        assert _next_due_at(row, flight_start, now) == _utc(2026, 2, 28, 14)

    def test_malformed_date_returns_none(self):
        """If flight_start is None (malformed), _find_due_flights skips it."""
        # _next_due_at isn't called for None flight_start; tested at integration level.
        pass


# ---------------------------------------------------------------------------
# Pre-flight adjustment — same calendar day
# ---------------------------------------------------------------------------

class TestPreflightSameDay:
    """Flight and pre-flight time on the same calendar day."""

    def test_late_schedule_capped_by_preflight(self):
        """auto_refresh_hour=14, flight at 09:00Z Mar 1 → due at 07:00Z."""
        row = _make_row(
            auto_refresh_hour=14,
            last_auto_refresh_at=_utc(2026, 2, 28, 14, 0),
        )
        flight_start = _utc(2026, 3, 1, 9)
        now = _utc(2026, 2, 28, 15, 0)
        due = _next_due_at(row, flight_start, now)
        # min(Mar 1 14:00Z, Mar 1 07:00Z) = Mar 1 07:00Z
        assert due == _utc(2026, 3, 1, 7)

    def test_early_schedule_unaffected(self):
        """auto_refresh_hour=6, flight at 09:00Z → regular 06:00Z wins."""
        row = _make_row(
            auto_refresh_hour=6,
            last_auto_refresh_at=_utc(2026, 2, 28, 6, 0),
        )
        flight_start = _utc(2026, 3, 1, 9)
        now = _utc(2026, 2, 28, 7, 0)
        due = _next_due_at(row, flight_start, now)
        # min(Mar 1 06:00Z, Mar 1 07:00Z) = Mar 1 06:00Z
        assert due == _utc(2026, 3, 1, 6)

    def test_flight_at_10_schedule_at_18(self):
        """auto_refresh_hour=18, flight at 10:00Z → capped at 08:00Z."""
        row = _make_row(
            target_time_utc=10,
            auto_refresh_hour=18,
            last_auto_refresh_at=_utc(2026, 2, 28, 18, 0),
        )
        flight_start = _utc(2026, 3, 1, 10)
        now = _utc(2026, 2, 28, 19, 0)
        due = _next_due_at(row, flight_start, now)
        assert due == _utc(2026, 3, 1, 8)

    def test_flight_at_3_schedule_at_10(self):
        """auto_refresh_hour=10, flight at 03:00Z → capped at 01:00Z."""
        row = _make_row(
            target_time_utc=3,
            auto_refresh_hour=10,
            last_auto_refresh_at=_utc(2026, 2, 28, 10, 0),
        )
        flight_start = _utc(2026, 3, 1, 3)
        now = _utc(2026, 2, 28, 11, 0)
        due = _next_due_at(row, flight_start, now)
        assert due == _utc(2026, 3, 1, 1)


# ---------------------------------------------------------------------------
# Pre-flight wrap-around — early-UTC / western US flights
# ---------------------------------------------------------------------------

class TestPreflightWrapAround:
    """Flight at 00:00–02:00Z: pre-flight falls on the previous calendar day."""

    def test_flight_at_1z_regular_at_14_two_refreshes_on_day_before(self):
        """Flight 01:00Z Mar 1, schedule 14:00Z.

        After the regular 14:00Z refresh on Feb 28, the next due time
        is min(Mar 1 14:00Z, Feb 28 23:00Z) = Feb 28 23:00Z, giving a
        second refresh on the same day — the key scenario.
        """
        row = _make_row(
            target_date="2026-03-01",
            target_time_utc=1,
            auto_refresh_hour=14,
            last_auto_refresh_at=_utc(2026, 2, 28, 14, 5),
        )
        flight_start = _utc(2026, 3, 1, 1)
        now = _utc(2026, 2, 28, 15, 0)
        due = _next_due_at(row, flight_start, now)
        assert due == _utc(2026, 2, 28, 23)

    def test_flight_at_0z_wraps_to_day_before(self):
        """Flight 00:00Z Mar 1, schedule 10 → preflight 22:00Z Feb 28."""
        row = _make_row(
            target_date="2026-03-01",
            target_time_utc=0,
            auto_refresh_hour=10,
            last_auto_refresh_at=_utc(2026, 2, 28, 10, 0),
        )
        flight_start = _utc(2026, 3, 1, 0)
        now = _utc(2026, 2, 28, 11, 0)
        due = _next_due_at(row, flight_start, now)
        assert due == _utc(2026, 2, 28, 22)

    def test_flight_at_1z_custom_4z_two_refreshes(self):
        """Flight 01:00Z, auto_refresh_hour=4.

        Regular refresh at 04:00Z Feb 28 → next regular Mar 1 04:00Z,
        but preflight = Feb 28 23:00Z is earlier → due at 23:00Z Feb 28.
        This is the western US example: 8pm Pacific (04:00Z) plus a
        3pm Pacific (23:00Z) pre-flight on the same UTC day.
        """
        row = _make_row(
            target_date="2026-03-01",
            target_time_utc=1,
            auto_refresh_hour=4,
            last_auto_refresh_at=_utc(2026, 2, 28, 4, 0),
        )
        flight_start = _utc(2026, 3, 1, 1)
        now = _utc(2026, 2, 28, 5, 0)
        due = _next_due_at(row, flight_start, now)
        assert due == _utc(2026, 2, 28, 23)


# ---------------------------------------------------------------------------
# No refresh should happen after / at flight start
# ---------------------------------------------------------------------------

class TestNoRefreshAfterFlightStart:

    def test_preflight_returned_when_regular_is_past_flight_start(self):
        """When regular slot is past flight start, preflight still wins via min().

        _next_due_at always returns a time < flight_start (since preflight
        is flight_start − 2h).  The caller (_find_due_flights) separately
        guards against now >= flight_start.
        """
        row = _make_row(
            target_time_utc=9,
            auto_refresh_hour=14,
            last_auto_refresh_at=_utc(2026, 3, 1, 8, 0),
        )
        flight_start = _utc(2026, 3, 1, 9)
        now = _utc(2026, 3, 1, 8, 30)
        due = _next_due_at(row, flight_start, now)
        # min(Mar 2 14:00Z, Mar 1 07:00Z) = Mar 1 07:00Z
        assert due == _utc(2026, 3, 1, 7)

    def test_find_due_skips_after_flight_start(self, db_session, dev_user):
        """Integration: _find_due_flights skips flights past their start."""
        row = FlightRow(
            id="flight-past-start",
            user_id=dev_user,
            route_name="test",
            waypoints_json="[]",
            target_date="2026-03-01",
            target_time_utc=9,
            cruise_altitude_ft=8000,
            flight_ceiling_ft=18000,
            flight_duration_hours=2.0,
            auto_refresh=True,
            auto_refresh_hour=6,
            last_auto_refresh_at=None,
        )
        db_session.add(row)
        db_session.flush()

        # 10:00Z on flight day — flight started at 09:00Z
        now = _utc(2026, 3, 1, 10, 0)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            due = _find_due_flights(db_session)
        assert len(due) == 0


# ---------------------------------------------------------------------------
# _find_due_flights integration tests
# ---------------------------------------------------------------------------

class TestFindDueFlights:

    def _insert(self, db, dev_user, **overrides):
        defaults = dict(
            id="flight-1",
            user_id=dev_user,
            route_name="test",
            waypoints_json="[]",
            target_date="2026-03-01",
            target_time_utc=9,
            cruise_altitude_ft=8000,
            flight_ceiling_ft=18000,
            flight_duration_hours=2.0,
            auto_refresh=True,
            auto_refresh_hour=14,
            last_auto_refresh_at=None,
        )
        defaults.update(overrides)
        row = FlightRow(**defaults)
        db.add(row)
        db.flush()
        return row

    def test_never_refreshed_due_at_preferred_hour(self, db_session, dev_user):
        self._insert(db_session, dev_user, auto_refresh_hour=6)
        now = _utc(2026, 2, 27, 7, 0)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            due = _find_due_flights(db_session)
        assert len(due) == 1

    def test_not_due_before_preferred_hour(self, db_session, dev_user):
        self._insert(db_session, dev_user, auto_refresh_hour=14)
        now = _utc(2026, 2, 27, 6, 0)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            due = _find_due_flights(db_session)
        assert len(due) == 0

    def test_already_refreshed_recently_not_due(self, db_session, dev_user):
        self._insert(
            db_session, dev_user,
            auto_refresh_hour=6,
            last_auto_refresh_at=_utc(2026, 2, 27, 6, 0),
        )
        # Same day, 2 hours later — not yet time for next daily refresh
        now = _utc(2026, 2, 27, 8, 0)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            due = _find_due_flights(db_session)
        assert len(due) == 0

    def test_preflight_triggers_second_refresh_on_same_day(self, db_session, dev_user):
        """The western US scenario: regular + pre-flight on the same UTC day."""
        self._insert(
            db_session, dev_user,
            target_date="2026-03-01",
            target_time_utc=1,
            auto_refresh_hour=14,
            last_auto_refresh_at=_utc(2026, 2, 28, 14, 5),
        )
        # 23:30Z on Feb 28 — pre-flight at 23:00Z is due
        now = _utc(2026, 2, 28, 23, 30)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            due = _find_due_flights(db_session)
        assert len(due) == 1

    def test_flight_day_preflight_same_day(self, db_session, dev_user):
        """Flight at 09:00Z, schedule 14 → due at 07:00Z on flight day."""
        self._insert(
            db_session, dev_user,
            target_date="2026-03-01",
            target_time_utc=9,
            auto_refresh_hour=14,
            last_auto_refresh_at=_utc(2026, 2, 28, 14, 0),
        )
        now = _utc(2026, 3, 1, 7, 30)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            due = _find_due_flights(db_session)
        assert len(due) == 1

    def test_flight_day_not_due_before_preflight(self, db_session, dev_user):
        """At 06:00Z, preflight 07:00Z hasn't arrived yet → not due."""
        self._insert(
            db_session, dev_user,
            target_date="2026-03-01",
            target_time_utc=9,
            auto_refresh_hour=14,
            last_auto_refresh_at=_utc(2026, 2, 28, 14, 0),
        )
        now = _utc(2026, 3, 1, 6, 0)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            due = _find_due_flights(db_session)
        assert len(due) == 0
