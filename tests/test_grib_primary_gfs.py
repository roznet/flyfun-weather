"""GRIB-primary GFS slot (#913): gate, .idx plans, units, wiring.

The decoders were checked against live NOAA files (gfs.20261024/00z f011,
f012, pgrb2 + pgrb2b) when this landed; these tests pin the pure parts
around them: which messages are planned, how raw values become
``HourlyForecast`` fields, and how the slot is written and gated.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from weatherbrief.fetch.grib.primary import build_skeleton, grib_primary_skip_reason
from weatherbrief.models import (
    ModelSource,
    PressureLevelData,
    RouteCrossSection,
    RoutePoint,
)

from tests.test_grib_primary import DEP, _cover_all, _points, _run_fetch

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def _us_points() -> list[RoutePoint]:
    return [
        RoutePoint(lat=40.6, lon=-73.8, distance_from_origin_nm=0.0, waypoint_icao="KJFK"),
        RoutePoint(lat=42.4, lon=-71.0, distance_from_origin_nm=160.0, waypoint_icao="KBOS"),
    ]


def test_gfs_gate_passes_for_a_european_flight(monkeypatch):
    monkeypatch.setenv("WB_GRIB_PRIMARY", "1")
    now = datetime.now(UTC)
    assert grib_primary_skip_reason(
        ModelSource.GFS, _points(), now + timedelta(hours=6), 2.0,
    ) is None


def test_gfs_gate_refuses_historical(monkeypatch):
    monkeypatch.setenv("WB_GRIB_PRIMARY", "1")
    assert grib_primary_skip_reason(
        ModelSource.GFS, _points(), DEP, 2.0, historical_mode=True,
    ) == "historical"


def test_gfs_gate_leaves_hrrr_routes_alone(monkeypatch):
    monkeypatch.setenv("WB_GRIB_PRIMARY", "1")
    monkeypatch.setenv("WB_HRRR_ENABLED", "true")
    now = datetime.now(UTC)
    assert grib_primary_skip_reason(
        ModelSource.GFS, _us_points(), now + timedelta(hours=3), 1.0,
    ) == "hrrr_route"
    # HRRR switched off: the same US route is a plain GFS slot.
    monkeypatch.setenv("WB_HRRR_ENABLED", "false")
    assert grib_primary_skip_reason(
        ModelSource.GFS, _us_points(), now + timedelta(hours=3), 1.0,
    ) is None


def test_gfs_gate_past_the_horizon(monkeypatch):
    monkeypatch.setenv("WB_GRIB_PRIMARY", "1")
    now = datetime.now(UTC)
    assert grib_primary_skip_reason(
        ModelSource.GFS, _points(), now + timedelta(days=17), 2.0,
    ) == "out_of_range"


def test_gfs_gate_switch_off(monkeypatch):
    monkeypatch.setenv("WB_GRIB_PRIMARY", "ecmwf,icon")
    assert grib_primary_skip_reason(
        ModelSource.GFS, _points(), datetime.now(UTC) + timedelta(hours=6), 2.0,
    ) == "disabled"


def test_gfs_window_out_of_range():
    from weatherbrief.fetch.grib.grib_fetch import gfs_window_out_of_range

    as_of = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    # Newest publishable cycle is 06z (12z minus the 5 h delay); +384 h.
    edge = datetime(2026, 10, 24, 6, tzinfo=UTC) + timedelta(hours=384)
    assert not gfs_window_out_of_range(edge - timedelta(hours=2), 2.0, as_of)
    assert gfs_window_out_of_range(edge - timedelta(hours=1), 2.0, as_of)


# ---------------------------------------------------------------------------
# .idx plans
# ---------------------------------------------------------------------------

_IDX_F001 = """\
1:0:d=2026102400:PRMSL:mean sea level:1 hour fcst:
2:1000:d=2026102400:TMP:2 m above ground:1 hour fcst:
3:2000:d=2026102400:UGRD:10 m above ground:1 hour fcst:
4:3000:d=2026102400:VGRD:10 m above ground:1 hour fcst:
5:4000:d=2026102400:APCP:surface:0-1 hour acc fcst:
6:5000:d=2026102400:APCP:surface:0-1 hour acc fcst:
7:6000:d=2026102400:ACPCP:surface:0-1 hour acc fcst:
8:7000:d=2026102400:ACPCP:surface:0-1 hour acc fcst:
9:8000:d=2026102400:TCDC:entire atmosphere:1 hour fcst:
10:9000:d=2026102400:TCDC:entire atmosphere:0-1 hour ave fcst:
11:10000:d=2026102400:LCDC:low cloud layer:0-1 hour ave fcst:
12:11000:d=2026102400:CPOFP:surface:1 hour fcst:
"""

_IDX_F012 = """\
1:0:d=2026102400:PRMSL:mean sea level:12 hour fcst:
2:1000:d=2026102400:TMP:2 m above ground:12 hour fcst:
3:2000:d=2026102400:UGRD:10 m above ground:12 hour fcst:
4:3000:d=2026102400:VGRD:10 m above ground:12 hour fcst:
5:4000:d=2026102400:APCP:surface:6-12 hour acc fcst:
6:5000:d=2026102400:APCP:surface:0-12 hour acc fcst:
7:6000:d=2026102400:ACPCP:surface:6-12 hour acc fcst:
8:7000:d=2026102400:ACPCP:surface:0-12 hour acc fcst:
9:8000:d=2026102400:TCDC:entire atmosphere:6-12 hour ave fcst:
10:9000:d=2026102400:TCDC:entire atmosphere:12 hour fcst:
11:10000:d=2026102400:HGT:0C isotherm:12 hour fcst:
"""

_IDX_F000 = """\
1:0:d=2026102400:PRMSL:mean sea level:anl:
2:1000:d=2026102400:TMP:2 m above ground:anl:
3:2000:d=2026102400:TCDC:entire atmosphere:anl:
"""


def _plan(idx: str, fhour: int) -> dict[str, tuple[int, int | None]]:
    from weatherbrief.fetch.grib.gfs_idx import plan_gfs_surface_byte_ranges

    return {r.variable: r.start for r in plan_gfs_surface_byte_ranges(idx, fhour)}


def test_surface_plan_takes_running_totals_and_instant_covers():
    plan = _plan(_IDX_F012, 12)
    assert plan["APCP"] == 5000     # 0-12 running total, not the 6-12 bucket
    assert plan["ACPCP"] == 7000
    assert plan["TCDC"] == 9000    # instantaneous, not the 6-12 average
    assert plan["HGT"] == 10000     # freezing level, last message
    assert {"PRMSL", "TMP", "UGRD", "VGRD"} <= set(plan)


def test_surface_plan_first_of_duplicates_and_no_averages():
    plan = _plan(_IDX_F001, 1)
    assert plan["APCP"] == 4000
    assert plan["ACPCP"] == 6000
    assert plan["TCDC"] == 8000
    assert "LCDC" not in plan               # only an average offered: absent
    assert plan["CPOFP"] == 11000


def test_surface_plan_reads_the_analysis_at_f000():
    plan = _plan(_IDX_F000, 0)
    assert set(plan) == {"PRMSL", "TMP", "TCDC"}


# gfs.20261025/06z f120: a whole-day running total is labelled in days.
_IDX_F120 = """\
1:0:d=2026102506:PRMSL:mean sea level:120 hour fcst:
2:1000:d=2026102506:TMP:2 m above ground:120 hour fcst:
3:2000:d=2026102506:APCP:surface:114-120 hour acc fcst:
4:3000:d=2026102506:APCP:surface:0-5 day acc fcst:
5:4000:d=2026102506:ACPCP:surface:114-120 hour acc fcst:
6:5000:d=2026102506:ACPCP:surface:0-5 day acc fcst:
7:6000:d=2026102506:TCDC:entire atmosphere:120 hour fcst:
"""


def test_surface_plan_reads_whole_day_totals_labelled_in_days():
    plan = _plan(_IDX_F120, 120)
    assert plan["APCP"] == 3000     # 0-5 day total, not the 114-120 bucket
    assert plan["ACPCP"] == 5000
    # f024's "0-1 day" total must not be taken for any other step.
    assert "APCP" not in _plan(_IDX_F120.replace("0-5 day", "0-1 day"), 120)


def test_sounding_plan_problem_names_the_gap():
    from weatherbrief.fetch.grib.gfs_idx import (
        GFS_SOUNDING_MANDATORY,
        ByteRange,
        gfs_sounding_plan_problem,
    )

    def ranges(levels, skip=()):
        return [
            ByteRange(variable=v, level_hpa=p, start=0, end=1)
            for p in levels for v in GFS_SOUNDING_MANDATORY if (v, p) not in skip
        ]

    assert gfs_sounding_plan_problem(ranges([850, 875]), [850, 875]) is None
    why = gfs_sounding_plan_problem(ranges([850]), [850, 875])
    assert why is not None and "875" in why
    why = gfs_sounding_plan_problem(ranges([850], skip={("RH", 850)}), [850])
    assert why is not None and "RH" in why


def test_sounding_cache_key_separates_level_subsets():
    from weatherbrief.fetch.grib import _gfs_sounding_cache_key, gfs_sounding_levels

    full = _gfs_sounding_cache_key(6, gfs_sounding_levels())
    assert full == _gfs_sounding_cache_key(6, list(reversed(gfs_sounding_levels())))
    assert _gfs_sounding_cache_key(6, [850, 700, 500]) != full
    assert len(gfs_sounding_levels()) == 28


def test_sounding_cache_key_separates_variable_subsets():
    # The map's reduced column (#914) must never stand in for the briefing's.
    from weatherbrief.fetch.grib import _gfs_sounding_cache_key, gfs_sounding_levels
    from weatherbrief.fetch.grib.gfs_idx import (
        GFS_SNAPSHOT_VARIABLES,
        GFS_SOUNDING_VARIABLES,
    )

    full = _gfs_sounding_cache_key(6, gfs_sounding_levels())
    assert _gfs_sounding_cache_key(6, gfs_sounding_levels(), GFS_SOUNDING_VARIABLES) == full
    # The snapshot column: the same 28 levels, the mandatory five only.
    snapshot = _gfs_sounding_cache_key(6, gfs_sounding_levels(), GFS_SNAPSHOT_VARIABLES)
    fewer_levels = _gfs_sounding_cache_key(6, [1000, 850, 700], GFS_SNAPSHOT_VARIABLES)
    assert len({full, snapshot, fewer_levels}) == 3
    assert "_V" in snapshot and "_L" not in snapshot


# ---------------------------------------------------------------------------
# Units and rates
# ---------------------------------------------------------------------------


def test_gfs_surface_snapshot_units():
    from weatherbrief.fetch.grib.decode import build_gfs_surface_snapshot

    snap = build_gfs_surface_snapshot({
        "TMP": 283.15, "DPT": 279.15, "RH": 76.0, "UGRD": 0.0, "VGRD": -10.0,
        "GUST": 15.0, "PRMSL": 101300.0, "PRES": 99000.0, "VIS": 24000.0,
        "HGT": 2100.0, "CAPE": -1.0, "CIN": -35.0, "LFTX": 2.5,
        "TCDC": 100.4, "LCDC": 40.0, "MCDC": 0.0, "HCDC": 5.0,
    })
    assert snap["temperature_2m_c"] == pytest.approx(10.0)
    assert snap["dewpoint_2m_c"] == pytest.approx(6.0)
    assert snap["relative_humidity_2m_pct"] == pytest.approx(76.0)
    assert snap["wind_speed_10m_kt"] == pytest.approx(19.44, abs=0.01)
    assert snap["wind_direction_10m_deg"] == pytest.approx(0.0)  # from the north
    assert snap["wind_gusts_10m_kt"] == pytest.approx(29.16, abs=0.01)
    assert snap["pressure_msl_hpa"] == pytest.approx(1013.0)
    assert snap["surface_pressure_hpa"] == pytest.approx(990.0)
    assert snap["visibility_m"] == 24000.0
    assert snap["freezing_level_m"] == 2100.0
    assert snap["cape_jkg"] == 0.0
    assert snap["convective_inhibition_jkg"] == pytest.approx(-35.0)
    assert snap["lifted_index_raw"] == 2.5
    assert snap["cloud_cover_pct"] == 100.0
    assert snap["cloud_cover_low_pct"] == 40.0


def test_gfs_surface_snapshot_empty_point():
    from weatherbrief.fetch.grib.decode import build_gfs_surface_snapshot

    assert all(v is None for v in build_gfs_surface_snapshot({}).values())


@pytest.mark.parametrize("raw,expected", [
    ({"CPOFP": 40.0}, 0.4),
    ({"CPOFP": -6e-6}, 0.0),          # a real 0 packed a hair below zero
    ({"CPOFP": -50.0, "CSNOW": 1.0}, 1.0),  # fill: no precip, use the category
    ({"CPOFP": -50.0}, None),
    ({"CSNOW": 0.0}, 0.0),
])
def test_gfs_frozen_fraction(raw, expected):
    from weatherbrief.fetch.grib.decode import gfs_frozen_fraction

    assert gfs_frozen_fraction(raw) == (None if expected is None else pytest.approx(expected))


def test_gfs_frozen_fraction_falls_back_to_window_start():
    """A snow shower that ended before the step: the step has CPOFP's fill and
    a dry CSNOW, the window's start still says frozen."""
    from weatherbrief.fetch.grib.decode import gfs_frozen_fraction, gfs_surface_rates

    step = {"APCP": 3.0, "ACPCP": 1.0, "CPOFP": -50.0, "CSNOW": 0.0}
    start = {"APCP": 1.0, "ACPCP": 0.4, "CPOFP": 100.0}
    assert gfs_frozen_fraction(step, start) == pytest.approx(1.0)
    out = gfs_surface_rates(step, start, 3.0)
    assert out["snowfall_cm"] == pytest.approx(2.0 / 3.0 * 0.7)
    assert out["rain_mm"] == 0.0
    # The step's own CPOFP wins when it has one.
    assert gfs_frozen_fraction({"CPOFP": 0.0}, start) == 0.0
    # Both fills: the step's CSNOW, as before.
    assert gfs_frozen_fraction(step, {"CPOFP": -50.0}) == 0.0


def test_gfs_surface_rates():
    from weatherbrief.fetch.grib.decode import gfs_surface_rates

    prev = {"APCP": 1.0, "ACPCP": 0.4}
    out = gfs_surface_rates({"APCP": 3.0, "ACPCP": 1.0, "CPOFP": 25.0}, prev, 1.0)
    assert out["precipitation_mm"] == pytest.approx(2.0)
    assert out["showers_mm"] == pytest.approx(0.6)
    assert out["snowfall_cm"] == pytest.approx(2.0 * 0.25 * 0.7)
    assert out["rain_mm"] == pytest.approx((2.0 - 0.6) * 0.75)
    # 3-hourly steps after f120: the amount is spread over the window.
    out = gfs_surface_rates({"APCP": 4.0, "ACPCP": 0.4}, prev, 3.0)
    assert out["precipitation_mm"] == pytest.approx(1.0)
    # Nothing falls: no snow, no rain, whatever CPOFP says.
    out = gfs_surface_rates({"APCP": 1.0, "ACPCP": 0.4, "CPOFP": -50.0}, prev, 1.0)
    assert out["snowfall_cm"] == 0.0 and out["rain_mm"] == 0.0
    # No previous step: nothing to difference.
    assert gfs_surface_rates({"APCP": 1.0}, None, None) == {}
    # Missing ACPCP: total known, split unknown.
    out = gfs_surface_rates({"APCP": 2.0, "CPOFP": 0.0}, {"APCP": 1.0}, 1.0)
    assert out["precipitation_mm"] == pytest.approx(1.0)
    assert "showers_mm" not in out and "rain_mm" not in out


# ---------------------------------------------------------------------------
# Writing the slot
# ---------------------------------------------------------------------------


def _gfs_section(pts):
    skel = build_skeleton(pts, ModelSource.GFS, start_date="2026-05-04", end_date="2026-05-04")
    return RouteCrossSection(model=ModelSource.GFS, route_points=pts,
                             fetched_at=DEP, point_forecasts=skel), skel


def test_gfs_surface_written_onto_skeleton():
    from weatherbrief.fetch.grib import _apply_gfs_surface_to_sections

    pts = _points(2)
    cs, skel = _gfs_section(pts)
    base = {"TMP": 283.15, "UGRD": 1.0, "VGRD": 1.0, "PRMSL": 101300.0, "TCDC": 30.0}
    surface = {
        8: [dict(base, APCP=1.0, ACPCP=0.0), dict(base, APCP=2.0, ACPCP=0.0)],  # lead
        9: [dict(base, APCP=1.5, ACPCP=0.5, CPOFP=0.0), dict(base, ACPCP=0.0)],
        10: [dict(base, APCP=2.5, ACPCP=0.5, CPOFP=0.0), dict(base, APCP=3.0, ACPCP=0.0)],
    }
    _apply_gfs_surface_to_sections([cs], [], pts, "20260504", 0, surface)
    p0 = {h.time.hour: h for h in skel[0].hourly}
    p1 = {h.time.hour: h for h in skel[1].hourly}
    assert p0[8].temperature_2m_c == pytest.approx(10.0)
    assert p0[8].precipitation_mm is None   # lead step only seeds
    assert p0[9].precipitation_mm == pytest.approx(0.5)
    assert p0[9].showers_mm == pytest.approx(0.5)
    assert p0[9].rain_mm == pytest.approx(0.0)
    assert p0[10].precipitation_mm == pytest.approx(1.0)
    assert p0[10].rain_mm == pytest.approx(1.0)
    assert p0[10].cloud_cover_pct == 30.0
    # Point 1 lost APCP at 09: unknown at 09 and 10, never a smaller number.
    assert p1[9].precipitation_mm is None and p1[10].precipitation_mm is None
    assert p1[10].pressure_msl_hpa == pytest.approx(1013.0)


def test_gfs_surface_keeps_open_meteo_values():
    from weatherbrief.fetch.grib import _apply_gfs_surface_to_sections

    pts = _points(1)
    cs, skel = _gfs_section(pts)
    h9 = next(h for h in skel[0].hourly if h.time.hour == 9)
    h9.temperature_2m_c = 99.0
    _apply_gfs_surface_to_sections([cs], [], pts, "20260504", 0, {9: [{"TMP": 283.15}]})
    assert h9.temperature_2m_c == 99.0


def test_sounding_only_written_where_the_hour_has_none():
    from weatherbrief.fetch.grib import _replace_pressure_levels_from_grib

    pts = _points(1)
    cs, skel = _gfs_section(pts)
    valid = datetime(2026, 5, 4, 9, tzinfo=UTC)
    h9 = next(h for h in skel[0].hourly if h.time == valid)
    om_level = PressureLevelData(pressure_hpa=850, temperature_c=-40.0, geopotential_height_m=1500.0)
    h9.pressure_levels = [om_level]
    column = {850: {
        "raw_temperature_k": 278.15, "raw_relative_humidity_pct": 80.0,
        "raw_u_wind_m_s": 5.0, "raw_v_wind_m_s": 0.0, "geopotential_height_m": 1480.0,
    }}
    n = _replace_pressure_levels_from_grib(
        [cs], [], pts, [column], valid_utc=valid,
        model_source=ModelSource.GFS, only_empty=True,
    )
    assert n == 0 and h9.pressure_levels == [om_level]
    h9.pressure_levels = []
    n = _replace_pressure_levels_from_grib(
        [cs], [], pts, [column], valid_utc=valid,
        model_source=ModelSource.GFS, only_empty=True,
    )
    assert n == 1 and h9.pressure_levels[0].temperature_c == pytest.approx(5.0)


def test_gfs_column_problem():
    from weatherbrief.fetch.grib import _gfs_column_problem

    full = {
        "raw_temperature_k": 1.0, "raw_relative_humidity_pct": 1.0,
        "raw_u_wind_m_s": 1.0, "raw_v_wind_m_s": 1.0, "geopotential_height_m": 1.0,
    }
    assert _gfs_column_problem({850: full, 700: full}, [850, 700]) is None
    assert _gfs_column_problem({}, [850]) == "no levels decoded"
    why = _gfs_column_problem({850: full, 700: {**full, "raw_v_wind_m_s": None}}, [850, 700])
    assert why == "700hPa missing raw_v_wind_m_s"


def test_hours_without_sounding():
    from weatherbrief.fetch.grib import _gfs_hours_without_sounding

    pts = _points(1)
    cs, skel = _gfs_section(pts)
    for h in skel[0].hourly:
        if h.time.hour != 10:
            h.pressure_levels = [PressureLevelData(pressure_hpa=850)]
    assert _gfs_hours_without_sounding([cs], "20260504", 6, [2, 3, 4, 5]) == [4]


def test_mixed_hour_keeps_the_patch_for_open_meteo_points():
    """A point that fell back to Open-Meteo keeps its CLMR/ICMR patch."""
    from weatherbrief.fetch.grib import _gfs_hour_has_sounding

    pts = _points(2)
    cs, skel = _gfs_section(pts)
    assert not _gfs_hour_has_sounding([cs], "20260504", 6, 4)
    h10 = next(h for h in skel[1].hourly if h.time.hour == 10)
    h10.pressure_levels = [PressureLevelData(pressure_hpa=850)]
    assert _gfs_hour_has_sounding([cs], "20260504", 6, 4)


@pytest.mark.parametrize("grib_built,hrrr_tried", [(False, True), (True, False)])
def test_grib_primary_flag_comes_from_the_gate_not_the_data(tmp_path, grib_built, hrrr_tried):
    """An Open-Meteo slot with empty hours still gets HRRR; only the gate's flag skips it."""
    from weatherbrief.fetch import grib as grib_mod

    pts = _points(1)
    cs, _skel = _gfs_section(pts)  # every hour empty, like an OM slot with nulls
    tried: list[bool] = []

    def fake_hrrr(*a, **kw):
        tried.append(True)
        return 123

    with patch.object(grib_mod, "_try_enrich_gfs_from_hrrr", new=fake_hrrr), patch.object(
        grib_mod, "find_latest_run", return_value=None,
    ):
        out = grib_mod._enrich_gfs_inner(
            [cs], [], pts, DEP, data_dir=tmp_path, flight_duration_hours=1.0,
            grib_built=grib_built,
        )
    assert bool(tried) is hrrr_tried
    assert out == ((123, "hrrr:noaa") if hrrr_tried else (None, None))


def test_gfs_previous_step():
    from weatherbrief.fetch.grib import _gfs_previous_step

    assert _gfs_previous_step(0) is None
    assert _gfs_previous_step(7) == 6
    assert _gfs_previous_step(120) == 119
    assert _gfs_previous_step(123) == 120


# ---------------------------------------------------------------------------
# run_fetch wiring
# ---------------------------------------------------------------------------


def test_covered_gfs_slot_makes_no_open_meteo_request(monkeypatch):
    result, om_calls = _run_fetch(
        monkeypatch, enrich=_cover_all(ModelSource.GFS),
        gate=lambda m: None if m == ModelSource.GFS else "disabled",
    )
    assert [m for m, _ in om_calls] == ["ecmwf"]
    assert result.grib_primary == {"gfs": 0}


def test_partial_gfs_slot_falls_back_for_short_points_only(monkeypatch):
    result, om_calls = _run_fetch(
        monkeypatch, enrich=_cover_all(ModelSource.GFS, skip={0}),
        gate=lambda m: None if m == ModelSource.GFS else "disabled",
    )
    assert [n for m, n in om_calls if m == "gfs"] == [1]
    assert result.grib_primary == {"gfs": 1}


# ---------------------------------------------------------------------------
# Reader (download + decode), network stubbed
# ---------------------------------------------------------------------------


def test_read_gfs_grib_points_reads_the_lead_step_and_skips_failures(tmp_path):
    from weatherbrief.fetch import grib as grib_mod

    asked: dict[str, list[int]] = {"snd": [], "sfc": []}
    sounding_flag = {"raise": False}

    def fake_snd(init_date, init_hour, fhour, run_dir, idx, session, levels, variables=None):
        asked["snd"].append(fhour)
        return fhour != 8

    def fake_sfc(init_date, init_hour, fhour, run_dir, idx, session):
        asked["sfc"].append(fhour)
        if fhour == 7 and sounding_flag["raise"]:
            raise ValueError("bad .idx")
        return True

    def fake_dispatch(jobs, return_exceptions=False, priority=None):
        out = []
        for name, (path, lats, lons) in jobs:
            if "f009" in path and name == "decode_gfs_surface":
                out.append(RuntimeError("bad file"))
            elif name == "decode_gfs_sounding":
                out.append([{850: {"x": 1.0}} for _ in lats])
            else:
                out.append([{"TMP": 280.0} for _ in lats])
        return out

    with patch.object(grib_mod, "_ensure_gfs_sounding", new=fake_snd), patch.object(
        grib_mod, "_ensure_gfs_surface", new=fake_sfc,
    ), patch.object(grib_mod, "_dispatch_decode_parallel", new=fake_dispatch):
        snd, sfc, steps = grib_mod.read_gfs_grib_points(
            "20260504", 0, [9, 8], [50.0, 51.0], [1.0, 2.0],
            run_dir=tmp_path, session=None,
        )
    assert {k: sorted(v) for k, v in asked.items()} == {"snd": [8, 9], "sfc": [7, 8, 9]}
    assert steps == [7, 8, 9]
    assert sorted(snd) == [9]
    assert sorted(sfc) == [7, 8]     # f009 decode failed: absent, no raise
    assert len(sfc[7]) == 2

    with patch.object(grib_mod, "_ensure_gfs_sounding", new=fake_snd), patch.object(
        grib_mod, "_ensure_gfs_surface", new=fake_sfc,
    ), patch.object(grib_mod, "_dispatch_decode_parallel", new=fake_dispatch):
        asked = {"snd": [], "sfc": []}
        snd, _, _ = grib_mod.read_gfs_grib_points(
            "20260504", 0, [9], [50.0], [1.0], run_dir=tmp_path, session=None,
            sounding=False,
        )
    assert snd == {} and asked["snd"] == []

    # every_previous_step: the step before each hour, not only the first
    # (map hours are 3 h apart, #914); variables reach the sounding fetch.
    seen_vars: list = []

    def fake_snd_vars(init_date, init_hour, fhour, run_dir, idx, session, levels, variables=None):
        seen_vars.append(variables)
        return True

    with patch.object(grib_mod, "_ensure_gfs_sounding", new=fake_snd_vars), patch.object(
        grib_mod, "_ensure_gfs_surface", new=fake_sfc,
    ), patch.object(grib_mod, "_dispatch_decode_parallel", new=fake_dispatch):
        asked = {"snd": [], "sfc": []}
        _, _, steps = grib_mod.read_gfs_grib_points(
            "20260504", 0, [6, 9, 126], [50.0], [1.0], run_dir=tmp_path, session=None,
            variables=frozenset({"TMP"}), every_previous_step=True,
        )
    assert steps == [5, 6, 8, 9, 123, 126]
    assert seen_vars == [frozenset({"TMP"})] * 3

    # One hour raising (e.g. an .idx parse error) loses that hour only.
    sounding_flag["raise"] = True
    with patch.object(grib_mod, "_ensure_gfs_sounding", new=fake_snd), patch.object(
        grib_mod, "_ensure_gfs_surface", new=fake_sfc,
    ), patch.object(grib_mod, "_dispatch_decode_parallel", new=fake_dispatch):
        snd, sfc, _ = grib_mod.read_gfs_grib_points(
            "20260504", 0, [8, 9], [50.0], [1.0], run_dir=tmp_path, session=None,
        )
    assert sorted(snd) == [9] and sorted(sfc) == [8]


class TestProbeGfsRun:
    """The GFS run probe checks the last needed step, not just f000 (§70)."""

    class _Session:
        def __init__(self, ok_urls):
            self.ok_urls = set(ok_urls)

        def head(self, url, timeout=None):
            from types import SimpleNamespace

            return SimpleNamespace(status_code=200 if url in self.ok_urls else 404)

    def test_published_step_grid(self):
        from weatherbrief.fetch.grib.grib_fetch import gfs_published_step

        assert gfs_published_step(0) == 0
        assert gfs_published_step(120) == 120
        assert gfs_published_step(121) == 123
        assert gfs_published_step(150) == 150
        assert gfs_published_step(400) == 384

    def test_f000_alone_unless_last_hour_given(self):
        from weatherbrief.fetch.grib.grib_fetch import gfs_idx_url, probe_gfs_run

        session = self._Session([gfs_idx_url("20261028", 12, 0)])
        assert probe_gfs_run("20261028", 12, session) is not None
        assert probe_gfs_run("20261028", 12, session, last_hour=145) is None

    def test_last_step_snapped_to_the_published_grid(self):
        from weatherbrief.fetch.grib.grib_fetch import gfs_idx_url, probe_gfs_run

        session = self._Session([
            gfs_idx_url("20261028", 12, 0), gfs_idx_url("20261028", 12, 147),
        ])
        resp = probe_gfs_run("20261028", 12, session, last_hour=145)
        assert resp is not None and resp.status_code == 200
