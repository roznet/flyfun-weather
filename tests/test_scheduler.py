"""Tests for the auto-refresh scheduler, in particular the pre-flight
hour adjustment logic in ``_effective_refresh_hour`` and the due-flight
filter ``_find_due_flights``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

from weatherbrief.db.models import FlightRow
from weatherbrief.scheduler import (
    _PREFLIGHT_LEAD_HOURS,
    _effective_refresh_hour,
    _find_due_flights,
    _is_flight_past,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(**overrides) -> FlightRow:
    """Build a minimal FlightRow-like object for unit tests."""
    defaults = dict(
        id="test-flight",
        user_id="u1",
        target_date="2026-03-01",
        target_time_utc=9,
        flight_duration_hours=2.0,
        auto_refresh=True,
        auto_refresh_hour=None,
        last_auto_refresh_date=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# _effective_refresh_hour — basic behaviour
# ---------------------------------------------------------------------------

class TestEffectiveRefreshHour:
    """Unit tests for _effective_refresh_hour."""

    def test_default_hour_when_no_custom(self):
        """No custom hour → target_time - 1."""
        row = _make_row(target_time_utc=9, auto_refresh_hour=None)
        today = date(2026, 2, 27)  # well before flight
        assert _effective_refresh_hour(row, today, current_hour=6) == 8

    def test_custom_hour_on_normal_day(self):
        """Custom hour used when not near flight day."""
        row = _make_row(target_time_utc=9, auto_refresh_hour=14)
        today = date(2026, 2, 27)
        assert _effective_refresh_hour(row, today, current_hour=6) == 14

    def test_malformed_date_falls_back(self):
        row = _make_row(target_date="bad-date", auto_refresh_hour=6)
        assert _effective_refresh_hour(row, date(2026, 3, 1), current_hour=5) == 6


# ---------------------------------------------------------------------------
# Flight-day adjustments
# ---------------------------------------------------------------------------

class TestFlightDayAdjustment:
    """On the day of the flight, late schedules move to 2 h before."""

    def test_late_schedule_moved_to_preflight(self):
        """auto_refresh_hour=14, flight at 09:00Z → adjusted to 07:00Z."""
        row = _make_row(target_time_utc=9, auto_refresh_hour=14)
        flight_day = date(2026, 3, 1)
        assert _effective_refresh_hour(row, flight_day, current_hour=6) == 7

    def test_early_schedule_unchanged(self):
        """auto_refresh_hour=6, flight at 09:00Z → stays 06:00Z."""
        row = _make_row(target_time_utc=9, auto_refresh_hour=6)
        flight_day = date(2026, 3, 1)
        assert _effective_refresh_hour(row, flight_day, current_hour=5) == 6

    def test_no_refresh_after_flight_started(self):
        """Once flight has started, return None."""
        row = _make_row(target_time_utc=9, auto_refresh_hour=6)
        flight_day = date(2026, 3, 1)
        assert _effective_refresh_hour(row, flight_day, current_hour=9) is None
        assert _effective_refresh_hour(row, flight_day, current_hour=12) is None

    def test_no_refresh_after_start_even_with_late_schedule(self):
        row = _make_row(target_time_utc=9, auto_refresh_hour=14)
        flight_day = date(2026, 3, 1)
        assert _effective_refresh_hour(row, flight_day, current_hour=10) is None

    def test_default_hour_on_flight_day_unchanged(self):
        """Default (target-1) is already before flight start."""
        row = _make_row(target_time_utc=9, auto_refresh_hour=None)
        flight_day = date(2026, 3, 1)
        # default = 8, which is < 9 → no adjustment
        assert _effective_refresh_hour(row, flight_day, current_hour=7) == 8

    def test_flight_at_10_schedule_at_18(self):
        """auto_refresh_hour=18, flight at 10:00Z → 08:00Z."""
        row = _make_row(target_time_utc=10, auto_refresh_hour=18)
        flight_day = date(2026, 3, 1)
        assert _effective_refresh_hour(row, flight_day, current_hour=7) == 8

    def test_flight_at_3_schedule_at_10(self):
        """auto_refresh_hour=10, flight at 03:00Z → 01:00Z (same day)."""
        row = _make_row(target_time_utc=3, auto_refresh_hour=10)
        flight_day = date(2026, 3, 1)
        assert _effective_refresh_hour(row, flight_day, current_hour=0) == 1


# ---------------------------------------------------------------------------
# Day-before adjustments (early-UTC / western US flights)
# ---------------------------------------------------------------------------

class TestDayBeforeAdjustment:
    """When the pre-flight time wraps to the previous calendar day."""

    def test_flight_at_1z_wraps_to_day_before(self):
        """Flight 01:00Z, schedule 14 → pre-flight 23:00Z day before."""
        row = _make_row(
            target_date="2026-03-01",
            target_time_utc=1,
            auto_refresh_hour=14,
        )
        day_before = date(2026, 2, 28)
        assert _effective_refresh_hour(row, day_before, current_hour=14) == 23

    def test_flight_at_0z_wraps_to_day_before(self):
        """Flight 00:00Z, schedule 10 → pre-flight 22:00Z day before."""
        row = _make_row(
            target_date="2026-03-01",
            target_time_utc=0,
            auto_refresh_hour=10,
        )
        day_before = date(2026, 2, 28)
        assert _effective_refresh_hour(row, day_before, current_hour=10) == 22

    def test_flight_at_1z_no_slot_on_flight_day(self):
        """Flight at 01:00Z — no valid slot on the flight day itself."""
        row = _make_row(
            target_date="2026-03-01",
            target_time_utc=1,
            auto_refresh_hour=14,
        )
        flight_day = date(2026, 3, 1)
        # Pre-flight at 23:00Z is on the previous day → None
        assert _effective_refresh_hour(row, flight_day, current_hour=0) is None

    def test_no_wrap_when_preflight_is_on_flight_day(self):
        """Flight at 09:00Z — pre-flight 07:00Z is on flight day, not day before."""
        row = _make_row(
            target_date="2026-03-01",
            target_time_utc=9,
            auto_refresh_hour=14,
        )
        day_before = date(2026, 2, 28)
        # Not a wrap-around → regular schedule on day before
        assert _effective_refresh_hour(row, day_before, current_hour=14) == 14

    def test_two_days_before_no_adjustment(self):
        """Two days before flight — regular schedule, no adjustment."""
        row = _make_row(
            target_date="2026-03-01",
            target_time_utc=1,
            auto_refresh_hour=14,
        )
        two_before = date(2026, 2, 27)
        assert _effective_refresh_hour(row, two_before, current_hour=14) == 14

    def test_flight_at_2z_wraps_to_day_before(self):
        """Flight 02:00Z, schedule 8 → pre-flight 00:00Z = flight day, NOT wrap."""
        row = _make_row(
            target_date="2026-03-01",
            target_time_utc=2,
            auto_refresh_hour=8,
        )
        # pre-flight = 00:00Z on March 1 = flight day → adjusted on flight day
        flight_day = date(2026, 3, 1)
        assert _effective_refresh_hour(row, flight_day, current_hour=0) == 0

        # Day before: no wrap since pre-flight is on flight day
        day_before = date(2026, 2, 28)
        assert _effective_refresh_hour(row, day_before, current_hour=8) == 8


# ---------------------------------------------------------------------------
# _is_flight_past
# ---------------------------------------------------------------------------

class TestIsFlightPast:

    def test_flight_in_future(self):
        row = _make_row(target_date="2099-01-01", target_time_utc=12,
                        flight_duration_hours=2.0)
        assert _is_flight_past(row) is False

    def test_flight_in_past(self):
        row = _make_row(target_date="2020-01-01", target_time_utc=12,
                        flight_duration_hours=2.0)
        assert _is_flight_past(row) is True

    def test_malformed_date_treated_as_past(self):
        row = _make_row(target_date="not-a-date")
        assert _is_flight_past(row) is True


# ---------------------------------------------------------------------------
# _find_due_flights (integration with DB)
# ---------------------------------------------------------------------------

class TestFindDueFlights:
    """Integration tests using an in-memory SQLite database."""

    def _insert_flight(self, db, **overrides):
        defaults = dict(
            id="flight-1",
            user_id="u1",
            route_name="test",
            waypoints_json="[]",
            target_date="2026-03-01",
            target_time_utc=9,
            cruise_altitude_ft=8000,
            flight_ceiling_ft=18000,
            flight_duration_hours=2.0,
            auto_refresh=True,
            auto_refresh_hour=14,
            last_auto_refresh_date=None,
        )
        defaults.update(overrides)
        row = FlightRow(**defaults)
        db.add(row)
        db.flush()
        return row

    def test_due_flight_returned(self, db_session, dev_user):
        """Flight with auto_refresh=True and due hour is returned."""
        self._insert_flight(db_session, user_id=dev_user, auto_refresh_hour=6)
        now = datetime(2026, 2, 27, 7, 0, tzinfo=timezone.utc)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            due = _find_due_flights(db_session)
        assert len(due) == 1

    def test_already_refreshed_today_skipped(self, db_session, dev_user):
        self._insert_flight(
            db_session, user_id=dev_user,
            auto_refresh_hour=6,
            last_auto_refresh_date="2026-02-27",
        )
        now = datetime(2026, 2, 27, 7, 0, tzinfo=timezone.utc)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            due = _find_due_flights(db_session)
        assert len(due) == 0

    def test_flight_day_adjustment_in_find_due(self, db_session, dev_user):
        """On flight day, late schedule adjusted to 2h before → due at 07:00Z."""
        self._insert_flight(
            db_session, user_id=dev_user,
            target_date="2026-03-01",
            target_time_utc=9,
            auto_refresh_hour=14,
        )
        # At 07:00Z on flight day — adjusted hour is 7, so due
        now = datetime(2026, 3, 1, 7, 0, tzinfo=timezone.utc)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            due = _find_due_flights(db_session)
        assert len(due) == 1

    def test_flight_day_not_due_before_adjusted_hour(self, db_session, dev_user):
        """At 06:00Z, adjusted hour is 07 → not yet due."""
        self._insert_flight(
            db_session, user_id=dev_user,
            target_date="2026-03-01",
            target_time_utc=9,
            auto_refresh_hour=14,
        )
        now = datetime(2026, 3, 1, 6, 0, tzinfo=timezone.utc)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            due = _find_due_flights(db_session)
        assert len(due) == 0

    def test_flight_day_skipped_after_start(self, db_session, dev_user):
        """After flight has started, no auto-refresh."""
        self._insert_flight(
            db_session, user_id=dev_user,
            target_date="2026-03-01",
            target_time_utc=9,
            auto_refresh_hour=14,
        )
        now = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            due = _find_due_flights(db_session)
        assert len(due) == 0
