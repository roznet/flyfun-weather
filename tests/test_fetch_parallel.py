"""Tests for parallel/sequential per-model fetch in run_fetch (issue #112)."""

from __future__ import annotations

import threading
import time
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


@pytest.fixture
def paid_api_key(monkeypatch):
    """Force the paid (parallel) path by setting OPENMETEO_API_KEY."""
    monkeypatch.setenv("OPENMETEO_API_KEY", "test-key")


@pytest.fixture
def free_tier(monkeypatch):
    """Force the free-tier (sequential + delay) path."""
    monkeypatch.delenv("OPENMETEO_API_KEY", raising=False)


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


def test_paid_path_runs_models_concurrently(paid_api_key):
    """All non-skipped models run in parallel — verified via threading.Barrier.

    A barrier of N parties only releases when N threads arrive. If the loop
    were sequential only one worker would ever be inside `fetch_multi_point`
    at a time and the barrier would time out.
    """
    models = [ModelSource.GFS, ModelSource.ECMWF, ModelSource.ICON]
    barrier = threading.Barrier(len(models), timeout=15.0)

    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        barrier.wait()  # raises BrokenBarrierError on timeout
        self._record_call()  # simulate one HTTP call per model
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
    # Each worker recorded one call; aggregate via thread-local sum.
    assert result.open_meteo_api_calls == 3


def test_paid_path_partial_failure(paid_api_key):
    """One model raising must not stop others from completing."""
    models = [ModelSource.GFS, ModelSource.ECMWF, ModelSource.ICON]

    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        if model == ModelSource.ECMWF:
            raise RuntimeError("simulated failure")
        self._record_call()
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
    # Two successful workers, each recorded one call; failed worker recorded none.
    assert result.open_meteo_api_calls == 2


def test_paid_path_failed_worker_calls_still_counted(paid_api_key):
    """A worker that records HTTP calls (e.g. 429 retries) and then raises
    must still contribute those calls to the aggregate. Otherwise we
    under-count traffic against the paid quota."""
    models = [ModelSource.GFS, ModelSource.ECMWF]

    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        if model == ModelSource.ECMWF:
            # Simulate 4 retry attempts that all 429'd before the worker
            # gives up — exactly the scenario that would silently drop
            # calls from the count if we didn't capture pre-raise.
            self._record_call()
            self._record_call()
            self._record_call()
            self._record_call()
            raise RuntimeError("rate limited")
        self._record_call()
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

    assert result.models_fetched == ["gfs"]
    # 1 (gfs success) + 4 (ecmwf failed retries) = 5
    assert result.open_meteo_api_calls == 5


def test_paid_path_concurrency_capped_to_model_count(paid_api_key, monkeypatch):
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


def test_free_tier_runs_sequentially_with_delay(free_tier, monkeypatch):
    """Free tier uses the sequential path with an inter-model delay so we
    don't blow through the 600/min rate limit."""
    monkeypatch.setattr(fetch_module, "_INTER_MODEL_DELAY_FREE", 0.05)
    sleep_calls: list[float] = []
    real_sleep = time.sleep

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        real_sleep(0)  # don't actually delay the test

    monkeypatch.setattr(fetch_module.time, "sleep", fake_sleep)

    # Track the order workers are dispatched in — sequential should match
    # input order strictly.
    dispatched: list[str] = []

    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        dispatched.append(model.value)
        return [_make_forecast(p, model) for p in points]

    models = [ModelSource.GFS, ModelSource.ECMWF, ModelSource.ICON]
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

    # Sequential: dispatch order matches input order.
    assert dispatched == ["gfs", "ecmwf", "icon"]
    # And so does the resulting models_fetched list (parallel path would be
    # completion order, which is non-deterministic).
    assert result.models_fetched == ["gfs", "ecmwf", "icon"]
    # 3 models → 2 inter-model delays.
    assert sleep_calls == [0.05, 0.05]


def test_free_tier_partial_failure(free_tier, monkeypatch):
    """One model failing in the free-tier path must not abort others."""
    monkeypatch.setattr(fetch_module, "_INTER_MODEL_DELAY_FREE", 0.0)

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
            models=[ModelSource.GFS, ModelSource.ECMWF, ModelSource.ICON],
            enrich_grib=False,
        )

    assert result.models_fetched == ["gfs", "icon"]
