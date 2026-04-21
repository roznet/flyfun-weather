"""Tests for airport resolution via euro_aip database."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from airport_mocks import mock_airport, mock_model
from weatherbrief.airports import is_known_waypoint, resolve_waypoints
from weatherbrief.models import Waypoint


@patch("weatherbrief.airports._load_airport_model")
def test_resolve_waypoints(mock_load):
    """Resolves ICAO codes to Waypoints."""
    airports = {
        "EGTK": mock_airport("EGTK", "Oxford Kidlington", 51.8361, -1.32),
        "LSGS": mock_airport("LSGS", "Sion", 46.2192, 7.3267),
    }
    mock_load.return_value = mock_model(airports)

    result, rejected = resolve_waypoints(["EGTK", "LSGS"], "/fake/db.sqlite")

    assert rejected == []
    assert len(result) == 2
    assert result[0].icao == "EGTK"
    assert result[0].lat == 51.8361
    assert result[1].icao == "LSGS"
    assert result[1].lon == 7.3267


@patch("weatherbrief.airports._load_airport_model")
def test_resolve_waypoints_missing(mock_load):
    """Raises KeyError for unknown ICAO codes."""
    airports = {
        "EGTK": mock_airport("EGTK", "Oxford Kidlington", 51.8361, -1.32),
    }
    mock_load.return_value = mock_model(airports)

    with pytest.raises(KeyError, match="ZZZZ"):
        resolve_waypoints(["EGTK", "ZZZZ"], "/fake/db.sqlite")


@patch("weatherbrief.airports._load_airport_model")
def test_resolve_waypoints_no_coordinates(mock_load):
    """Raises KeyError when departure airport has no coordinates."""
    airport = mock_airport("EGTK", "Oxford Kidlington", None, None)
    airports = {"EGTK": airport}
    mock_load.return_value = mock_model(airports)

    with pytest.raises(KeyError, match="EGTK"):
        resolve_waypoints(["EGTK", "LSGS"], "/fake/db.sqlite")


# --- is_known_waypoint ---


@patch("weatherbrief.airports._load_airport_model")
def test_is_known_waypoint_found(mock_load):
    """Returns True for a known airport code."""
    airports = {
        "EGBJ": mock_airport("EGBJ", "Gloucestershire", 51.8942, -2.1672),
    }
    mock_load.return_value = mock_model(airports)

    assert is_known_waypoint("EGBJ", "/fake/db.sqlite") is True


@patch("weatherbrief.airports._load_airport_model")
def test_is_known_waypoint_not_found(mock_load):
    """Returns False for an unknown code."""
    mock_load.return_value = mock_model({})

    assert is_known_waypoint("ZZZZ", "/fake/db.sqlite") is False


@patch("weatherbrief.airports._load_airport_model")
def test_is_known_waypoint_case_insensitive(mock_load):
    """Accepts lowercase input."""
    airports = {
        "EGBJ": mock_airport("EGBJ", "Gloucestershire", 51.8942, -2.1672),
    }
    mock_load.return_value = mock_model(airports)

    assert is_known_waypoint("egbj", "/fake/db.sqlite") is True
