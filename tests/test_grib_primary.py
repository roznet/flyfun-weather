"""GRIB-primary ECMWF / ICON briefing slots (#912).

Covers the pieces that let a briefing skip the Open-Meteo fetch for the two
slots GRIB already rebuilds: the gate switch, the empty skeleton, the
per-point Open-Meteo fallback and its splice rules, the ``run_fetch`` wiring
(no Open-Meteo request for a covered slot), and the new GRIB surface fields
(ECMWF rain/showers split, ICON surface decode and rates, the MSLP height
datum, the ICON time interpolation).
"""

from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from weatherbrief.fetch.grib import primary
from weatherbrief.fetch.grib.primary import (
    build_skeleton,
    fill_grib_primary_gaps,
    grib_primary_models,
    required_hours,
    splice_open_meteo,
    uncovered_points,
)
from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    PressureLevelData,
    RouteCrossSection,
    RoutePoint,
    WaypointForecast,
)
from weatherbrief.tasks import fetch as fetch_module

from tests.test_fetch_parallel import _disable_elevation, _route

UTC = timezone.utc
DEP = datetime(2026, 5, 4, 9, 30, tzinfo=UTC)  # 2 h flight → window 09:00–12:00Z


def _points(n: int = 3) -> list[RoutePoint]:
    pts = []
    for i in range(n):
        pts.append(RoutePoint(
            lat=50.0 + 0.1 * i, lon=1.0 + 0.1 * i,
            distance_from_origin_nm=10.0 * i,
            waypoint_icao="EGTK" if i == 0 else None,
            waypoint_name="Oxford" if i == 0 else None,
        ))
    return pts


def _levels() -> list[PressureLevelData]:
    return [PressureLevelData(pressure_hpa=850, temperature_c=5.0, geopotential_height_m=1500.0)]


def _fill_hour(h: HourlyForecast, *, temp: float = 12.0) -> None:
    h.pressure_levels = _levels()
    h.temperature_2m_c = temp
    h.wind_speed_10m_kt = 10.0
    h.precipitation_mm = 0.0
    h.cloud_cover_pct = 40.0


def _fill_window(wf: WaypointForecast, hours: list[datetime], **kw) -> None:
    for h in wf.hourly:
        if h.time in hours:
            _fill_hour(h, **kw)


class _FakeClient:
    """Stands in for OpenMeteoClient: counts calls, returns full OM forecasts."""

    def __init__(self, fail: Exception | None = None):
        self.call_count = 0
        self.calls: list[tuple[ModelSource, int]] = []
        self.fail = fail

    def fetch_multi_point(self, points, model, *, start_date=None, end_date=None):
        self.call_count += 1
        self.calls.append((model, len(points)))
        if self.fail is not None:
            raise self.fail
        skel = build_skeleton(points, model, start_date=start_date, end_date=end_date)
        for wf in skel:
            for h in wf.hourly:
                _fill_hour(h, temp=99.0)
                h.precipitation_probability_pct = 20.0
                h.lifted_index_raw = -1.0
        return skel


# ---------------------------------------------------------------------------
# Switch, gate, skeleton, window
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    (None, (ModelSource.ECMWF, ModelSource.ICON, ModelSource.GFS)),
    ("1", (ModelSource.ECMWF, ModelSource.ICON, ModelSource.GFS)),
    ("0", ()),
    ("off", ()),
    ("ecmwf", (ModelSource.ECMWF,)),
    ("icon, gfs", (ModelSource.ICON, ModelSource.GFS)),
    ("ecmwf,icon", (ModelSource.ECMWF, ModelSource.ICON)),
    ("ukmo", ()),
])
def test_switch(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("WB_GRIB_PRIMARY", raising=False)
    else:
        monkeypatch.setenv("WB_GRIB_PRIMARY", value)
    assert grib_primary_models() == expected


def test_gate_disabled_and_icon_rules(monkeypatch):
    monkeypatch.setenv("WB_GRIB_PRIMARY", "0")
    assert primary.grib_primary_skip_reason(ModelSource.ICON, _points(), DEP, 2.0) == "disabled"
    monkeypatch.setenv("WB_GRIB_PRIMARY", "1")
    assert primary.grib_primary_skip_reason(
        ModelSource.ICON, _points(), DEP, 2.0, historical_mode=True,
    ) == "historical"
    far = [RoutePoint(lat=40.0, lon=-75.0, distance_from_origin_nm=0.0)]
    assert primary.grib_primary_skip_reason(ModelSource.ICON, far, DEP, 2.0) == "out_of_domain"
    now = datetime.now(UTC)
    assert primary.grib_primary_skip_reason(
        ModelSource.ICON, _points(), now + timedelta(days=9), 2.0,
    ) == "out_of_range"
    assert primary.grib_primary_skip_reason(
        ModelSource.ICON, _points(), now + timedelta(hours=6), 2.0,
    ) is None
    # Models without a GRIB source never are.
    assert primary.grib_primary_skip_reason(
        ModelSource.UKMO, _points(), DEP, 2.0,
    ) == "disabled"


def test_gate_ecmwf_without_files(monkeypatch, tmp_path):
    monkeypatch.setenv("WB_GRIB_PRIMARY", "1")
    monkeypatch.setenv("ECMWF_GRIB_DIR", str(tmp_path))
    assert primary.grib_primary_skip_reason(ModelSource.ECMWF, _points(), DEP, 2.0) == "no_run"


def test_skeleton_matches_open_meteo_shape():
    pts = _points()
    skel = build_skeleton(pts, ModelSource.ICON, start_date="2026-05-04", end_date="2026-05-05")
    assert len(skel) == len(pts)
    # Open-Meteo labels: ICAO for waypoints, RP<nm> for interpolated points.
    assert [wf.waypoint.icao for wf in skel] == ["EGTK", "RP010", "RP020"]
    hours = skel[0].hourly
    assert len(hours) == 48
    assert hours[0].time == datetime(2026, 5, 4, 0, tzinfo=UTC)
    assert hours[-1].time == datetime(2026, 5, 5, 23, tzinfo=UTC)
    assert all(h.temperature_2m_c is None and not h.pressure_levels for h in hours)


def test_required_hours_window():
    hours = required_hours(DEP, 2.0)
    assert hours[0] == datetime(2026, 5, 4, 9, tzinfo=UTC)
    assert hours[-1] == datetime(2026, 5, 4, 12, tzinfo=UTC)
    assert len(required_hours(datetime(2026, 5, 4, 9, tzinfo=UTC), 0.0)) == 2


# ---------------------------------------------------------------------------
# Coverage and splice
# ---------------------------------------------------------------------------


def test_uncovered_points_and_splice_rules():
    pts = _points()
    skel = build_skeleton(pts, ModelSource.ECMWF, start_date="2026-05-04", end_date="2026-05-04")
    hours = required_hours(DEP, 2.0)
    for wf in skel:
        _fill_window(wf, hours)
    # Point 1 misses cloud cover at one hour: uncovered.
    gap = next(h for h in skel[1].hourly if h.time == hours[1])
    gap.cloud_cover_pct = None
    assert uncovered_points(skel, hours) == [1]

    om = _FakeClient().fetch_multi_point([pts[1]], ModelSource.ECMWF,
                                         start_date="2026-05-04", end_date="2026-05-04")[0]
    grib_levels = gap.pressure_levels
    splice_open_meteo(skel[1], om)
    # The hole takes Open-Meteo's value, GRIB values are kept.
    assert gap.cloud_cover_pct == 40.0
    assert gap.temperature_2m_c == 12.0
    assert gap.pressure_levels is grib_levels
    # Fields GRIB never writes come through too, at every hour.
    assert gap.precipitation_probability_pct == 20.0
    # An hour without a sounding takes Open-Meteo's whole column.
    early = skel[1].hourly[0]
    assert early.pressure_levels and early.temperature_2m_c == 99.0
    assert uncovered_points(skel, hours) == []


def _sections(model: ModelSource, pts, *, covered: set[int]) -> list[RouteCrossSection]:
    skel = build_skeleton(pts, model, start_date="2026-05-04", end_date="2026-05-04")
    hours = required_hours(DEP, 2.0)
    for i in covered:
        _fill_window(skel[i], hours)
    return [RouteCrossSection(model=model, route_points=pts,
                              fetched_at=datetime.now(UTC), point_forecasts=skel)]


def test_fill_gaps_no_call_when_covered():
    pts = _points()
    sections = _sections(ModelSource.ECMWF, pts, covered={0, 1, 2})
    client = _FakeClient()
    (out,) = fill_grib_primary_gaps(
        sections, pts, [ModelSource.ECMWF], DEP, 2.0,
        client=client, start_date="2026-05-04", end_date="2026-05-04",
    )
    assert client.calls == []
    assert out.points_fallback == 0 and not out.failed


def test_fill_gaps_ignores_window_hours_past_the_slot_span():
    """21:40 + 2 h (prod, #912): the window's ceil hour is 00Z next day, which
    a one-day slot cannot hold. A slot covered through 23Z needs no fallback."""
    pts = _points()
    dep = datetime(2026, 5, 4, 21, 40, tzinfo=UTC)
    skel = build_skeleton(pts, ModelSource.ECMWF, start_date="2026-05-04", end_date="2026-05-04")
    in_span = [t for t in required_hours(dep, 2.0) if t.day == 4]
    assert len(in_span) < len(required_hours(dep, 2.0))
    for wf in skel:
        _fill_window(wf, in_span)
    sections = [RouteCrossSection(model=ModelSource.ECMWF, route_points=pts,
                                  fetched_at=datetime.now(UTC), point_forecasts=skel)]
    client = _FakeClient()
    (out,) = fill_grib_primary_gaps(
        sections, pts, [ModelSource.ECMWF], dep, 2.0,
        client=client, start_date="2026-05-04", end_date="2026-05-04",
    )
    assert client.calls == []
    assert out.points_fallback == 0


def test_fill_gaps_fetches_only_short_points(caplog):
    pts = _points()
    sections = _sections(ModelSource.ICON, pts, covered={0, 2})
    client = _FakeClient()
    with caplog.at_level("INFO"):
        (out,) = fill_grib_primary_gaps(
            sections, pts, [ModelSource.ICON], DEP, 2.0,
            client=client, start_date="2026-05-04", end_date="2026-05-04",
            reason="test reason",
        )
    assert client.calls == [(ModelSource.ICON, 1)]
    assert out.points_fallback == 1 and out.open_meteo_calls == 1
    assert uncovered_points(sections[0].point_forecasts, required_hours(DEP, 2.0)) == []
    assert "Open-Meteo fallback for 1/3 points (test reason)" in caplog.text


def test_fill_gaps_reports_failure():
    pts = _points()
    sections = _sections(ModelSource.ICON, pts, covered=set())
    (out,) = fill_grib_primary_gaps(
        sections, pts, [ModelSource.ICON], DEP, 2.0,
        client=_FakeClient(fail=RuntimeError("down")),
        start_date="2026-05-04", end_date="2026-05-04",
    )
    assert out.failed and isinstance(out.error, RuntimeError)


# ---------------------------------------------------------------------------
# run_fetch wiring
# ---------------------------------------------------------------------------


def _run_fetch(monkeypatch, *, enrich, fetch_fail=None, gate=None):
    """run_fetch with ECMWF gated in, Open-Meteo mocked, enrichment stubbed."""
    monkeypatch.setenv("WB_GRIB_PRIMARY", "1")
    om_calls: list[tuple[str, int]] = []

    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        self._record_call()
        om_calls.append((model.value, len(points)))
        if fetch_fail is not None and model == ModelSource.ECMWF:
            raise fetch_fail
        return _FakeClient().fetch_multi_point(points, model, start_date=start_date, end_date=end_date)

    def fake_enrich(cross_sections, all_forecasts, route_points, departure_time, **kw):
        return enrich(cross_sections) or ({}, {}, {})

    def fake_gate(model, *a, **kw):
        return gate(model) if gate else (None if model == ModelSource.ECMWF else "disabled")

    with _disable_elevation(), patch(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        new=fake_fetch_multi_point,
    ), patch.object(fetch_module, "_open_meteo_live_horizons", return_value={}), patch(
        "weatherbrief.fetch.grib.enrich_forecasts", new=fake_enrich,
    ), patch.object(primary, "grib_primary_skip_reason", new=fake_gate):
        result = fetch_module.run_fetch(
            route=_route(), departure_time=DEP,
            models=[ModelSource.GFS, ModelSource.ECMWF],
            enrich_grib=True,
        )
    return result, om_calls


def _cover_all(model: ModelSource, skip: set[int] = frozenset()):
    def enrich(cross_sections):
        hours = required_hours(DEP, 2.0)
        for cs in cross_sections:
            if cs.model != model:
                continue
            for i, wf in enumerate(cs.point_forecasts):
                if i not in skip:
                    _fill_window(wf, hours)
        return {model.value: 1}, {}, {}
    return enrich


def test_covered_slot_makes_no_open_meteo_request(monkeypatch):
    result, om_calls = _run_fetch(monkeypatch, enrich=_cover_all(ModelSource.ECMWF))
    assert [m for m, _ in om_calls] == ["gfs"]
    assert sorted(result.models_fetched) == ["ecmwf", "gfs"]
    assert result.grib_primary == {"ecmwf": 0}
    assert result.open_meteo_api_calls == 1
    ecmwf = next(cs for cs in result.cross_sections if cs.model == ModelSource.ECMWF)
    # Waypoint forecasts share the skeleton objects, as with an OM slot.
    egtk = [wf for wf in result.all_forecasts if wf.model == ModelSource.ECMWF]
    assert len(egtk) == 2 and egtk[0] is ecmwf.point_forecasts[0]


def test_partial_slot_falls_back_for_short_points_only(monkeypatch):
    result, om_calls = _run_fetch(monkeypatch, enrich=_cover_all(ModelSource.ECMWF, skip={1, 2}))
    ecmwf_calls = [n for m, n in om_calls if m == "ecmwf"]
    assert ecmwf_calls == [2]
    assert result.grib_primary == {"ecmwf": 2}
    assert result.open_meteo_api_calls == 2


def test_enrichment_raising_falls_back_to_open_meteo(monkeypatch):
    def boom(_):
        raise RuntimeError("grib down")
    result, om_calls = _run_fetch(monkeypatch, enrich=boom)
    n_points = len(result.route_points)
    assert ("ecmwf", n_points) in om_calls
    assert "ecmwf" in result.models_fetched and result.grib_enrichment_failed


def test_failed_fallback_drops_slot_like_a_failed_fetch(monkeypatch):
    import requests

    result, _ = _run_fetch(
        monkeypatch, enrich=lambda cs: None, fetch_fail=requests.Timeout("slow"),
    )
    assert result.models_fetched == ["gfs"]
    assert result.models_failed == {"ecmwf": "timeout"}
    assert result.grib_primary == {}
    assert all(cs.model != ModelSource.ECMWF for cs in result.cross_sections)
    assert all(wf.model != ModelSource.ECMWF for wf in result.all_forecasts)


def test_gate_refusal_keeps_open_meteo_first(monkeypatch):
    result, om_calls = _run_fetch(
        monkeypatch, enrich=lambda cs: None, gate=lambda m: "no_run",
    )
    assert sorted(m for m, _ in om_calls) == ["ecmwf", "gfs"]
    assert result.grib_primary == {}


# ---------------------------------------------------------------------------
# GRIB surface fields
# ---------------------------------------------------------------------------


def test_ecmwf_rain_showers_split_and_new_instant_fields():
    from weatherbrief.fetch.grib import _apply_ecmwf_surface_to_hourly

    pts = _points(1)
    t0 = datetime(2026, 5, 4, 9, tzinfo=UTC)
    t1 = t0 + timedelta(hours=3)
    skel = build_skeleton(pts, ModelSource.ECMWF, start_date="2026-05-04", end_date="2026-05-04")
    cs = RouteCrossSection(model=ModelSource.ECMWF, route_points=pts,
                           fetched_at=t0, point_forecasts=skel)
    raw = {
        "temperature_2m_k": 283.15, "dewpoint_2m_k": 279.15, "msl_pa": 101500.0,
        "total_cover_frac": 0.5, "low_cover_frac": 0.25,
        # 3 h deltas: tp 6 mm, cp 1.5 mm, sf 0.6 mm water.
        "total_precip_m": 0.010, "conv_precip_m": 0.0025, "snowfall_m_we": 0.0010,
    }
    _apply_ecmwf_surface_to_hourly(
        [cs], [], pts, [raw], [True],
        valid_utc=t1, prev_valid_utc=t0,
        prev_tp_per_point=[0.004], prev_sf_per_point=[0.0004],
        prev_cp_per_point=[0.001],
    )
    by_t = {h.time: h for h in skel[0].hourly}
    for k in (1, 2, 3):
        h = by_t[t0 + timedelta(hours=k)]
        assert h.precipitation_mm == pytest.approx(2.0)
        assert h.showers_mm == pytest.approx(0.5)
        assert h.rain_mm == pytest.approx(1.3)  # (6 − 1.5 − 0.6) / 3
    assert by_t[t0].precipitation_mm is None  # outside (prev, valid]
    h1 = by_t[t1]
    assert h1.pressure_msl_hpa == pytest.approx(1015.0)
    assert h1.relative_humidity_2m_pct == pytest.approx(76.0, abs=1.0)
    assert h1.cloud_cover_pct == pytest.approx(50.0)
    assert h1.cloud_cover_low_pct == pytest.approx(25.0)


def test_mslp_datum_for_grib_primary_column():
    from weatherbrief.fetch.grib.decode import build_pressure_levels_from_grib

    # ICON-like column: T/RH/wind, no geopotential, no Open-Meteo reference.
    point = {
        p: {"raw_temperature_k": t, "raw_relative_humidity_pct": rh}
        for p, t, rh in ((1000, 288.15, 70.0), (850, 278.15, 70.0), (700, 268.15, 50.0))
    }
    isa = build_pressure_levels_from_grib(point)
    low = build_pressure_levels_from_grib(point, msl_pressure_hpa=990.0)
    by_p_low = {pl.pressure_hpa: pl for pl in low}
    by_p_isa = {pl.pressure_hpa: pl for pl in isa}
    # 1000 hPa sits below sea level when MSLP is 990 hPa (~-80 m).
    assert by_p_low[1000].geopotential_height_m == pytest.approx(-82.0, abs=10.0)
    assert by_p_isa[1000].geopotential_height_m > 50.0
    # Layer thicknesses still come from the column itself.
    thick_low = by_p_low[850].geopotential_height_m - by_p_low[1000].geopotential_height_m
    thick_isa = by_p_isa[850].geopotential_height_m - by_p_isa[1000].geopotential_height_m
    assert thick_low == pytest.approx(thick_isa, abs=1.0)


def test_icon_surface_snapshot_units():
    from weatherbrief.fetch.grib.decode import build_icon_surface_snapshot

    snap = build_icon_surface_snapshot({
        "t_2m": 283.15, "td_2m": 279.15, "u_10m": 0.0, "v_10m": -5.0,
        "vmax_10m": 10.0, "pmsl": 101300.0, "ps": 98000.0, "vis": 8000.0,
        "hzerocl": -999.0,
    })
    assert snap["temperature_2m_c"] == pytest.approx(10.0)
    assert snap["dewpoint_2m_c"] == pytest.approx(6.0)
    assert snap["relative_humidity_2m_pct"] == pytest.approx(76.0, abs=1.0)
    assert snap["wind_speed_10m_kt"] == pytest.approx(9.72, abs=0.01)
    assert snap["wind_direction_10m_deg"] == pytest.approx(0.0)  # from the north
    assert snap["wind_gusts_10m_kt"] == pytest.approx(19.44, abs=0.01)
    assert snap["pressure_msl_hpa"] == pytest.approx(1013.0)
    assert snap["surface_pressure_hpa"] == pytest.approx(980.0)
    assert snap["visibility_m"] == 8000.0
    assert snap.get("freezing_level_m") is None  # hzerocl sentinel


def test_icon_surface_rates():
    from weatherbrief.fetch.grib import _icon_surface_rates

    prev = {"tot_prec": 1.0, "rain_gsp": 0.5, "rain_con": 0.2, "snow_gsp": 0.1, "snow_con": 0.0}
    cur = {"tot_prec": 4.0, "rain_gsp": 2.0, "rain_con": 0.8, "snow_gsp": 0.4, "snow_con": 0.3}
    out = _icon_surface_rates(cur, prev, 3.0)
    assert out["precipitation_mm"] == pytest.approx(1.0)
    assert out["rain_mm"] == pytest.approx(0.5)
    assert out["showers_mm"] == pytest.approx(0.3)  # (0.6 + 0.3) / 3
    assert out["snowfall_cm"] == pytest.approx(0.2 * 0.7)  # (0.3 + 0.3) / 3 mm
    # A missing component makes the sum unknown, never smaller.
    partial = dict(cur)
    del partial["snow_con"]
    out = _icon_surface_rates(partial, prev, 3.0)
    assert "showers_mm" not in out and "snowfall_cm" not in out
    assert out["rain_mm"] == pytest.approx(0.5)
    assert _icon_surface_rates(cur, None, 3.0) == {}


def test_icon_surface_written_onto_skeleton():
    from weatherbrief.fetch.grib import _apply_icon_surface_to_sections

    pts = _points(2)
    skel = build_skeleton(pts, ModelSource.ICON, start_date="2026-05-04", end_date="2026-05-04")
    cs = RouteCrossSection(model=ModelSource.ICON, route_points=pts,
                           fetched_at=DEP, point_forecasts=skel)
    base = {"t_2m": 283.15, "td_2m": 279.15, "u_10m": 1.0, "v_10m": 1.0,
            "pmsl": 101300.0, "ps": 99000.0}
    surface = {
        8: [dict(base, tot_prec=1.0), dict(base, tot_prec=2.0)],  # lead step
        9: [dict(base, tot_prec=1.5), dict(base)],  # point 1 lost tot_prec
        10: [dict(base, tot_prec=2.5), dict(base, tot_prec=3.0)],
    }
    _apply_icon_surface_to_sections([cs], [], pts, "20260504", 0, surface, {})
    p0 = {h.time.hour: h for h in skel[0].hourly}
    p1 = {h.time.hour: h for h in skel[1].hourly}
    assert p0[8].temperature_2m_c == pytest.approx(10.0)
    assert p0[8].precipitation_mm is None  # lead step only seeds
    assert p0[9].precipitation_mm == pytest.approx(0.5)
    assert p0[10].precipitation_mm == pytest.approx(1.0)
    # Point 1: no tot_prec at 09 → unknown at 09 AND at 10 (no 2-step total).
    assert p1[9].precipitation_mm is None and p1[10].precipitation_mm is None
    assert p1[10].pressure_msl_hpa == pytest.approx(1013.0)


def test_icon_surface_decode_from_grib(tmp_path):
    pytest.importorskip("eccodes")
    from tests import grib_synth
    from weatherbrief.fetch.grib.decode import decode_icon_surface_per_point

    area = dict(lat_first=55.0, lat_last=45.0, lon_first=-5.0, lon_last=10.0)
    paths = {}
    for var, offset in (("t_2m", 280.0), ("pmsl", 101000.0)):
        p = tmp_path / f"{var}.grib2"
        with open(p, "wb") as f:
            # The shortName is deliberately unrelated: the file names the field.
            grib_synth.write(
                f, sample="regular_ll_sfc_grib2", short="2t", type_of_level="surface",
                level=0, area=area, step=0.0625,
                values=lambda lats, lons, o=offset: grib_synth.linear_field(lats, lons, offset=o),
            )
        paths[var] = str(p)
    out = decode_icon_surface_per_point(paths, [50.0, 70.0], [1.0, 1.0])
    expected = grib_synth.linear_field(
        __import__("numpy").array([50.0]), __import__("numpy").array([1.0]), offset=280.0,
    )[0][0]
    assert out[0]["t_2m"] == pytest.approx(float(expected), abs=1e-3)
    assert "pmsl" in out[0]
    assert out[1] == {}  # outside the grid: missing, not 0


def test_icon_surface_time_interpolated_across_3h_steps():
    from weatherbrief.fetch.grib.fill import _linear_interp_grib_surface
    from weatherbrief.models import NWPCloudDiagnostics

    pts = _points(1)
    skel = build_skeleton(pts, ModelSource.ICON, start_date="2026-05-04", end_date="2026-05-04")
    by_h = {h.time.hour: h for h in skel[0].hourly}
    for hour, msl, cover in ((9, 1010.0, 30.0), (12, 1016.0, 60.0)):
        h = by_h[hour]
        h.attach_nwp_diagnostics(NWPCloudDiagnostics())
        h.pressure_msl_hpa = msl
        h.cloud_cover_pct = cover
        h.temperature_2m_c = 10.0
    cs = RouteCrossSection(model=ModelSource.ICON, route_points=pts,
                           fetched_at=DEP, point_forecasts=skel)
    _linear_interp_grib_surface([cs], [])
    assert by_h[10].pressure_msl_hpa == pytest.approx(1012.0)
    assert by_h[11].cloud_cover_pct == pytest.approx(50.0)


def test_open_meteo_slot_keeps_its_values():
    """The fallback path matches today: new GRIB fields never overwrite OM."""
    from weatherbrief.fetch.grib import (
        _apply_ecmwf_surface_to_hourly,
        _apply_icon_surface_to_sections,
    )

    pts = _points(1)
    t0 = datetime(2026, 5, 4, 9, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)
    om = _FakeClient().fetch_multi_point(pts, ModelSource.ECMWF,
                                         start_date="2026-05-04", end_date="2026-05-04")
    for h in om[0].hourly:
        h.pressure_msl_hpa = 1001.0
        h.showers_mm = 0.7
    cs = RouteCrossSection(model=ModelSource.ECMWF, route_points=pts,
                           fetched_at=t0, point_forecasts=om)
    _apply_ecmwf_surface_to_hourly(
        [cs], [], pts,
        [{"msl_pa": 102000.0, "total_cover_frac": 0.9, "total_precip_m": 0.002,
          "conv_precip_m": 0.002, "snowfall_m_we": 0.0}],
        [True], valid_utc=t1, prev_valid_utc=t0,
        prev_tp_per_point=[0.0], prev_sf_per_point=[0.0], prev_cp_per_point=[0.0],
    )
    h1 = next(h for h in om[0].hourly if h.time == t1)
    assert h1.pressure_msl_hpa == 1001.0
    assert h1.cloud_cover_pct == 40.0
    assert h1.showers_mm == 0.7
    assert h1.precipitation_mm == pytest.approx(2.0)  # existing overwrite kept

    icon = _FakeClient().fetch_multi_point(pts, ModelSource.ICON,
                                           start_date="2026-05-04", end_date="2026-05-04")
    ics = RouteCrossSection(model=ModelSource.ICON, route_points=pts,
                            fetched_at=t0, point_forecasts=icon)
    _apply_icon_surface_to_sections(
        [ics], [], pts, "20260504", 0,
        {9: [{"t_2m": 300.0, "tot_prec": 0.0}], 10: [{"t_2m": 300.0, "tot_prec": 5.0}]}, {},
    )
    assert all(h.temperature_2m_c == 99.0 for h in icon[0].hourly)
    assert all(h.precipitation_mm == 0.0 for h in icon[0].hourly)


def test_time_scan_confirm_refuses_when_gap_fill_fails(tmp_path, monkeypatch, caplog):
    """A failed fallback leaves empty hours: the confirm refuses, never grades them."""
    from weatherbrief.tasks import artifacts, time_scan
    import weatherbrief.fetch.grib as grib_mod

    pts = _points()
    sections = _sections(ModelSource.ICON, pts, covered=set())
    monkeypatch.setattr(artifacts, "load_cross_sections", lambda _: sections)
    monkeypatch.setattr(artifacts, "load_route_points", lambda _: pts)
    monkeypatch.setattr(grib_mod, "enrich_forecasts", lambda *a, **k: ({}, {}, {}))
    monkeypatch.setattr(primary, "fill_grib_primary_gaps", lambda *a, **k: [
        primary.GribPrimaryOutcome(model="icon", points_total=3, empty=True),
    ])
    graded = []
    monkeypatch.setattr(
        "weatherbrief.tasks.advise.run_alt_from_pack",
        lambda *a, **k: graded.append(1),
    )
    (tmp_path / "fetch_meta.json").write_text(
        '{"fetch_facts": {"grib_primary": {"icon": 0}}}'
    )
    with caplog.at_level("WARNING"):
        assert time_scan.confirm_candidate(
            tmp_path, _route(), DEP, data_dir=tmp_path,
        ) is None
    assert graded == []
    assert "GRIB-primary gap fill failed for icon" in caplog.text


# ---------------------------------------------------------------------------
# Post-rollout checks: fetch facts in the pack, pack-vs-Open-Meteo script
# ---------------------------------------------------------------------------


def test_fetch_facts_written_and_survive_meta_rewrite(monkeypatch, tmp_path):
    import json

    from weatherbrief.tasks.artifacts import write_pack_meta

    monkeypatch.setenv("WB_GRIB_PRIMARY", "1")

    def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
        self._record_call()
        return _FakeClient().fetch_multi_point(points, model, start_date=start_date, end_date=end_date)

    with _disable_elevation(), patch(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        new=fake_fetch_multi_point,
    ), patch.object(fetch_module, "_open_meteo_live_horizons", return_value={}), patch(
        "weatherbrief.fetch.grib.enrich_forecasts", new=lambda cs, *a, **k: _cover_all(ModelSource.ECMWF)(cs),
    ), patch.object(primary, "grib_primary_skip_reason",
                    new=lambda m, *a, **k: None if m == ModelSource.ECMWF else "disabled"):
        fetch_module.run_fetch(
            route=_route(), departure_time=DEP,
            models=[ModelSource.GFS, ModelSource.ECMWF],
            enrich_grib=True, pack_dir=tmp_path,
        )
    facts = json.loads((tmp_path / "fetch_meta.json").read_text())["fetch_facts"]
    assert facts["grib_primary"] == {"ecmwf": 0}
    assert facts["open_meteo_calls"] == 1  # GFS only: no leak for the GRIB slot
    assert facts["grib_init_times"] == {"ecmwf": 1}
    # The pipeline's end-of-run rewrite does not know them and must keep them.
    write_pack_meta(tmp_path, models_fetched=["gfs", "ecmwf"], diagnostics=[])
    again = json.loads((tmp_path / "fetch_meta.json").read_text())["fetch_facts"]
    assert again == facts


def test_pack_vs_open_meteo_helpers():
    from scripts.compare_pack_surface_openmeteo import (
        VarStats,
        billed_units,
        compare_point,
        sample_indices,
    )

    assert sample_indices(10, 3) == [0, 4, 9]
    assert sample_indices(2, 5) == [0, 1]
    assert sample_indices(7, 1) == [3]
    assert billed_units(3) == pytest.approx(4.8)  # 16 variables → 1.6 per point

    hours = [HourlyForecast(time=datetime(2026, 5, 4, h, tzinfo=UTC)) for h in (9, 10, 11)]
    hours[0].temperature_2m_c, hours[0].precipitation_mm = 10.0, 3.0
    hours[1].temperature_2m_c, hours[1].precipitation_mm = 11.0, 3.0
    # 11Z has no GRIB value: not compared.
    om = {
        "time": ["2026-05-04T09:00", "2026-05-04T10:00", "2026-05-04T11:00"],
        "temperature_2m": [9.0, 15.0, 0.0],
        "precipitation": [1.0, 1.0, 0.0],
    }
    stats: dict[str, VarStats] = {}
    assert compare_point(stats, hours, om, "pk pt0") == 2
    t = stats["temperature_2m_c"]
    assert t.n == 2 and t.sum_d / t.n == pytest.approx(-1.5)
    assert t.max_abs == pytest.approx(4.0) and "10:00" in t.max_where
    assert len(t.suspects) == 1  # |Δ| 4 > 3 °C
    p = stats["precipitation_mm"]
    assert p.total_pack / p.total_om == pytest.approx(3.0)  # an accumulation slip shows as ×3


def test_failure_drill_sends_points_to_the_fallback(monkeypatch):
    monkeypatch.setenv("WB_GRIB_PRIMARY_DRILL", "ecmwf:half")
    result, om_calls = _run_fetch(monkeypatch, enrich=_cover_all(ModelSource.ECMWF))
    n = len(result.route_points)
    assert [c for m, c in om_calls if m == "ecmwf"] == [len(range(0, n, 2))]
    assert result.grib_primary == {"ecmwf": len(range(0, n, 2))}


def test_pack_as_open_meteo_round_trip():
    from scripts.compare_pack_surface_openmeteo import compare_point, hourly_as_open_meteo

    pts = _points(1)
    skel = build_skeleton(pts, ModelSource.ICON, start_date="2026-05-04", end_date="2026-05-04")
    _fill_window(skel[0], required_hours(DEP, 2.0))
    stats = {}
    assert compare_point(stats, skel[0].hourly, hourly_as_open_meteo(skel[0].hourly), "x") == 4
    assert all(s.max_abs == 0 for s in stats.values())


def test_time_scan_confirm_leaves_open_meteo_slots_alone(tmp_path, monkeypatch):
    """No fetch_facts entry for the slot → no gap fill, today's behaviour."""
    from weatherbrief.tasks import artifacts, time_scan
    import weatherbrief.fetch.grib as grib_mod

    pts = _points()
    sections = _sections(ModelSource.ICON, pts, covered=set())
    monkeypatch.setattr(artifacts, "load_cross_sections", lambda _: sections)
    monkeypatch.setattr(artifacts, "load_route_points", lambda _: pts)
    monkeypatch.setattr(grib_mod, "enrich_forecasts", lambda *a, **k: ({}, {}, {}))
    seen = []

    def spy(cross_sections, route_points, models, *a, **k):
        seen.append(list(models))
        return []

    monkeypatch.setattr(primary, "fill_grib_primary_gaps", spy)
    monkeypatch.setattr("weatherbrief.tasks.advise.run_alt_from_pack", lambda *a, **k: types.SimpleNamespace(manifest=None))
    time_scan.confirm_candidate(tmp_path, _route(), DEP, data_dir=tmp_path)
    assert seen == [[]]
