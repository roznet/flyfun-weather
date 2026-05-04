"""Tests for parallel per-model fetch in run_fetch (issue #112)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    RouteConfig,
    RoutePoint,
    Waypoint,
    WaypointForecast,
)
from weatherbrief.tasks import fetch as fetch_module


def _route() -> RouteConfig:
    return RouteConfig(
        name="EGTK-LFPB",
        waypoints=[
            Waypoint(icao="EGTK", name="Oxford", lat=51.8361, lon=-1.32),
            Waypoint(icao="LFPB", name="Paris Le Bourget", lat=48.9694, lon=2.4414),
        ],
        cruise_altitude_ft=8000,
        flight_duration_hours=2.0,
    )


def _make_forecast(point: RoutePoint, model: ModelSource) -> WaypointForecast:
    wp = Waypoint(
        icao=point.waypoint_icao or f"RP{int(point.distance_from_origin_nm):03d}",
        name=point.waypoint_name or "interp",
        lat=point.lat,
        lon=point.lon,
    )
    return WaypointForecast(
        waypoint=wp,
        model=model,
        fetched_at=datetime.now(timezone.utc),
        hourly=[
            HourlyForecast(
                time=datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc),
                temperature_2m_c=10.0,
                cloud_cover_pct=30.0,
                precipitation_mm=0.0,
                freezing_level_m=2000.0,
                pressure_levels=[],
            ),
        ],
    )


def _disable_elevation():
    """Stop SRTM downloads in the test environment."""
    return patch(
        "weatherbrief.fetch.elevation.get_elevation_profile",
        side_effect=Exception("disabled in test"),
    )


def test_models_fetched_concurrently():
    """All non-skipped models run in parallel — verified via threading.Barrier.

    A barrier of N parties only releases when N threads arrive. If the loop were
    sequential, only one worker would ever be in `fetch_multi_point` at a time
    and the barrier would time out.
    """
    models = [ModelSource.GFS, ModelSource.ECMWF, ModelSource.ICON]
    barrier = threading.Barrier(len(models), timeout=5.0)

    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        barrier.wait()  # raises BrokenBarrierError on timeout
        return [_make_forecast(p, model) for p in points]

    with _disable_elevation(), patch(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        new=fake_fetch_multi_point,
    ):
        result = fetch_module.run_fetch(
            route=_route(),
            departure_time=datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc),
            models=models,
            enrich_grib=False,
        )

    assert sorted(result.models_fetched) == ["ecmwf", "gfs", "icon"]
    assert len(result.cross_sections) == 3


def test_partial_failure_does_not_abort_other_models():
    """One model raising must not stop others from completing."""
    models = [ModelSource.GFS, ModelSource.ECMWF, ModelSource.ICON]

    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        if model == ModelSource.ECMWF:
            raise RuntimeError("simulated failure")
        return [_make_forecast(p, model) for p in points]

    with _disable_elevation(), patch(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        new=fake_fetch_multi_point,
    ):
        result = fetch_module.run_fetch(
            route=_route(),
            departure_time=datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc),
            models=models,
            enrich_grib=False,
        )

    assert sorted(result.models_fetched) == ["gfs", "icon"]
    assert "ecmwf" not in result.models_fetched
    fetched_models = {cs.model for cs in result.cross_sections}
    assert fetched_models == {ModelSource.GFS, ModelSource.ICON}


def test_concurrency_capped_to_number_of_models(monkeypatch):
    """Worker count never exceeds the number of models actually being fetched."""
    seen_max_workers: list[int] = []

    real_executor = fetch_module.concurrent.futures.ThreadPoolExecutor

    class CapturingExecutor(real_executor):
        def __init__(self, max_workers=None, **kw):
            seen_max_workers.append(max_workers)
            super().__init__(max_workers=max_workers, **kw)

    monkeypatch.setattr(
        fetch_module.concurrent.futures, "ThreadPoolExecutor", CapturingExecutor,
    )
    monkeypatch.setattr(fetch_module, "_BRIEFING_FETCH_CONCURRENCY", 8)

    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        return [_make_forecast(p, model) for p in points]

    with _disable_elevation(), patch(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        new=fake_fetch_multi_point,
    ):
        fetch_module.run_fetch(
            route=_route(),
            departure_time=datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc),
            models=[ModelSource.GFS, ModelSource.ECMWF],
            enrich_grib=False,
        )

    assert seen_max_workers == [2], (
        f"Expected max_workers capped to 2 (model count), got {seen_max_workers}"
    )
