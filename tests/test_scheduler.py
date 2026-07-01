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
    _seconds_until_next_30min_boundary,
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
        departure_time=_utc(2026, 3, 1, 9),
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
        row = _make_row(departure_time=_utc(2026, 3, 1, 9))
        assert _flight_start_dt(row) == _utc(2026, 3, 1, 9)

    def test_returns_none_for_missing_departure(self):
        row = _make_row(departure_time=None)
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
        row = _make_row(departure_time=_utc(2026, 3, 1, 9), auto_refresh_hour=None)
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
            departure_time=_utc(2026, 3, 1, 10),
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
            departure_time=_utc(2026, 3, 1, 3),
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
            departure_time=_utc(2026, 3, 1, 1),
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
            departure_time=_utc(2026, 3, 1, 0),
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
            departure_time=_utc(2026, 3, 1, 1),
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

    def test_no_more_refreshes_after_preflight_served(self):
        """Once refreshed after preflight, only regular schedule applies.

        Last refresh at 08:00Z is past preflight 07:00Z, so preflight slot
        is satisfied.  Next due is the regular slot (Mar 2 14:00Z), which is
        past flight_start — so _find_due_flights won't trigger another refresh.
        """
        row = _make_row(
            departure_time=_utc(2026, 3, 1, 9),
            auto_refresh_hour=14,
            last_auto_refresh_at=_utc(2026, 3, 1, 8, 0),
        )
        flight_start = _utc(2026, 3, 1, 9)
        now = _utc(2026, 3, 1, 8, 30)
        due = _next_due_at(row, flight_start, now)
        # Preflight (07:00Z) already served → next is regular Mar 2 14:00Z
        assert due == _utc(2026, 3, 2, 14)

    def test_preflight_not_due_again_after_served(self):
        """After the pre-flight refresh, the flight must NOT be due on
        subsequent scheduler polls (bug: repeated emails every ~hour)."""
        row = _make_row(
            departure_time=_utc(2026, 3, 1, 9),
            auto_refresh_hour=14,
            # Pre-flight refresh happened at 07:10Z (preflight was 07:00Z)
            last_auto_refresh_at=_utc(2026, 3, 1, 7, 10),
        )
        flight_start = _utc(2026, 3, 1, 9)

        # Poll 10 min later
        due = _next_due_at(row, flight_start, _utc(2026, 3, 1, 7, 20))
        # Next regular is Mar 2 14:00Z — well past flight start
        assert due == _utc(2026, 3, 2, 14)

        # Poll 1 hour later — still shouldn't be due
        due = _next_due_at(row, flight_start, _utc(2026, 3, 1, 8, 10))
        assert due == _utc(2026, 3, 2, 14)

    def test_find_due_skips_after_flight_start(self, db_session, dev_user):
        """Integration: _find_due_flights skips flights past their start."""
        row = FlightRow(
            id="flight-past-start",
            user_id=dev_user,
            route_name="test",
            waypoints_json="[]",
            departure_time=_utc(2026, 3, 1, 9),
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
            departure_time=_utc(2026, 3, 1, 9),
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
            departure_time=_utc(2026, 3, 1, 1),
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
            departure_time=_utc(2026, 3, 1, 9),
            auto_refresh_hour=14,
            last_auto_refresh_at=_utc(2026, 2, 28, 14, 0),
        )
        now = _utc(2026, 3, 1, 7, 30)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            due = _find_due_flights(db_session)
        assert len(due) == 1

    def test_not_due_after_preflight_served(self, db_session, dev_user):
        """After the pre-flight refresh, flight should NOT be due again."""
        self._insert(
            db_session, dev_user,
            departure_time=_utc(2026, 3, 1, 9),
            auto_refresh_hour=14,
            # Pre-flight refresh already happened at 07:10Z
            last_auto_refresh_at=_utc(2026, 3, 1, 7, 10),
        )
        # 08:00Z — between preflight (07:00Z) and flight start (09:00Z)
        now = _utc(2026, 3, 1, 8, 0)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            due = _find_due_flights(db_session)
        assert len(due) == 0

    def test_flight_day_not_due_before_preflight(self, db_session, dev_user):
        """At 06:00Z, preflight 07:00Z hasn't arrived yet → not due."""
        self._insert(
            db_session, dev_user,
            departure_time=_utc(2026, 3, 1, 9),
            auto_refresh_hour=14,
            last_auto_refresh_at=_utc(2026, 2, 28, 14, 0),
        )
        now = _utc(2026, 3, 1, 6, 0)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            due = _find_due_flights(db_session)
        assert len(due) == 0


# ---------------------------------------------------------------------------
# _seconds_until_next_30min_boundary
# ---------------------------------------------------------------------------

class TestSecondsUntilNext30MinBoundary:
    """Boundary helper drives the METAR ingest loop's timing.

    Given an offset within a 30-min bucket, returns seconds until the next
    fire instant. Two candidates exist per hour (``:offset`` and
    ``:30+offset``); when both are in the past, falls through to the first
    slot of the next hour.
    """

    @pytest.mark.parametrize("now_min,expected_secs", [
        (0, 1800),    # 12:00:00 → next slot 12:30 (12:00 not strictly > now)
        (1, 1740),    # 12:01 → 12:30 in 29min
        (15, 900),    # 12:15 → 12:30 in 15min
        (29, 60),     # 12:29 → 12:30 in 1min
        (30, 1800),   # 12:30:00 → next slot 13:00
        (31, 1740),   # 12:31 → 13:00 in 29min
        (45, 900),    # 12:45 → 13:00 in 15min
        (59, 60),     # 12:59 → 13:00 in 1min
    ])
    def test_offset_zero(self, now_min, expected_secs):
        """offset_seconds=0 fires at :00 and :30 sharp."""
        fixed = _utc(2026, 4, 1, 12, now_min, 0)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            secs = _seconds_until_next_30min_boundary(0)
        assert secs == expected_secs

    @pytest.mark.parametrize("now_min,expected_secs", [
        (0, 300),     # 12:00 → 12:05 in 5min
        (5, 1800),    # 12:05:00 → next slot 12:35
        (10, 1500),   # 12:10 → 12:35 in 25min
        (35, 1800),   # 12:35:00 → next slot 13:05
        (40, 1500),   # 12:40 → 13:05 in 25min
    ])
    def test_offset_five_minutes(self, now_min, expected_secs):
        """offset_seconds=300 fires at :05 and :35."""
        fixed = _utc(2026, 4, 1, 12, now_min, 0)
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            secs = _seconds_until_next_30min_boundary(300)
        assert secs == expected_secs

    def test_falls_through_hour_boundary(self):
        """At 12:35, both offsets in this hour past — returns to 13:00."""
        fixed = _utc(2026, 4, 1, 12, 35, 30)  # 12:35:30
        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            secs = _seconds_until_next_30min_boundary(0)
        # 13:00:00 - 12:35:30 = 24min 30s
        assert secs == 24 * 60 + 30

    @pytest.mark.parametrize("bad_offset", [-1, 1800, 1801, 3600])
    def test_rejects_out_of_range_offset(self, bad_offset):
        """Offset must be in [0, 1800) — otherwise minute=30+offset overflows."""
        with pytest.raises(ValueError, match="offset_seconds"):
            _seconds_until_next_30min_boundary(bad_offset)


# ---------------------------------------------------------------------------
# Tiered refresh gate in the scheduler (issue #167)
# ---------------------------------------------------------------------------

class TestAutoRefreshGate:
    """The scheduler applies decide_refresh's full/none policy but never the
    realtime fallback — a non-"full" decision means skip (no pipeline run).
    """

    @patch("weatherbrief.api.packs._prepare_refresh")
    @patch("weatherbrief.pipeline.execute_briefing")
    @patch("weatherbrief.api.packs.decide_refresh")
    @patch("weatherbrief.api.packs._build_data_status")
    @patch("weatherbrief.storage.flights.list_packs")
    @patch("weatherbrief.storage.flights._row_to_flight")
    @patch("weatherbrief.scheduler.SessionLocal")
    def test_skips_when_gate_not_full(
        self, mock_session, mock_row_to_flight, mock_list,
        mock_status, mock_decide, mock_exec, mock_prepare,
    ):
        from unittest.mock import MagicMock

        from weatherbrief.api.packs import DataStatus, RefreshDecision
        from weatherbrief.models import BriefingPackMeta
        from weatherbrief.scheduler import _auto_refresh_one

        mock_session.return_value = MagicMock()
        mock_row_to_flight.return_value = SimpleNamespace(
            departure_time=datetime.now(timezone.utc) + timedelta(hours=3),
        )
        mock_list.return_value = [BriefingPackMeta(
            flight_id="f", fetch_timestamp=datetime.now(timezone.utc),
            days_out=0, artifact_path="/tmp/pack",
        )]
        mock_status.return_value = DataStatus(fresh=True)
        # Realtime is button-only; the scheduler must skip it.
        mock_decide.return_value = RefreshDecision(
            mode="realtime", reason="d0", needed=1, n_eligible=3, n_updated=0, days_out=0,
        )

        _auto_refresh_one(_make_row(), SimpleNamespace(db_path="/fake/db"), "u1")

        mock_exec.assert_not_called()
        mock_prepare.assert_not_called()

    @patch("weatherbrief.scheduler._try_send_email")
    @patch("weatherbrief.api.packs._finalize_refresh")
    @patch("weatherbrief.api.packs._prepare_refresh")
    @patch("weatherbrief.pipeline.execute_briefing")
    @patch("weatherbrief.api.packs.decide_refresh")
    @patch("weatherbrief.api.packs._build_data_status")
    @patch("weatherbrief.storage.flights.list_packs")
    @patch("weatherbrief.storage.flights._row_to_flight")
    @patch("weatherbrief.scheduler.SessionLocal")
    def test_runs_pipeline_when_full(
        self, mock_session, mock_row_to_flight, mock_list,
        mock_status, mock_decide, mock_exec, mock_prepare, mock_finalize, mock_email,
    ):
        from unittest.mock import MagicMock

        from weatherbrief.api.packs import DataStatus, RefreshDecision
        from weatherbrief.models import BriefingPackMeta
        from weatherbrief.scheduler import _auto_refresh_one

        mock_session.return_value = MagicMock()
        mock_row_to_flight.return_value = SimpleNamespace(
            departure_time=datetime.now(timezone.utc) + timedelta(days=2),
        )
        mock_list.return_value = [BriefingPackMeta(
            flight_id="f", fetch_timestamp=datetime.now(timezone.utc),
            days_out=2, artifact_path="/tmp/pack",
        )]
        mock_status.return_value = DataStatus(fresh=False)
        mock_decide.return_value = RefreshDecision(
            mode="full", reason="all updated", needed=3, n_eligible=3, n_updated=3, days_out=2,
        )
        mock_prepare.return_value = (
            MagicMock(), datetime.now(timezone.utc), "/tmp/pack", MagicMock(), {}, None,
        )
        mock_exec.return_value = MagicMock()

        _auto_refresh_one(_make_row(), SimpleNamespace(db_path="/fake/db"), "u1")

        mock_exec.assert_called_once()


# ---------------------------------------------------------------------------
# Model-update-aware email timing (issue #192)
# ---------------------------------------------------------------------------

class TestDeferRegularForModelUpdate:
    """Defer a regular slot to ride an imminent ECMWF full-horizon (00/12Z)
    delivery (~06:40 / 18:40 UTC). Only ever defers, bounded, never on the day
    of / day before the flight.
    """

    # Flight far out so the days_out >= 2 gate is satisfied unless stated.
    FLIGHT = _utc(2026, 3, 5, 9)

    def _store(self, init, next_expected, *, stale=False):
        """Build a MarkerStore with one ECMWF marker.

        ``last_check`` is real wall-clock now (not the simulated timeline) so
        ``Marker.is_stale`` — which compares against ``datetime.now`` — reports
        a healthy heartbeat unless ``stale=True``.
        """
        from weatherbrief.fetch.freshness.markers import Marker, MarkerStore

        store = MarkerStore()
        store._markers[("ecmwf:direct", "ecmwf")] = Marker(
            source="ecmwf:direct", model="ecmwf",
            init=init, next_expected=next_expected,
            last_check=None if stale else datetime.now(timezone.utc),
        )
        return store

    def _defer(self, regular, store, flight=None):
        from weatherbrief.scheduler import _defer_regular_for_model_update

        return _defer_regular_for_model_update(regular, flight or self.FLIGHT, store)

    def test_just_missed_morning_defers_to_after_delivery(self):
        # 06:00 slot, latest run is prior 18Z, 00Z lands 06:40 → defer to 07:00.
        store = self._store(_utc(2026, 2, 28, 18), _utc(2026, 3, 1, 6, 40))
        assert self._defer(_utc(2026, 3, 1, 6), store) == _utc(2026, 3, 1, 7)

    def test_just_missed_05z_within_window_defers(self):
        # 05:00 slot is 1h40m before 06:40 — inside the 2h window → defer 07:00.
        store = self._store(_utc(2026, 2, 28, 18), _utc(2026, 3, 1, 6, 40))
        assert self._defer(_utc(2026, 3, 1, 5), store) == _utc(2026, 3, 1, 7)

    def test_evening_just_missed_defers(self):
        # 18:00 slot, 12Z lands 18:40 → defer to 19:00.
        store = self._store(_utc(2026, 3, 1, 0), _utc(2026, 3, 1, 18, 40))
        assert self._defer(_utc(2026, 3, 1, 18), store) == _utc(2026, 3, 1, 19)

    def test_riding_fresh_no_defer(self):
        # 08:00 slot already past the 06:40 delivery; next full run (18:40) is
        # well beyond the 2h window → no defer.
        store = self._store(_utc(2026, 2, 28, 18), _utc(2026, 3, 1, 6, 40))
        assert self._defer(_utc(2026, 3, 1, 8), store) == _utc(2026, 3, 1, 8)

    def test_too_early_outside_window_no_defer(self):
        # 04:00 slot is 2h40m before 06:40 — outside the 2h window → no defer.
        store = self._store(_utc(2026, 2, 28, 18), _utc(2026, 3, 1, 6, 40))
        assert self._defer(_utc(2026, 3, 1, 4), store) == _utc(2026, 3, 1, 4)

    def test_day_of_never_defers(self):
        # Regular slot on the flight day → timeliness wins, never defer.
        store = self._store(_utc(2026, 2, 28, 18), _utc(2026, 3, 1, 6, 40))
        flight = _utc(2026, 3, 1, 9)
        assert self._defer(_utc(2026, 3, 1, 6), store, flight) == _utc(2026, 3, 1, 6)

    def test_day_before_never_defers(self):
        # days_out == 1 (slot Mar 4, flight Mar 5) → never defer.
        store = self._store(_utc(2026, 3, 3, 18), _utc(2026, 3, 4, 6, 40))
        flight = _utc(2026, 3, 5, 9)
        assert self._defer(_utc(2026, 3, 4, 6), store, flight) == _utc(2026, 3, 4, 6)

    def test_run_already_in_hand_no_defer(self):
        # Marker already advanced to the 00Z target → fresh data in hand.
        store = self._store(_utc(2026, 3, 1, 0), _utc(2026, 3, 1, 12, 40))
        assert self._defer(_utc(2026, 3, 1, 6), store) == _utc(2026, 3, 1, 6)

    def test_slip_respected_small(self):
        # 00Z running slightly late (06:55) → defer to delivery + 20m = 07:15.
        store = self._store(_utc(2026, 2, 28, 18), _utc(2026, 3, 1, 6, 55))
        assert self._defer(_utc(2026, 3, 1, 6), store) == _utc(2026, 3, 1, 7, 15)

    def test_slip_capped_at_max_wait(self):
        # 00Z badly slipping (10:00) → capped at regular + 2h30m = 08:30.
        store = self._store(_utc(2026, 2, 28, 18), _utc(2026, 3, 1, 10, 0))
        assert self._defer(_utc(2026, 3, 1, 6), store) == _utc(2026, 3, 1, 8, 30)

    def test_stale_marker_no_defer(self):
        # Suspect heartbeat → can't confirm an imminent delivery → no defer.
        store = self._store(_utc(2026, 2, 28, 18), _utc(2026, 3, 1, 6, 40), stale=True)
        assert self._defer(_utc(2026, 3, 1, 6), store) == _utc(2026, 3, 1, 6)

    def test_missing_marker_no_defer(self):
        from weatherbrief.fetch.freshness.markers import MarkerStore

        assert self._defer(_utc(2026, 3, 1, 6), MarkerStore()) == _utc(2026, 3, 1, 6)


class TestNextDueAtModelUpdate:
    """`_next_due_at` only applies the deferral when `apply_model_update` is set,
    and only to the regular term (never preflight).
    """

    def _store(self, init, next_expected):
        from weatherbrief.fetch.freshness.markers import Marker, MarkerStore

        store = MarkerStore()
        store._markers[("ecmwf:direct", "ecmwf")] = Marker(
            source="ecmwf:direct", model="ecmwf",
            init=init, next_expected=next_expected,
            last_check=datetime.now(timezone.utc),
        )
        return store

    def test_null_default_snaps_out_of_dead_zone(self):
        # Departure-1 default hour lands in the pre-delivery dead zone; with the
        # snap the regular slot rides the imminent 00Z run. Flight is 6 days out
        # so the days_out >= 2 gate passes and preflight is far in the future.
        row = _make_row(departure_time=_utc(2026, 3, 7, 7), auto_refresh_hour=None)
        store = self._store(_utc(2026, 2, 28, 18), _utc(2026, 3, 1, 6, 40))
        now = _utc(2026, 3, 1, 5, 30)
        due = _next_due_at(
            row, _utc(2026, 3, 7, 7), now,
            apply_model_update=True, store=store,
        )
        # regular = Mar 1 06:00 → deferred to Mar 1 07:00; min(.., preflight
        # Mar 7 05:00) = Mar 1 07:00.
        assert due == _utc(2026, 3, 1, 7)

    def test_no_apply_leaves_slot_unchanged(self):
        row = _make_row(departure_time=_utc(2026, 3, 7, 7), auto_refresh_hour=None)
        store = self._store(_utc(2026, 2, 28, 18), _utc(2026, 3, 1, 6, 40))
        now = _utc(2026, 3, 1, 5, 30)
        due = _next_due_at(
            row, _utc(2026, 3, 7, 7), now,
            apply_model_update=False, store=store,
        )
        assert due == _utc(2026, 3, 1, 6)

    def test_explicit_hour_riding_fresh_not_snapped(self):
        # Explicit 08:00 already rides the fresh 00Z run (next full run is 12Z
        # at 18:40, beyond the 2h window) → unchanged even with the toggle on.
        row = _make_row(departure_time=_utc(2026, 3, 7, 9), auto_refresh_hour=8)
        store = self._store(_utc(2026, 2, 28, 18), _utc(2026, 3, 1, 6, 40))
        now = _utc(2026, 3, 1, 5, 30)
        due = _next_due_at(
            row, _utc(2026, 3, 7, 9), now,
            apply_model_update=True, store=store,
        )
        assert due == _utc(2026, 3, 1, 8)

    def test_preflight_term_untouched_by_snap(self):
        # A late regular hour is still capped by preflight; the snap only
        # touches the regular term, so the result stays the preflight time.
        row = _make_row(
            departure_time=_utc(2026, 3, 5, 9),
            auto_refresh_hour=14,
            last_auto_refresh_at=_utc(2026, 3, 4, 14, 0),
        )
        store = self._store(_utc(2026, 2, 28, 18), _utc(2026, 3, 5, 6, 40))
        now = _utc(2026, 3, 4, 15, 0)
        due = _next_due_at(
            row, _utc(2026, 3, 5, 9), now,
            apply_model_update=True, store=store,
        )
        # preflight = 07:00Z on flight day, earlier than the 14:00 regular slot.
        assert due == _utc(2026, 3, 5, 7)
