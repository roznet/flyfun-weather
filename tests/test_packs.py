"""Unit tests for packs helper functions (_build_route_config, _prepare_refresh)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
