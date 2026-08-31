"""Tests for the Météo-France TEMSI pipeline task and its serving gates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from weatherbrief.fetch.meteofrance_charts import CHART_IDS
from weatherbrief.models.analysis import RouteConfig, Waypoint
from weatherbrief.tasks.meteofrance_charts import run_meteofrance_charts

FRENCH = RouteConfig(
    name="fr",
    waypoints=[Waypoint(icao="LFPG", name="LFPG", lat=49.010, lon=2.548),
               Waypoint(icao="LFML", name="LFML", lat=43.438, lon=5.213)],
)
BRITISH = RouteConfig(
    name="gb",
    waypoints=[Waypoint(icao="EGLL", name="EGLL", lat=51.470, lon=-0.454),
               Waypoint(icao="EGCC", name="EGCC", lat=53.354, lon=-2.275)],
)

ETD = datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def cached(tmp_path: Path, monkeypatch):
    """A cache holding both zones at 15Z, with the network refresh stubbed."""
    from weatherbrief.fetch.chart_cache import RefreshReport
    from weatherbrief.tasks import meteofrance_charts as task_mod

    for zone in CHART_IDS:
        d = tmp_path / "meteofrance_charts" / "2026-08-31T15Z"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{zone}.png").write_bytes(b"\x89PNG stub")

    monkeypatch.setattr(task_mod, "enabled", lambda: True)
    monkeypatch.setattr(
        task_mod, "refresh_charts",
        lambda data_dir, **kw: RefreshReport(run_cycle="2026-08-31T15Z"),
    )
    return tmp_path


def test_selects_a_validity_per_zone(cached: Path):
    res = run_meteofrance_charts(route=FRENCH, departure_time=ETD, data_dir=cached)
    assert res.in_coverage is True
    assert res.within_horizon is True
    assert res.zone_cycles == {z: "2026-08-31T15Z" for z in CHART_IDS}


def test_non_french_route_gets_nothing(cached: Path, monkeypatch):
    """The licence gate, not a coverage gate — the bytes are cached and still withheld."""
    called = []
    from weatherbrief.tasks import meteofrance_charts as task_mod
    monkeypatch.setattr(task_mod, "refresh_charts",
                        lambda *a, **k: called.append(1))

    res = run_meteofrance_charts(route=BRITISH, departure_time=ETD, data_dir=cached)
    assert res.in_coverage is False
    assert res.zone_cycles == {}
    assert called == []  # never even refreshed


def test_beyond_horizon_is_distinct_from_out_of_coverage(cached: Path):
    """A next-day briefing is eligible but has no chart — the UI must tell them apart."""
    res = run_meteofrance_charts(
        route=FRENCH, departure_time=ETD + timedelta(days=1), data_dir=cached,
    )
    assert res.in_coverage is True
    assert res.within_horizon is False
    assert res.zone_cycles == {}


def test_unconfigured_source_is_silent(tmp_path: Path, monkeypatch):
    from weatherbrief.tasks import meteofrance_charts as task_mod
    monkeypatch.setattr(task_mod, "enabled", lambda: False)
    res = run_meteofrance_charts(route=FRENCH, departure_time=ETD, data_dir=tmp_path)
    assert (res.in_coverage, res.within_horizon, res.zone_cycles) == (False, False, {})


def test_refresh_failure_keeps_coverage_but_no_charts(cached: Path, monkeypatch):
    from weatherbrief.fetch.chart_cache import RefreshReport
    from weatherbrief.tasks import meteofrance_charts as task_mod
    monkeypatch.setattr(task_mod, "refresh_charts",
                        lambda *a, **k: RefreshReport(error="boom"))
    res = run_meteofrance_charts(route=FRENCH, departure_time=ETD, data_dir=cached)
    assert res.in_coverage is True and res.zone_cycles == {}


def test_refresh_raising_does_not_break_the_briefing(cached: Path, monkeypatch):
    from weatherbrief.tasks import meteofrance_charts as task_mod

    def boom(*a, **k):
        raise RuntimeError("network gone")

    monkeypatch.setattr(task_mod, "refresh_charts", boom)
    res = run_meteofrance_charts(route=FRENCH, departure_time=ETD, data_dir=cached)
    assert res.in_coverage is True and res.zone_cycles == {}


def test_zone_missing_from_cache_is_skipped(cached: Path):
    """The zones don't publish in lockstep, so one may have no validity."""
    (cached / "meteofrance_charts" / "2026-08-31T15Z" / "euroc.png").unlink()
    res = run_meteofrance_charts(route=FRENCH, departure_time=ETD, data_dir=cached)
    assert res.zone_cycles == {"france": "2026-08-31T15Z"}
    assert res.within_horizon is True


# ---------------------------------------------------------------------------
# Serving gates
# ---------------------------------------------------------------------------


def test_meteofrance_is_not_a_synoptic_basemap():
    """The maps page has no route, so a route-gated source can't appear there."""
    from weatherbrief.api._chart_serving import SOURCES, source_allowed

    spec = SOURCES["meteofrance"]
    assert spec.synoptic_basemap is False
    assert source_allowed(spec, request=None, db=None) is False


def test_meteofrance_bytes_are_privately_cached(tmp_path: Path):
    """Licence-restricted bytes must never carry a public cache header."""
    from weatherbrief.api._chart_serving import SOURCES, serve_chart_bytes

    d = tmp_path / "meteofrance_charts" / "2026-08-31T15Z"
    d.mkdir(parents=True)
    (d / "france.png").write_bytes(b"\x89PNG stub")

    resp = serve_chart_bytes(
        tmp_path, SOURCES["meteofrance"], "2026-08-31T15Z", "france", immutable=False,
    )
    assert resp.headers["Cache-Control"].startswith("private")


def test_sibling_sources_keep_public_caching(tmp_path: Path):
    """Guard against the private-cache flag leaking onto the open sources."""
    from weatherbrief.api._chart_serving import SOURCES, serve_chart_bytes

    d = tmp_path / "dwd_charts" / "2026-08-31T12Z"
    d.mkdir(parents=True)
    (d / "ana.png").write_bytes(b"\x89PNG stub")

    resp = serve_chart_bytes(
        tmp_path, SOURCES["dwd"], "2026-08-31T12Z", "ana", immutable=False,
    )
    assert resp.headers["Cache-Control"].startswith("public")
