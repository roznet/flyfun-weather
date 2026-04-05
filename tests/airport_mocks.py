"""Shared mock helpers for the euro_aip airport model.

Mock only the DB layer; the real RouteResolver runs against this model,
so contract violations (e.g. single-token route) are caught.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def mock_airport(icao: str, name: str, lat: float, lon: float):
    """Create a mock airport object matching euro_aip Airport interface."""
    airport = MagicMock()
    airport.ident = icao
    airport.name = name
    airport.latitude_deg = lat
    airport.longitude_deg = lon
    return airport


class MockAirportsCollection:
    """Minimal mock for the airports collection used by RouteResolver."""

    def __init__(self, airports_dict: dict):
        self._airports = airports_dict

    def get(self, icao):
        return self._airports.get(icao)

    def where(self, **kwargs):
        ident = kwargs.get("ident")
        result = self._airports.get(ident)
        return _MockQueryResult(result)


class _MockQueryResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


def mock_model(airports_dict: dict, waypoints_dict: dict | None = None):
    """Create a mock model with airports collection and optional waypoints."""
    model = MagicMock()
    model.airports = MockAirportsCollection(airports_dict)
    _wp = waypoints_dict or {}
    model.get_waypoint.side_effect = lambda name: _wp.get(name)
    model.get_waypoint_candidates.side_effect = lambda name: _wp.get(name, [])
    return model


TEST_AIRPORTS = {
    "EGBJ": mock_airport("EGBJ", "Gloucestershire", 51.8942, -2.1672),
    "LFOV": mock_airport("LFOV", "Laval-Entrammes", 48.0314, -0.7428),
    "LFPB": mock_airport("LFPB", "Paris Le Bourget", 48.9694, 2.4414),
    "EGTK": mock_airport("EGTK", "Oxford Kidlington", 51.8361, -1.32),
    "LSGS": mock_airport("LSGS", "Sion", 46.2192, 7.3267),
}
