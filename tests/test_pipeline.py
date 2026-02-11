"""Tests for the pipeline module."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    PressureLevelData,
    RouteConfig,
    Waypoint,
    WaypointForecast,
)
from weatherbrief.pipeline import BriefingOptions, BriefingResult, analyze_waypoint


@pytest.fixture
def target_time():
    return datetime(2026, 2, 21, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_forecasts(target_time):
    """Two model forecasts for the same waypoint."""
    wp = Waypoint(icao="EGTK", name="Oxford Kidlington", lat=51.8361, lon=-1.32)

    levels = [
        PressureLevelData(
            pressure_hpa=850, temperature_c=0, relative_humidity_pct=90,
            wind_speed_kt=25, wind_direction_deg=280, geopotential_height_m=1450,
        ),
        PressureLevelData(
            pressure_hpa=700, temperature_c=-8, relative_humidity_pct=60,
            wind_speed_kt=35, wind_direction_deg=300, geopotential_height_m=3010,
        ),
    ]

    hourly = HourlyForecast(
        time=target_time,
        temperature_2m_c=5.0,
        cloud_cover_pct=40.0,
        precipitation_mm=0.0,
        freezing_level_m=1500.0,
        pressure_levels=levels,
    )

    levels2 = [
        PressureLevelData(
            pressure_hpa=850, temperature_c=1, relative_humidity_pct=85,
            wind_speed_kt=20, wind_direction_deg=270, geopotential_height_m=1460,
        ),
        PressureLevelData(
            pressure_hpa=700, temperature_c=-7, relative_humidity_pct=55,
            wind_speed_kt=30, wind_direction_deg=290, geopotential_height_m=3020,
        ),
    ]

    hourly2 = HourlyForecast(
        time=target_time,
        temperature_2m_c=5.5,
        cloud_cover_pct=35.0,
        precipitation_mm=0.0,
        freezing_level_m=1600.0,
        pressure_levels=levels2,
    )

    return [
        WaypointForecast(
            waypoint=wp, model=ModelSource.GFS,
            fetched_at=datetime.now(tz=timezone.utc), hourly=[hourly],
        ),
        WaypointForecast(
            waypoint=wp, model=ModelSource.ECMWF,
            fetched_at=datetime.now(tz=timezone.utc), hourly=[hourly2],
        ),
    ]


class TestAnalyzeWaypoint:
    def test_produces_wind_components(self, sample_forecasts, target_time):
        analysis = analyze_waypoint(sample_forecasts, target_time, track_deg=155.0)
        assert "gfs" in analysis.wind_components
        assert "ecmwf" in analysis.wind_components

    def test_produces_icing_bands(self, sample_forecasts, target_time):
        analysis = analyze_waypoint(sample_forecasts, target_time, track_deg=155.0)
        assert "gfs" in analysis.icing_bands
        assert "ecmwf" in analysis.icing_bands

    def test_produces_cloud_layers(self, sample_forecasts, target_time):
        analysis = analyze_waypoint(sample_forecasts, target_time, track_deg=155.0)
        assert "gfs" in analysis.cloud_layers
        assert "ecmwf" in analysis.cloud_layers

    def test_produces_model_divergence(self, sample_forecasts, target_time):
        analysis = analyze_waypoint(sample_forecasts, target_time, track_deg=155.0)
        # Should have comparison for temperature, wind, cloud, precip, freezing
        assert len(analysis.model_divergence) >= 2
        var_names = {d.variable for d in analysis.model_divergence}
        assert "temperature_c" in var_names
        assert "wind_speed_kt" in var_names

    def test_raises_on_empty_forecasts(self, target_time):
        with pytest.raises(ValueError, match="No forecasts"):
            analyze_waypoint([], target_time, track_deg=155.0)

    def test_single_model_no_divergence(self, sample_forecasts, target_time):
        analysis = analyze_waypoint(
            sample_forecasts[:1], target_time, track_deg=155.0
        )
        assert len(analysis.model_divergence) == 0


class TestBriefingOptions:
    def test_defaults(self):
        opts = BriefingOptions()
        assert len(opts.models) == 3
        assert opts.fetch_gramet is False
        assert opts.generate_skewt is False
        assert opts.generate_llm_digest is False
        assert opts.data_dir is None


class TestBriefingResult:
    def test_defaults(self, tmp_path):
        from weatherbrief.models import ForecastSnapshot, RouteConfig, Waypoint

        route = RouteConfig(
            name="test",
            waypoints=[
                Waypoint(icao="EGTK", name="Oxford", lat=51.8, lon=-1.3),
                Waypoint(icao="LFPB", name="Paris", lat=48.9, lon=2.4),
            ],
        )
        snapshot = ForecastSnapshot(
            route=route, target_date="2026-02-21",
            fetch_date="2026-02-19", days_out=2,
        )
        result = BriefingResult(snapshot=snapshot, snapshot_path=tmp_path / "snap.json")
        assert result.gramet_path is None
        assert result.skewt_paths == []
        assert result.digest_path is None
        assert result.errors == []
