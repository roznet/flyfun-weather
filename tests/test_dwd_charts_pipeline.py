"""Tests for the run_dwd_charts pipeline task.

These hit the eligibility branches and the report→result mapping; the
actual HTTP fetch is mocked via ``responses`` to keep tests offline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import responses

from weatherbrief.fetch.dwd_charts import CHART_IDS, DWD_BASE_URL, _FILENAMES
from weatherbrief.models import RouteConfig, Waypoint
from weatherbrief.tasks.dwd_charts import run_dwd_charts


_NOW = datetime.now(timezone.utc)
_PNG = b"\x89PNG\r\n\x1a\n" + b"z" * 64


def _eu_route() -> RouteConfig:
    return RouteConfig(
        name="EGTK_LSGS",
        waypoints=[
            Waypoint(icao="EGTK", name="Oxford", lat=51.8, lon=-1.3),
            Waypoint(icao="LSGS", name="Sion", lat=46.2, lon=7.3),
        ],
        cruise_altitude_ft=8000,
        flight_duration_hours=4.5,
    )


def _us_route() -> RouteConfig:
    return RouteConfig(
        name="KSFO_KLAX",
        waypoints=[
            Waypoint(icao="KSFO", name="San Francisco", lat=37.6, lon=-122.4),
            Waypoint(icao="KLAX", name="Los Angeles", lat=33.9, lon=-118.4),
        ],
        cruise_altitude_ft=8000,
        flight_duration_hours=1.0,
    )


def _seed_responses(last_modified: str = "Wed, 08 May 2026 06:30:00 GMT"):
    for cid in CHART_IDS:
        responses.add(
            responses.GET,
            f"{DWD_BASE_URL}/{_FILENAMES[cid]}",
            body=_PNG,
            status=200,
            headers={"Last-Modified": last_modified, "ETag": f'"{cid}-1"'},
        )


def test_run_dwd_charts_us_route_skips_fetch(tmp_path: Path):
    # No responses registered — if we attempted any HTTP call, the test
    # would error out via responses.
    result = run_dwd_charts(
        route=_us_route(),
        departure_time=_NOW + timedelta(hours=24),
        data_dir=tmp_path,
    )
    assert result.in_coverage is False
    assert result.run_cycle is None
    assert result.default_chart_id is None


@responses.activate
def test_run_dwd_charts_eu_route_populates_fields(tmp_path: Path):
    issued = datetime(2026, 5, 8, 6, 0, tzinfo=timezone.utc)
    _seed_responses("Wed, 08 May 2026 06:30:00 GMT")

    # ETD ~48h after issuance → expect default_chart_id "048"
    etd = issued + timedelta(hours=48)
    result = run_dwd_charts(
        route=_eu_route(),
        departure_time=etd,
        data_dir=tmp_path,
    )

    assert result.in_coverage is True
    assert result.within_horizon is True
    assert result.run_cycle == "2026-05-08T06Z"
    assert result.default_chart_id == "048"


@responses.activate
def test_run_dwd_charts_eu_route_beyond_horizon(tmp_path: Path):
    issued = datetime(2026, 5, 8, 6, 0, tzinfo=timezone.utc)
    _seed_responses("Wed, 08 May 2026 06:30:00 GMT")

    # ETD 200h ahead — beyond +108h
    etd = issued + timedelta(hours=200)
    result = run_dwd_charts(
        route=_eu_route(),
        departure_time=etd,
        data_dir=tmp_path,
    )

    assert result.in_coverage is True
    assert result.within_horizon is False
    # Cycle is still recorded for debugging, but no default chart picked
    assert result.run_cycle == "2026-05-08T06Z"
    assert result.default_chart_id is None


@responses.activate
def test_run_dwd_charts_analysis_5xx_marks_unavailable(tmp_path: Path):
    # Analysis 500 — no other charts queried; result should mark unavailable.
    responses.add(
        responses.GET,
        f"{DWD_BASE_URL}/{_FILENAMES['ana']}",
        status=500,
        body=b"",
    )
    result = run_dwd_charts(
        route=_eu_route(),
        departure_time=_NOW + timedelta(hours=24),
        data_dir=tmp_path,
    )
    assert result.in_coverage is True
    assert result.within_horizon is False
    assert result.run_cycle is None


def _seed_split_run_responses():
    """Analysis rolled to 18Z; the forecasts are the 00Z run, published 05:18."""
    for cid in CHART_IDS:
        lm = "Mon, 14 Sep 2026 18:30:00 GMT" if cid == "ana" else "Mon, 14 Sep 2026 05:18:13 GMT"
        responses.add(
            responses.GET,
            f"{DWD_BASE_URL}/{_FILENAMES[cid]}",
            body=_PNG,
            status=200,
            headers={"Last-Modified": lm, "ETag": f'"{cid}-1"'},
        )


@responses.activate
def test_run_dwd_charts_defaults_by_the_forecasts_own_run(tmp_path: Path):
    _seed_split_run_responses()
    # 18Z two days on: +60h of the 00Z run (valid 12Z) is nearest. The cycle key
    # plus offset would have picked +48h, valid 00Z that day.
    result = run_dwd_charts(
        route=_eu_route(),
        departure_time=datetime(2026, 9, 16, 18, tzinfo=timezone.utc),
        data_dir=tmp_path,
    )
    assert result.run_cycle == "2026-09-14T18Z"
    assert result.default_chart_id == "060"


@responses.activate
def test_run_dwd_charts_horizon_ends_at_the_forecast_runs_108h(tmp_path: Path):
    _seed_split_run_responses()
    # 00Z run + 108h = 18 Sep 12Z. The cycle key (18Z) + 108h would still have
    # admitted this 19 Sep 06Z departure.
    result = run_dwd_charts(
        route=_eu_route(),
        departure_time=datetime(2026, 9, 19, 6, tzinfo=timezone.utc),
        data_dir=tmp_path,
    )
    assert result.in_coverage is True
    assert result.within_horizon is False
