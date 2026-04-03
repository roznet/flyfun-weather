"""Tests for airport resolution via euro_aip database."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from weatherbrief.airports import resolve_waypoints
from weatherbrief.models import Waypoint


def _mock_airport(icao: str, name: str, lat: float, lon: float):
    """Create a mock airport object matching euro_aip Airport interface."""
    airport = MagicMock()
    airport.ident = icao
    airport.name = name
    airport.latitude_deg = lat
    airport.longitude_deg = lon
    return airport


class _MockAirportsCollection:
    """Minimal mock for the airports collection used by RouteResolver."""

    def __init__(self, airports_dict: dict):
        self._airports = airports_dict

    def get(self, icao):
        return self._airports.get(icao)

    def where(self, **kwargs):
        """Mimic airports.where(ident=...).first() used by RouteResolver."""
        ident = kwargs.get("ident")
        result = self._airports.get(ident)
        return _MockQueryResult(result)


class _MockQueryResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


def _mock_model(airports_dict: dict, waypoints_dict: dict | None = None):
    """Create a mock model with airports collection and optional waypoints."""
    model = MagicMock()
    model.airports = _MockAirportsCollection(airports_dict)

    # Waypoint lookups (for get_waypoint and get_waypoint_candidates)
    _wp = waypoints_dict or {}
    model.get_waypoint.side_effect = lambda name: _wp.get(name)
    model.get_waypoint_candidates.side_effect = lambda name: _wp.get(name, [])
    return model


@patch("weatherbrief.airports._load_airport_model")
def test_resolve_waypoints(mock_load):
    """Resolves ICAO codes to Waypoints."""
    airports = {
        "EGTK": _mock_airport("EGTK", "Oxford Kidlington", 51.8361, -1.32),
        "LSGS": _mock_airport("LSGS", "Sion", 46.2192, 7.3267),
    }
    mock_load.return_value = _mock_model(airports)

    result = resolve_waypoints(["EGTK", "LSGS"], "/fake/db.sqlite")

    assert len(result) == 2
    assert result[0].icao == "EGTK"
    assert result[0].lat == 51.8361
    assert result[1].icao == "LSGS"
    assert result[1].lon == 7.3267


@patch("weatherbrief.airports._load_airport_model")
def test_resolve_waypoints_missing(mock_load):
    """Raises KeyError for unknown ICAO codes."""
    airports = {
        "EGTK": _mock_airport("EGTK", "Oxford Kidlington", 51.8361, -1.32),
    }
    mock_load.return_value = _mock_model(airports)

    with pytest.raises(KeyError, match="ZZZZ"):
        resolve_waypoints(["EGTK", "ZZZZ"], "/fake/db.sqlite")


@patch("weatherbrief.airports._load_airport_model")
def test_resolve_waypoints_no_coordinates(mock_load):
    """Raises KeyError when departure airport has no coordinates."""
    airport = _mock_airport("EGTK", "Oxford Kidlington", None, None)
    airports = {"EGTK": airport}
    mock_load.return_value = _mock_model(airports)

    with pytest.raises(KeyError, match="EGTK"):
        resolve_waypoints(["EGTK", "LSGS"], "/fake/db.sqlite")
