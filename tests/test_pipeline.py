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
from weatherbrief.fetch.variables import MODEL_ENDPOINTS
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
            pressure_hpa=925, temperature_c=5, relative_humidity_pct=80,
            wind_speed_kt=15, wind_direction_deg=270, geopotential_height_m=770,
        ),
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
            pressure_hpa=925, temperature_c=6, relative_humidity_pct=78,
            wind_speed_kt=12, wind_direction_deg=265, geopotential_height_m=775,
        ),
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


class TestModelCatalog:
    # Retired models kept in ModelSource enum for backward compat (old pack deserialization)
    _RETIRED_MODELS = {"best_match"}

    def test_model_source_matches_endpoints(self):
        """Every active ModelSource value has a MODEL_ENDPOINTS key and vice versa."""
        enum_values = {m.value for m in ModelSource} - self._RETIRED_MODELS
        endpoint_keys = set(MODEL_ENDPOINTS.keys())
        assert enum_values == endpoint_keys

    def test_at_least_one_default(self):
        defaults = [k for k, v in MODEL_ENDPOINTS.items() if v.default]
        assert len(defaults) >= 1


class TestAnalyzeWaypoint:
    def test_produces_wind_components(self, sample_forecasts, target_time):
        analysis = analyze_waypoint(sample_forecasts, target_time, track_deg=155.0)
        assert "gfs" in analysis.wind_components
        assert "ecmwf" in analysis.wind_components

    def test_produces_sounding_analysis(self, sample_forecasts, target_time):
        analysis = analyze_waypoint(sample_forecasts, target_time, track_deg=155.0)
        assert "gfs" in analysis.sounding
        assert "ecmwf" in analysis.sounding

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
        from weatherbrief.fetch.variables import MODEL_ENDPOINTS
        opts = BriefingOptions()
        expected = {k for k, v in MODEL_ENDPOINTS.items() if v.default}
        assert {m.value for m in opts.models} == expected
        assert opts.fetch_gramet is False
        assert opts.generate_skewt is False
        assert opts.generate_llm_digest is False
        assert opts.data_dir is None
        assert opts.output_dir is None

    def test_output_dir(self, tmp_path):
        opts = BriefingOptions(output_dir=tmp_path / "pack")
        assert opts.output_dir == tmp_path / "pack"


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
        assert result.digest is None
        assert result.diagnostics == []

    def test_digest_field_carries_object(self, tmp_path):
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
        # Simulate a WeatherDigest-like object
        class FakeDigest:
            assessment = "GREEN"
            assessment_reason = "Good weather"

        result = BriefingResult(
            snapshot=snapshot, snapshot_path=tmp_path / "snap.json",
            digest=FakeDigest(),
        )
        assert result.digest is not None
        assert result.digest.assessment == "GREEN"
        assert result.digest.assessment_reason == "Good weather"


def test_briefing_options_uses_cloud_source_not_cloud_method():
    """#410 renamed ``BriefingOptions.cloud_method`` → ``cloud_source``. Guards the
    rename-regression class (CLAUDE.md "update ALL callers"): the alt-departure
    re-run at pipeline.py section 3.1 read the stale ``options.cloud_method`` after
    the rename — an ``AttributeError`` swallowed by that block's broad try/except,
    so it failed silently. Regression for the #413 review's Critical #2.
    """
    import dataclasses

    fields = {f.name for f in dataclasses.fields(BriefingOptions)}
    assert "cloud_source" in fields
    assert "cloud_method" not in fields

    opts = BriefingOptions(cloud_source="dd")
    assert opts.cloud_source == "dd"
    with pytest.raises(TypeError):
        BriefingOptions(cloud_method="dd")  # the old kwarg is gone

    # No stale ``options.cloud_method`` reader survives anywhere in the pipeline
    # module (the missed 16-space-indented alt call was exactly this).
    import inspect

    import weatherbrief.pipeline as pipeline_mod

    assert "options.cloud_method" not in inspect.getsource(pipeline_mod)


class TestExecuteBriefingMemoryWindow:
    """``execute_briefing`` samples memory for the refresh's duration (#506).

    The point of the wrapper is that the peak reported alongside a refresh is
    measured over THAT refresh, rather than being a since-container-start
    high-water mark that happened to be read at the end of it.
    """

    def test_task_peaks_are_passed_to_the_boundary_log(self, monkeypatch):
        import weatherbrief.pipeline as pipeline_mod

        captured: dict = {}
        monkeypatch.setattr(
            pipeline_mod, "_execute_briefing_stages", lambda *a, **kw: "result",
        )
        monkeypatch.setattr(
            pipeline_mod, "_log_memory", lambda *a, **kw: captured.update(kw),
        )

        assert pipeline_mod.execute_briefing(
            route=None, departure_time=None,
        ) == "result"
        assert "task_peak_rss_mb" in captured
        assert "task_peak_cgroup_mb" in captured
        assert captured["task_peak_samples"] >= 1, (
            "start() samples synchronously, so a window always has >=1 point"
        )

    def test_memory_is_logged_even_when_the_pipeline_raises(self, monkeypatch):
        """A refresh that died is exactly the one whose peak is worth having."""
        import weatherbrief.pipeline as pipeline_mod

        calls: list[dict] = []

        def boom(*a, **kw):
            raise RuntimeError("pipeline exploded")

        monkeypatch.setattr(pipeline_mod, "_execute_briefing_stages", boom)
        monkeypatch.setattr(
            pipeline_mod, "_log_memory", lambda *a, **kw: calls.append(kw),
        )

        with pytest.raises(RuntimeError, match="pipeline exploded"):
            pipeline_mod.execute_briefing(route=None, departure_time=None)

        assert len(calls) == 1, "the memory window must close on the error path"
