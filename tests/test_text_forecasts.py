"""Tests for unified text forecast dispatcher and region detection."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from weatherbrief.fetch.text_forecasts import (
    ForecastRegion,
    TextForecasts,
    detect_region,
    fetch_text_forecasts,
)
from weatherbrief.models import RouteConfig, Waypoint


# Destination coordinates: the region comes from where it is, not its name.
_DEST_COORDS: dict[str, tuple[float, float]] = {
    "KLAX": (33.9416, -118.4085),
    "PHNL": (21.3187, -157.9225),
    "PANC": (61.1743, -149.9963),
    "EGTK": (51.8369, -1.3200),
    "LFPB": (48.9694, 2.4414),
    "LSGS": (46.2196, 7.3268),
    "PEPUL": (52.2675, -1.8873),
}


def _route(dest_icao: str) -> RouteConfig:
    """Build a minimal route with just a destination."""
    lat, lon = _DEST_COORDS[dest_icao]
    return RouteConfig(
        name="test",
        waypoints=[
            Waypoint(icao="KSFX", name="Start", lat=40.0, lon=-111.0),
            Waypoint(icao=dest_icao, name="Dest", lat=lat, lon=lon),
        ],
    )


# --- Region detection ---


def test_detect_region_k_prefix():
    """K-prefix destination → US."""
    assert detect_region(_route("KLAX")) == ForecastRegion.US


def test_detect_region_p_prefix():
    """P-prefix destination (Alaska/Hawaii) → US."""
    assert detect_region(_route("PHNL")) == ForecastRegion.US
    assert detect_region(_route("PANC")) == ForecastRegion.US


def test_detect_region_e_prefix():
    """E-prefix destination → Europe."""
    assert detect_region(_route("EGTK")) == ForecastRegion.EUROPE


def test_detect_region_l_prefix():
    """L-prefix destination → Europe."""
    assert detect_region(_route("LFPB")) == ForecastRegion.EUROPE
    assert detect_region(_route("LSGS")) == ForecastRegion.EUROPE


def test_detect_region_p_named_uk_fix_is_europe():
    """A UK destination fix starting with P is Europe (prod 2026-10-26 EGBO→PEPUL)."""
    assert detect_region(_route("PEPUL")) == ForecastRegion.EUROPE


def test_detect_region_mixed_route_uses_destination():
    """Mixed-prefix route → uses last waypoint (destination)."""
    route = RouteConfig(
        name="test",
        waypoints=[
            Waypoint(icao="EGTK", name="Oxford", lat=51.8, lon=-1.3),
            Waypoint(icao="KLAX", name="LA", lat=33.9, lon=-118.4),
        ],
    )
    assert detect_region(route) == ForecastRegion.US


def test_detect_region_none_route():
    """None route → Europe default."""
    assert detect_region(None) == ForecastRegion.EUROPE


# --- Dispatcher ---


@patch("weatherbrief.fetch.text_forecasts._fetch_us_text")
def test_dispatch_us_route(mock_us):
    """US route dispatches to NWS fetcher."""
    mock_us.return_value = TextForecasts(
        region=ForecastRegion.US,
        source_label="NWS AFD",
        language_note="English",
        entries=[],
        fetched_at=datetime.now(timezone.utc),
    )

    result = fetch_text_forecasts(route=_route("KLAX"))

    mock_us.assert_called_once()
    assert result.region == ForecastRegion.US


@patch("weatherbrief.fetch.text_forecasts._fetch_eu_text")
def test_dispatch_eu_route(mock_eu):
    """EU route dispatches to DWD fetcher."""
    mock_eu.return_value = TextForecasts(
        region=ForecastRegion.EUROPE,
        source_label="DWD Synoptic Overview",
        language_note="German — translate relevant content",
        entries=[],
        fetched_at=datetime.now(timezone.utc),
    )

    result = fetch_text_forecasts(route=_route("EGTK"))

    mock_eu.assert_called_once()
    assert result.region == ForecastRegion.EUROPE


@patch("weatherbrief.fetch.text_forecasts._fetch_eu_text")
def test_dispatch_none_route_defaults_to_eu(mock_eu):
    """None route → DWD (Europe default)."""
    mock_eu.return_value = TextForecasts(
        region=ForecastRegion.EUROPE,
        source_label="DWD Synoptic Overview",
        language_note="German — translate relevant content",
        entries=[],
        fetched_at=datetime.now(timezone.utc),
    )

    result = fetch_text_forecasts(route=None)

    mock_eu.assert_called_once()
    assert result.region == ForecastRegion.EUROPE
