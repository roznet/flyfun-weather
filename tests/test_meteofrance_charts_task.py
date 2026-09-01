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


def test_offers_one_option_per_zone(cached: Path):
    res = run_meteofrance_charts(route=FRENCH, departure_time=ETD, data_dir=cached)
    assert res.in_coverage is True
    assert res.within_horizon is True
    assert res.options == [
        {"zone": z, "run_cycle": "2026-08-31T15Z"} for z in CHART_IDS
    ]
    # France first: the low-level chart is the one GA is actually flown inside.
    assert res.default_id == "france|2026-08-31T15Z"


def test_offers_neighbouring_validities_for_trend(cached: Path):
    """Once a chart represents the ETD, the ones either side are offered too."""
    root = cached / "meteofrance_charts"
    for cycle in ("2026-08-31T12Z", "2026-08-31T18Z"):
        d = root / cycle
        d.mkdir(parents=True, exist_ok=True)
        (d / "france.png").write_bytes(b"\x89PNG stub")

    res = run_meteofrance_charts(route=FRENCH, departure_time=ETD, data_dir=cached)
    assert [(o["zone"], o["run_cycle"]) for o in res.options] == [
        ("france", "2026-08-31T15Z"),
        ("euroc", "2026-08-31T15Z"),
        ("france", "2026-08-31T12Z"),
        ("france", "2026-08-31T18Z"),
    ]
    assert res.default_id == "france|2026-08-31T15Z"


def test_chart_hours_before_departure_is_still_offered(cached: Path):
    """The gate is 6h, not "does a chart represent the ETD".

    AEROWEB publishes barely two validities ahead, so under the old 1h30 rule
    a flight even three hours out saw nothing — precisely when the current
    TEMSI is still worth reading. 15Z cached, 20:00Z departure: 5h early, and
    it must show.
    """
    res = run_meteofrance_charts(
        route=FRENCH,
        departure_time=datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
        data_dir=cached,
    )
    assert res.within_horizon is True
    assert res.default_id == "france|2026-08-31T15Z"


def test_gate_stops_at_six_hours(cached: Path):
    """Past 6h it is no longer context, and the section stays hidden."""
    res = run_meteofrance_charts(
        route=FRENCH,
        departure_time=datetime(2026, 8, 31, 21, 31, tzinfo=timezone.utc),
        data_dir=cached,
    )
    assert res.within_horizon is False
    assert res.options == []


def test_stale_validities_are_not_offered(cached: Path):
    """A chart from yesterday is not trend context; it is noise."""
    d = cached / "meteofrance_charts" / "2026-08-30T15Z"
    d.mkdir(parents=True, exist_ok=True)
    (d / "france.png").write_bytes(b"\x89PNG stub")
    res = run_meteofrance_charts(route=FRENCH, departure_time=ETD, data_dir=cached)
    assert all(o["run_cycle"].startswith("2026-08-31") for o in res.options)


def test_non_french_route_gets_nothing(cached: Path, monkeypatch):
    """The licence gate, not a coverage gate — the bytes are cached and still withheld."""
    called = []
    from weatherbrief.tasks import meteofrance_charts as task_mod
    monkeypatch.setattr(task_mod, "refresh_charts",
                        lambda *a, **k: called.append(1))

    res = run_meteofrance_charts(route=BRITISH, departure_time=ETD, data_dir=cached)
    assert res.in_coverage is False
    assert res.options == []
    assert called == []  # never even refreshed


def test_beyond_horizon_is_distinct_from_out_of_coverage(cached: Path):
    """A next-day briefing is eligible but has no chart — the UI must tell them apart."""
    res = run_meteofrance_charts(
        route=FRENCH, departure_time=ETD + timedelta(days=1), data_dir=cached,
    )
    assert res.in_coverage is True
    assert res.within_horizon is False
    assert res.options == []


def test_unconfigured_source_is_silent(tmp_path: Path, monkeypatch):
    from weatherbrief.tasks import meteofrance_charts as task_mod
    monkeypatch.setattr(task_mod, "enabled", lambda: False)
    res = run_meteofrance_charts(route=FRENCH, departure_time=ETD, data_dir=tmp_path)
    assert (res.in_coverage, res.within_horizon, res.options) == (False, False, [])


def test_refresh_failure_keeps_coverage_but_no_charts(cached: Path, monkeypatch):
    from weatherbrief.fetch.chart_cache import RefreshReport
    from weatherbrief.tasks import meteofrance_charts as task_mod
    monkeypatch.setattr(task_mod, "refresh_charts",
                        lambda *a, **k: RefreshReport(error="boom"))
    res = run_meteofrance_charts(route=FRENCH, departure_time=ETD, data_dir=cached)
    assert res.in_coverage is True and res.options == []


def test_refresh_raising_does_not_break_the_briefing(cached: Path, monkeypatch):
    from weatherbrief.tasks import meteofrance_charts as task_mod

    def boom(*a, **k):
        raise RuntimeError("network gone")

    monkeypatch.setattr(task_mod, "refresh_charts", boom)
    res = run_meteofrance_charts(route=FRENCH, departure_time=ETD, data_dir=cached)
    assert res.in_coverage is True and res.options == []


def test_zone_missing_from_cache_is_skipped(cached: Path):
    """The zones don't publish in lockstep, so one may have no validity."""
    (cached / "meteofrance_charts" / "2026-08-31T15Z" / "euroc.png").unlink()
    res = run_meteofrance_charts(route=FRENCH, departure_time=ETD, data_dir=cached)
    assert res.options == [{"zone": "france", "run_cycle": "2026-08-31T15Z"}]
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


# ---------------------------------------------------------------------------
# Serve-time liveness filtering
# ---------------------------------------------------------------------------


def _meta_with(options, default_id, tmp_path):
    from weatherbrief.models.storage import BriefingPackMeta

    return BriefingPackMeta(
        flight_id="f",
        fetch_timestamp=ETD,
        days_out=0,
        artifact_path=str(tmp_path / "pack"),
        meteofrance_charts_options=options,
        meteofrance_charts_default_id=default_id,
        meteofrance_charts_in_coverage=True,
        meteofrance_charts_within_horizon=True,
    )


def _write_chart(data_dir: Path, run_cycle: str, zone: str) -> None:
    d = data_dir / "meteofrance_charts" / run_cycle
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{zone}.png").write_bytes(b"\x89PNG stub")


def test_evicted_options_are_not_offered(tmp_path: Path, monkeypatch):
    """A briefing outlives the ~48h TEMSI cache; dead tabs must not be shown."""
    from weatherbrief.api.packs import _live_meteofrance_options

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _write_chart(tmp_path, "2026-08-31T15Z", "france")

    options, default = _live_meteofrance_options(_meta_with(
        [{"zone": "france", "run_cycle": "2026-08-31T15Z"},
         {"zone": "euroc", "run_cycle": "2026-08-31T15Z"},
         {"zone": "france", "run_cycle": "2026-08-31T18Z"}],
        "france|2026-08-31T15Z",
        tmp_path,
    ))
    assert options == [{"zone": "france", "run_cycle": "2026-08-31T15Z"}]
    assert default == "france|2026-08-31T15Z"


def test_default_falls_back_when_its_chart_is_evicted(tmp_path: Path, monkeypatch):
    """Opening on a dead tab would 410 on first paint."""
    from weatherbrief.api.packs import _live_meteofrance_options

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _write_chart(tmp_path, "2026-08-31T18Z", "euroc")

    options, default = _live_meteofrance_options(_meta_with(
        [{"zone": "france", "run_cycle": "2026-08-31T15Z"},
         {"zone": "euroc", "run_cycle": "2026-08-31T18Z"}],
        "france|2026-08-31T15Z",  # evicted
        tmp_path,
    ))
    assert options == [{"zone": "euroc", "run_cycle": "2026-08-31T18Z"}]
    assert default == "euroc|2026-08-31T18Z"


def test_all_evicted_yields_nothing(tmp_path: Path, monkeypatch):
    from weatherbrief.api.packs import _live_meteofrance_options

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    options, default = _live_meteofrance_options(_meta_with(
        [{"zone": "france", "run_cycle": "2026-08-31T15Z"}],
        "france|2026-08-31T15Z",
        tmp_path,
    ))
    assert options == [] and default is None


def test_pack_still_records_what_was_offered(tmp_path: Path, monkeypatch):
    """Filtering happens on the way out — the stored record is untouched."""
    from weatherbrief.api.packs import _live_meteofrance_options

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    meta = _meta_with(
        [{"zone": "france", "run_cycle": "2026-08-31T15Z"}],
        "france|2026-08-31T15Z",
        tmp_path,
    )
    _live_meteofrance_options(meta)
    assert meta.meteofrance_charts_options == [
        {"zone": "france", "run_cycle": "2026-08-31T15Z"},
    ]
    assert meta.meteofrance_charts_default_id == "france|2026-08-31T15Z"
