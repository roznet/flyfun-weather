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


def _mock_model(airports_dict: dict):
    """Create a mock model with airports collection."""
    model = MagicMock()

    def get_airport(icao):
        return airports_dict.get(icao)

    model.airports.get = get_airport
    return model


@patch("weatherbrief.airports.DatabaseStorage")
def test_resolve_waypoints(mock_storage_cls):
    """Resolves ICAO codes to Waypoints."""
    airports = {
        "EGTK": _mock_airport("EGTK", "Oxford Kidlington", 51.8361, -1.32),
        "LSGS": _mock_airport("LSGS", "Sion", 46.2192, 7.3267),
    }
    mock_storage_cls.return_value.load_model.return_value = _mock_model(airports)

    result = resolve_waypoints(["EGTK", "LSGS"], "/fake/db.sqlite")

    assert len(result) == 2
    assert result[0].icao == "EGTK"
    assert result[0].lat == 51.8361
    assert result[1].icao == "LSGS"
    assert result[1].lon == 7.3267


@patch("weatherbrief.airports.DatabaseStorage")
def test_resolve_waypoints_missing(mock_storage_cls):
    """Raises KeyError for unknown ICAO codes."""
    airports = {
        "EGTK": _mock_airport("EGTK", "Oxford Kidlington", 51.8361, -1.32),
    }
    mock_storage_cls.return_value.load_model.return_value = _mock_model(airports)

    with pytest.raises(KeyError, match="ZZZZ"):
        resolve_waypoints(["EGTK", "ZZZZ"], "/fake/db.sqlite")


@patch("weatherbrief.airports.DatabaseStorage")
def test_resolve_waypoints_no_coordinates(mock_storage_cls):
    """Raises KeyError when airport has no coordinates."""
    airport = _mock_airport("EGTK", "Oxford Kidlington", None, None)
    airports = {"EGTK": airport}
    mock_storage_cls.return_value.load_model.return_value = _mock_model(airports)

    with pytest.raises(KeyError, match="no coordinates"):
        resolve_waypoints(["EGTK"], "/fake/db.sqlite")
