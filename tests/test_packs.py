"""Unit tests for packs helper functions (_build_route_config, _prepare_refresh)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from airport_mocks import TEST_AIRPORTS, mock_model
from weatherbrief.models import Flight, Waypoint


# --- _build_route_config ---


class TestBuildRouteConfig:
    """Test _build_route_config(flight, db_path).

    Mocks _load_airport_model so RouteResolver runs for real.
    """

    def _make_flight(self, waypoints, **kwargs):
        defaults = dict(
            id="test-flight",
            user_id="test-user",
            route_name="test_route",
            waypoints=waypoints,
            departure_time=datetime.now(timezone.utc) + timedelta(days=3),
            cruise_altitude_ft=8000,
            flight_ceiling_ft=18000,
            flight_duration_hours=4.5,
            created_at=datetime.now(timezone.utc),
        )
        defaults.update(kwargs)
        return Flight(**defaults)

    @patch("weatherbrief.airports._load_airport_model")
    def test_happy_path(self, mock_load):
        """Two valid waypoints produce a RouteConfig."""
        from weatherbrief.api.packs import _build_route_config

        mock_load.return_value = mock_model(TEST_AIRPORTS)
        flight = self._make_flight(["EGTK", "LSGS"])

        route = _build_route_config(flight, "/fake/db")

        assert len(route.waypoints) == 2
        assert route.waypoints[0].icao == "EGTK"
        assert route.waypoints[1].icao == "LSGS"
        assert route.cruise_altitude_ft == 8000
        assert route.flight_ceiling_ft == 18000
        assert route.flight_duration_hours == 4.5

    def test_no_waypoints_raises(self):
        """Flight with empty waypoints raises ValueError."""
        from weatherbrief.api.packs import _build_route_config

        flight = self._make_flight([])
        with pytest.raises(ValueError, match="no waypoints"):
            _build_route_config(flight, "/fake/db")

    @patch("weatherbrief.airports._load_airport_model")
    def test_route_name_fallback(self, mock_load):
        """When route_name is None, uses arrow-joined waypoints."""
        from weatherbrief.api.packs import _build_route_config

        mock_load.return_value = mock_model(TEST_AIRPORTS)
        flight = self._make_flight(["EGTK", "LSGS"], route_name="")

        route = _build_route_config(flight, "/fake/db")
        assert "EGTK" in route.name
        assert "LSGS" in route.name

    @patch("weatherbrief.airports._load_airport_model")
    def test_unknown_waypoint_raises(self, mock_load):
        """Unknown waypoint in flight raises KeyError."""
        from weatherbrief.api.packs import _build_route_config

        mock_load.return_value = mock_model(TEST_AIRPORTS)
        flight = self._make_flight(["EGTK", "ZZZZ"])

        with pytest.raises(KeyError):
            _build_route_config(flight, "/fake/db")


# --- _prepare_refresh ---


class TestPrepareRefresh:
    """Test _prepare_refresh().

    Mocks airport model and runs everything else real (profile loading,
    rate limits, option building) against the test DB.
    """

    def _make_flight(self, waypoints, departure_offset_days=3, **kwargs):
        defaults = dict(
            id="test-flight",
            user_id="test-user",
            route_name="test_route",
            waypoints=waypoints,
            departure_time=datetime.now(timezone.utc) + timedelta(days=departure_offset_days),
            cruise_altitude_ft=8000,
            flight_ceiling_ft=18000,
            flight_duration_hours=4.5,
            created_at=datetime.now(timezone.utc),
        )
        defaults.update(kwargs)
        return Flight(**defaults)

    @patch("weatherbrief.airports._load_airport_model")
    def test_basic_no_db(self, mock_load, monkeypatch, tmp_path):
        """Without DB session, returns route + options with defaults."""
        from weatherbrief.api.packs import _prepare_refresh

        mock_load.return_value = mock_model(TEST_AIRPORTS)
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

        flight = self._make_flight(["EGTK", "LSGS"])
        route, fetch_ts, pack_path, options, model_metadata = _prepare_refresh(
            flight, "/fake/db", "test-user", "test-flight",
        )

        assert len(route.waypoints) == 2
        assert route.cruise_altitude_ft == 8000
        assert options.enrich_grib is True
        assert pack_path.exists()

    @patch("weatherbrief.airports._load_airport_model")
    def test_with_db_session(self, mock_load, monkeypatch, tmp_path, db_session, dev_user):
        """With DB session, loads preferences and checks rate limits."""
        from weatherbrief.api.packs import _prepare_refresh

        mock_load.return_value = mock_model(TEST_AIRPORTS)
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

        flight = self._make_flight(["EGTK", "LSGS"])
        route, fetch_ts, pack_path, options, model_metadata = _prepare_refresh(
            flight, "/fake/db", dev_user, "test-flight", db=db_session,
        )

        assert len(route.waypoints) == 2
        assert options.user_id == dev_user
        assert options.airports_db_path == "/fake/db"

    @patch("weatherbrief.airports._load_airport_model")
    def test_historical_disables_services(self, mock_load, monkeypatch, tmp_path):
        """Historical flights disable GRAMET and LLM digest."""
        from weatherbrief.api.packs import _prepare_refresh

        mock_load.return_value = mock_model(TEST_AIRPORTS)
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

        flight = self._make_flight(["EGTK", "LSGS"], departure_offset_days=-3)
        route, fetch_ts, pack_path, options, model_metadata = _prepare_refresh(
            flight, "/fake/db", "test-user", "test-flight",
        )

        assert options.fetch_gramet is False
        assert options.generate_llm_digest is False


# --- decide_refresh (tiered refresh gate, issue #167) ---


def _ms(state, covers=True, next_expected="2026-05-20T12:00:00+00:00"):
    """Build a ModelStatus for gate tests."""
    from weatherbrief.api.packs import ModelStatus

    return ModelStatus(
        source="m:openmeteo",
        pack_init=1,
        latest_available=2,
        next_expected=next_expected,
        state=state,
        covers_horizon=covers,
    )


def _status(*models, next_expected_update=None):
    """Build a DataStatus from (state, covers[, next_expected]) tuples."""
    from weatherbrief.api.packs import DataStatus

    out = {}
    for i, spec in enumerate(models):
        state, covers = spec[0], spec[1]
        nx = spec[2] if len(spec) > 2 else "2026-05-20T12:00:00+00:00"
        out[f"m{i}"] = _ms(state, covers, nx)
    stale = [m for m, ms in out.items() if ms.state == "stale"]
    return DataStatus(
        fresh=not stale,
        stale_models=stale,
        models=out,
        next_expected_update=next_expected_update,
    )


class TestRefreshThreshold:
    """_refresh_threshold: {>=2: 3, 1: 2, 0: 1}."""

    def test_thresholds(self):
        from weatherbrief.api.packs import _refresh_threshold

        assert _refresh_threshold(0) == 1
        assert _refresh_threshold(1) == 2
        assert _refresh_threshold(2) == 3
        assert _refresh_threshold(5) == 3


class TestDaysOutNow:
    # _days_out_now is date-based, so pin the hour to noon to avoid a flaky
    # day-rollover when the suite runs near UTC midnight.
    def test_days_out_from_departure(self):
        from weatherbrief.api.packs import _days_out_now

        depart = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=12)
        flight = SimpleNamespace(departure_time=depart)
        assert _days_out_now(flight) == 2

    def test_d0(self):
        from weatherbrief.api.packs import _days_out_now

        flight = SimpleNamespace(departure_time=datetime.now(timezone.utc).replace(hour=12))
        assert _days_out_now(flight) == 0


class TestDecideRefresh:
    """Matrix from issue #167 acceptance criteria.

    Threshold: D-0 needs 1 updated, D-1 needs 2, D-2+ needs 3 (capped by the
    number of models whose latest run covers the flight horizon).
    """

    def test_d2_partial_updated_is_none(self):
        from weatherbrief.api.packs import decide_refresh

        status = _status(
            ("stale", True),
            ("stale", True),
            ("awaiting", True, "2026-05-20T18:00:00+00:00"),
        )
        d = decide_refresh(status, 2)
        assert d.mode == "none"
        assert d.needed == 3
        assert d.n_updated == 2
        assert d.eta_useful == "2026-05-20T18:00:00+00:00"
        # The one not-yet-updated covering model is what we're waiting on.
        assert d.pending_models == ["m2"]

    def test_d2_all_updated_is_full(self):
        from weatherbrief.api.packs import decide_refresh

        status = _status(("stale", True), ("stale", True), ("stale", True))
        assert decide_refresh(status, 2).mode == "full"

    def test_d1_two_updated_is_full(self):
        from weatherbrief.api.packs import decide_refresh

        status = _status(("stale", True), ("stale", True), ("awaiting", True))
        assert decide_refresh(status, 1).mode == "full"

    def test_d1_one_updated_is_none(self):
        from weatherbrief.api.packs import decide_refresh

        status = _status(("stale", True), ("awaiting", True), ("awaiting", True))
        d = decide_refresh(status, 1)
        assert d.mode == "none"
        assert d.needed == 2

    def test_d0_zero_updated_is_realtime(self):
        from weatherbrief.api.packs import decide_refresh

        status = _status(("awaiting", True), ("awaiting", True), ("awaiting", True))
        assert decide_refresh(status, 0).mode == "realtime"

    def test_d0_one_updated_is_full(self):
        from weatherbrief.api.packs import decide_refresh

        status = _status(("stale", True), ("awaiting", True), ("awaiting", True))
        assert decide_refresh(status, 0).mode == "full"

    def test_two_model_selection_caps_needed(self):
        from weatherbrief.api.packs import decide_refresh

        # 2 covering models -> D-2 needs both (min(3, 2) == 2).
        one = _status(("stale", True), ("awaiting", True))
        d = decide_refresh(one, 2)
        assert d.mode == "none"
        assert d.needed == 2
        both = _status(("stale", True), ("stale", True))
        assert decide_refresh(both, 2).mode == "full"

    def test_one_model_selection_always_full_when_updated(self):
        from weatherbrief.api.packs import decide_refresh

        status = _status(("stale", True))
        assert decide_refresh(status, 2).mode == "full"
        assert decide_refresh(status, 5).mode == "full"

    def test_one_model_not_updated_d2_none_d0_realtime(self):
        from weatherbrief.api.packs import decide_refresh

        status = _status(("awaiting", True))
        assert decide_refresh(status, 2).mode == "none"
        assert decide_refresh(status, 0).mode == "realtime"

    def test_non_covering_models_excluded_from_eligible(self):
        from weatherbrief.api.packs import decide_refresh

        # Third model's latest run doesn't reach the flight -> not eligible.
        status = _status(("stale", True), ("stale", True), ("current", False))
        d = decide_refresh(status, 2)
        assert d.n_eligible == 2
        assert d.mode == "full"  # needed = min(3, 2) = 2, both eligible updated

    def test_no_eligible_models_d2_none(self):
        from weatherbrief.api.packs import decide_refresh

        status = _status(
            ("current", False), ("current", False),
            next_expected_update="2026-05-21T06:00:00+00:00",
        )
        d = decide_refresh(status, 2)
        assert d.mode == "none"
        assert d.n_eligible == 0
        assert d.eta_useful == "2026-05-21T06:00:00+00:00"

    def test_no_eligible_models_d0_realtime(self):
        from weatherbrief.api.packs import decide_refresh

        status = _status(("current", False))
        assert decide_refresh(status, 0).mode == "realtime"

    def test_eta_useful_is_kth_soonest_pending(self):
        from weatherbrief.api.packs import decide_refresh

        # D-2 needs 3, only 1 updated -> k = 2; pending next_expected sorted
        # [15:00, 18:00] -> the 2nd soonest (18:00) is when threshold is met.
        status = _status(
            ("stale", True),
            ("awaiting", True, "2026-05-20T18:00:00+00:00"),
            ("awaiting", True, "2026-05-20T15:00:00+00:00"),
        )
        d = decide_refresh(status, 2)
        assert d.mode == "none"
        assert d.eta_useful == "2026-05-20T18:00:00+00:00"
        # pending_models is sorted by next_expected (soonest first): m2 (15:00)
        # then m1 (18:00); the stale m0 is excluded.
        assert d.pending_models == ["m2", "m1"]
