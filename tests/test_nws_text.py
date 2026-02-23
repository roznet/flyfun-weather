"""Tests for NWS Area Forecast Discussion fetcher."""

from __future__ import annotations

import json

import responses

from weatherbrief.fetch.nws_text import (
    AWC_AFD_URL,
    NWS_POINTS_URL,
    NWSTextForecasts,
    _cwa_to_icao_prefix,
    fetch_nws_afd,
)
from weatherbrief.models import Waypoint

SAMPLE_AFD = """\
FXUS66 KMTR 101200
AFDMTR

AREA FORECAST DISCUSSION
National Weather Service San Francisco Bay Area
4 AM PST Mon Feb 10 2026

.SYNOPSIS...
High pressure continues to dominate the region.

.AVIATION...
VFR conditions expected through the forecast period.
"""

# NWS points API response shape
POINTS_RESPONSE_MTR = {
    "properties": {
        "cwa": "MTR",
        "gridId": "MTR",
        "gridX": 92,
        "gridY": 105,
    }
}

POINTS_RESPONSE_LOX = {
    "properties": {
        "cwa": "LOX",
        "gridId": "LOX",
        "gridX": 148,
        "gridY": 45,
    }
}


def _wp(icao: str, lat: float, lon: float) -> Waypoint:
    return Waypoint(icao=icao, name=icao, lat=lat, lon=lon)


@responses.activate
def test_fetch_nws_afd_success(tmp_path):
    """Full success: WFO lookup + AFD fetch for two unique CWAs."""
    responses.add(
        responses.GET,
        NWS_POINTS_URL.format(lat="37.6213", lon="-122.3790"),
        json=POINTS_RESPONSE_MTR,
        status=200,
    )
    responses.add(
        responses.GET,
        NWS_POINTS_URL.format(lat="33.9425", lon="-118.4081"),
        json=POINTS_RESPONSE_LOX,
        status=200,
    )
    responses.add(
        responses.GET,
        AWC_AFD_URL.format(cwa="KMTR"),
        body=SAMPLE_AFD,
        status=200,
    )
    responses.add(
        responses.GET,
        AWC_AFD_URL.format(cwa="KLOX"),
        body="FXUS66 KLOX 101200\nAFDLOX\n\nAREA FORECAST DISCUSSION\nNational Weather Service Los Angeles\n4 AM PST Mon Feb 10 2026\n\n.SYNOPSIS...\nOnshore flow continues.",
        status=200,
    )

    wps = [
        _wp("KSFO", 37.6213, -122.3790),
        _wp("KLAX", 33.9425, -118.4081),
    ]

    result = fetch_nws_afd(wps, cache_dir=tmp_path)

    assert isinstance(result, NWSTextForecasts)
    assert "MTR" in result.afd_texts
    assert "LOX" in result.afd_texts
    assert "SYNOPSIS" in result.afd_texts["MTR"]
    assert result.fetched_at is not None


@responses.activate
def test_wfo_lookup_failure_graceful(tmp_path):
    """WFO lookup fails for one waypoint — still fetches the other."""
    responses.add(
        responses.GET,
        NWS_POINTS_URL.format(lat="37.6213", lon="-122.3790"),
        json={"status": 500},
        status=500,
    )
    responses.add(
        responses.GET,
        NWS_POINTS_URL.format(lat="33.9425", lon="-118.4081"),
        json=POINTS_RESPONSE_LOX,
        status=200,
    )
    responses.add(
        responses.GET,
        AWC_AFD_URL.format(cwa="KLOX"),
        body="FXUS66 KLOX 101200\nAFDLOX\n\nAREA FORECAST DISCUSSION\nNational Weather Service Los Angeles\n4 AM PST Mon Feb 10 2026\n\n.SYNOPSIS...\nMarine layer persists.",
        status=200,
    )

    wps = [
        _wp("KSFO", 37.6213, -122.3790),
        _wp("KLAX", 33.9425, -118.4081),
    ]

    result = fetch_nws_afd(wps, cache_dir=tmp_path)

    assert "MTR" not in result.afd_texts
    assert "LOX" in result.afd_texts


@responses.activate
def test_afd_fetch_failure_graceful(tmp_path):
    """AFD fetch fails — CWA omitted from results, no exception."""
    responses.add(
        responses.GET,
        NWS_POINTS_URL.format(lat="37.6213", lon="-122.3790"),
        json=POINTS_RESPONSE_MTR,
        status=200,
    )
    responses.add(
        responses.GET,
        AWC_AFD_URL.format(cwa="KMTR"),
        body="Server Error",
        status=500,
    )

    wps = [_wp("KSFO", 37.6213, -122.3790)]
    result = fetch_nws_afd(wps, cache_dir=tmp_path)

    assert result.afd_texts == {}


@responses.activate
def test_cwa_deduplication(tmp_path):
    """Two airports in the same CWA → only one AFD fetch."""
    # Both KSFO and KOAK are in CWA MTR
    responses.add(
        responses.GET,
        NWS_POINTS_URL.format(lat="37.6213", lon="-122.3790"),
        json=POINTS_RESPONSE_MTR,
        status=200,
    )
    responses.add(
        responses.GET,
        NWS_POINTS_URL.format(lat="37.7213", lon="-122.2208"),
        json=POINTS_RESPONSE_MTR,
        status=200,
    )
    responses.add(
        responses.GET,
        AWC_AFD_URL.format(cwa="KMTR"),
        body=SAMPLE_AFD,
        status=200,
    )

    wps = [
        _wp("KSFO", 37.6213, -122.3790),
        _wp("KOAK", 37.7213, -122.2208),
    ]

    result = fetch_nws_afd(wps, cache_dir=tmp_path)

    assert len(result.afd_texts) == 1
    assert "MTR" in result.afd_texts
    # Only 1 AFD request should have been made (2 points + 1 AFD = 3 total)
    afd_calls = [c for c in responses.calls if "fcstdisc" in c.request.url]
    assert len(afd_calls) == 1


@responses.activate
def test_wfo_cache_persistence(tmp_path):
    """Cache is saved and reloaded — second call skips WFO lookup."""
    # First call: WFO lookup needed
    responses.add(
        responses.GET,
        NWS_POINTS_URL.format(lat="37.6213", lon="-122.3790"),
        json=POINTS_RESPONSE_MTR,
        status=200,
    )
    responses.add(
        responses.GET,
        AWC_AFD_URL.format(cwa="KMTR"),
        body=SAMPLE_AFD,
        status=200,
    )

    wps = [_wp("KSFO", 37.6213, -122.3790)]
    fetch_nws_afd(wps, cache_dir=tmp_path)

    # Verify cache file was written
    cache_file = tmp_path / "airport_wfo_cache.json"
    assert cache_file.exists()
    cache_data = json.loads(cache_file.read_text())
    assert cache_data["KSFO"] == "MTR"

    # Second call: only AFD fetch needed (no WFO lookup)
    responses.reset()
    responses.add(
        responses.GET,
        AWC_AFD_URL.format(cwa="KMTR"),
        body=SAMPLE_AFD,
        status=200,
    )

    result = fetch_nws_afd(wps, cache_dir=tmp_path)
    assert "MTR" in result.afd_texts
    # No points API calls made
    points_calls = [c for c in responses.calls if "points" in c.request.url]
    assert len(points_calls) == 0


def test_cwa_to_icao_prefix_conus():
    """CONUS CWAs get K prefix."""
    assert _cwa_to_icao_prefix("MTR") == "KMTR"
    assert _cwa_to_icao_prefix("LOX") == "KLOX"
    assert _cwa_to_icao_prefix("OKX") == "KOKX"


def test_cwa_to_icao_prefix_alaska():
    """Alaska CWAs get PA prefix."""
    assert _cwa_to_icao_prefix("AFG") == "PAAFG"
    assert _cwa_to_icao_prefix("AJK") == "PAAJK"


def test_cwa_to_icao_prefix_hawaii():
    """Hawaii CWA gets PH prefix."""
    assert _cwa_to_icao_prefix("HFO") == "PHHFO"


def test_cwa_to_icao_prefix_puerto_rico():
    """Puerto Rico CWA gets TJ prefix."""
    assert _cwa_to_icao_prefix("SJU") == "TJSJU"


@responses.activate
def test_fetch_no_cache_dir():
    """Works without a cache_dir (no caching)."""
    # WFO lookup fails → empty result
    responses.add(
        responses.GET,
        NWS_POINTS_URL.format(lat="37.6213", lon="-122.3790"),
        json={"status": 500},
        status=500,
    )

    wps = [_wp("KSFO", 37.6213, -122.3790)]
    result = fetch_nws_afd(wps, cache_dir=None)
    assert isinstance(result, NWSTextForecasts)
    assert result.afd_texts == {}
