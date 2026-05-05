"""Tests for parallel chunk fetch in standalone_verification (issue #110).

Mirrors the briefing-side test_fetch_parallel.py pattern: verify chunks run
concurrently (via threading.Barrier), survive partial failures, and cap
worker count to chunk count.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from weatherbrief.models import HourlyForecast, ModelSource, Waypoint, WaypointForecast
from weatherbrief.tasks import standalone_verification as sv
from weatherbrief.tasks.airport_watchlist import WatchlistAirport


def _airport(icao: str) -> WatchlistAirport:
    return WatchlistAirport(icao=icao, lat=50.0, lon=2.0)


def _airports(n: int) -> list[WatchlistAirport]:
    return [_airport(f"EG{i:02d}") for i in range(n)]


def _empty_forecast(icao: str) -> WaypointForecast:
    """A WaypointForecast with no hourly samples — keeps tests focused on
    chunk-level concurrency rather than per-snapshot parsing."""
    return WaypointForecast(
        waypoint=Waypoint(icao=icao, name=icao, lat=50.0, lon=2.0),
        model=ModelSource.GFS,
        fetched_at=datetime.now(timezone.utc),
        hourly=[],
    )


def _forecast_with_one_sample(icao: str) -> WaypointForecast:
    """One hourly sample at a SAMPLE_HOURS_UTC hour so it survives the filter
    and produces exactly one snapshot dict per airport."""
    return WaypointForecast(
        waypoint=Waypoint(icao=icao, name=icao, lat=50.0, lon=2.0),
        model=ModelSource.GFS,
        fetched_at=datetime.now(timezone.utc),
        hourly=[
            HourlyForecast(
                time=datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc),
                temperature_2m_c=10.0,
                cloud_cover_pct=30.0,
                precipitation_mm=0.0,
                pressure_levels=[],
            ),
        ],
    )


@pytest.fixture
def small_chunk_size(monkeypatch):
    """Force 4 airports → 4 single-airport chunks so we can drive concurrency
    behaviour without 100s of fixture rows."""
    monkeypatch.setattr(sv, "_OPEN_METEO_BATCH_SIZE", 1)


def test_chunks_dispatch_concurrently(small_chunk_size, monkeypatch):
    """Chunks must run in parallel — verified via threading.Barrier.

    A barrier of N parties only releases when N threads arrive. If the loop
    were sequential only one worker would ever be inside `fetch_multi_point`
    at a time and the barrier would time out.
    """
    airports = _airports(4)
    monkeypatch.setattr(sv, "_OPEN_METEO_CONCURRENCY", 4)
    barrier = threading.Barrier(len(airports), timeout=15.0)

    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        barrier.wait()  # raises BrokenBarrierError on timeout
        self._record_call()
        return [_empty_forecast(p.waypoint_icao) for p in points]

    with patch(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        new=fake_fetch_multi_point,
    ):
        snaps, calls = sv._fetch_forecasts_for_model(
            "gfs",
            datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc),
            airports,
            session=None,
        )

    # 4 chunks × 1 airport, but no hourly samples → 0 snapshots; we still
    # verify that all chunks completed by checking the call count.
    assert snaps == []
    assert calls == 4


def test_aggregated_snapshot_count_matches_sequential(small_chunk_size, monkeypatch):
    """Output count under parallel dispatch matches what the sequential code
    would produce — no chunk dropped, no duplicates."""
    airports = _airports(4)
    monkeypatch.setattr(sv, "_OPEN_METEO_CONCURRENCY", 4)

    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        self._record_call()
        return [_forecast_with_one_sample(p.waypoint_icao) for p in points]

    with patch(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        new=fake_fetch_multi_point,
    ):
        snaps, calls = sv._fetch_forecasts_for_model(
            "gfs",
            datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc),
            airports,
            session=None,
        )

    icaos = sorted(s["icao"] for s in snaps)
    assert icaos == ["EG00", "EG01", "EG02", "EG03"]
    assert calls == 4


def test_partial_chunk_failure_does_not_abort_others(small_chunk_size, monkeypatch):
    """One chunk's retry-exhausted failure must not stop the others from
    completing — partial success beats total failure."""
    airports = _airports(4)
    monkeypatch.setattr(sv, "_OPEN_METEO_CONCURRENCY", 4)

    # Don't actually wait between retries.
    monkeypatch.setattr(sv.time, "sleep", lambda _: None)

    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        if any(p.waypoint_icao == "EG02" for p in points):
            self._record_call()
            raise RuntimeError("simulated 500")
        self._record_call()
        return [_forecast_with_one_sample(p.waypoint_icao) for p in points]

    with patch(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        new=fake_fetch_multi_point,
    ):
        snaps, calls = sv._fetch_forecasts_for_model(
            "gfs",
            datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc),
            airports,
            session=None,
        )

    icaos = sorted(s["icao"] for s in snaps)
    assert icaos == ["EG00", "EG01", "EG03"]
    # 3 successful chunks + 3 retry attempts on the failing chunk = 6
    assert calls == 6


def test_concurrency_capped_to_chunk_count(small_chunk_size, monkeypatch):
    """Worker count never exceeds the actual number of chunks."""
    airports = _airports(2)
    monkeypatch.setattr(sv, "_OPEN_METEO_CONCURRENCY", 8)

    seen_max_workers: list[int] = []
    real_executor = sv.concurrent.futures.ThreadPoolExecutor

    class CapturingExecutor(real_executor):
        def __init__(self, max_workers=None, **kw):
            seen_max_workers.append(max_workers)
            super().__init__(max_workers=max_workers, **kw)

    monkeypatch.setattr(
        sv.concurrent.futures, "ThreadPoolExecutor", CapturingExecutor,
    )

    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        return [_empty_forecast(p.waypoint_icao) for p in points]

    with patch(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        new=fake_fetch_multi_point,
    ):
        sv._fetch_forecasts_for_model(
            "gfs",
            datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc),
            airports,
            session=None,
        )

    assert seen_max_workers == [2], (
        f"Expected max_workers capped to 2 (chunk count), got {seen_max_workers}"
    )


def test_empty_airport_list_returns_no_snapshots(monkeypatch):
    """Empty input must short-circuit cleanly without spinning up an executor."""
    monkeypatch.setattr(sv, "_OPEN_METEO_CONCURRENCY", 4)

    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        raise AssertionError("fetch_multi_point must not be called for empty input")

    with patch(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        new=fake_fetch_multi_point,
    ):
        snaps, calls = sv._fetch_forecasts_for_model(
            "gfs",
            datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc),
            airports=[],
            session=None,
        )

    assert snaps == []
    assert calls == 0
