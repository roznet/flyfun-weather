"""The climb / cruise / descent flight path (#1005)."""

from __future__ import annotations

import math

import pytest

from weatherbrief.analysis.flight_path import (
    ICED_RATE_FACTOR,
    PRESETS,
    analyses_flight_path,
    build_flight_path,
    climb_rate_fpm,
    compute_flight_path,
    estimate_duration_h,
    flight_path_mode,
    preset_for,
    recompute_for_cruise,
    resolve_performance,
)
from weatherbrief.atmo import cas_to_mach_isa, cas_to_tas_isa, ias_to_tas_isa, mach_to_tas_isa


def _climb(path):
    """(minutes, nm) of the climb to the path's peak."""
    top = next(p for p in path.profile if p[1] >= path.peak_ft - 1e-6)
    return top[2] / 60.0, top[0]


def _descent(path):
    tod = next(p for p in path.profile if p[0] >= path.tod_nm - 1e-6)
    return (path.profile[-1][2] - tod[2]) / 60.0, path.total_distance_nm - path.tod_nm


def _within(value, expected, pct=10):
    return abs(value - expected) <= expected * pct / 100


# The issue's table (still air, illustrative): (preset, cruise ft, route nm,
# cruise IAS, climb min, climb nm, descent min, descent nm).
TABLE = [
    ("hp_piston", 10000, 150, 150, 14, 28, 20, 54),
    ("turbo_piston", 16000, 250, 160, 16, 35, 23, 65),
    ("single_turboprop", 26000, 250, 220, 21, 65, 18, 82),
    ("light_jet", 41000, 350, 260, 23, 138, 23, 129),
]


@pytest.mark.parametrize(("preset", "cruise", "dist", "ias", "c_min", "c_nm", "d_min", "d_nm"), TABLE)
def test_presets_match_the_issue_table(preset, cruise, dist, ias, c_min, c_nm, d_min, d_nm):
    path = compute_flight_path([0.0, dist / 2, dist], cruise, PRESETS[preset], cruise_ias_kt=ias)
    assert path.cruise_reached
    climb_min, climb_nm = _climb(path)
    desc_min, desc_nm = _descent(path)
    assert _within(climb_min, c_min), climb_min
    assert _within(climb_nm, c_nm), climb_nm
    assert _within(desc_min, d_min), desc_min
    assert _within(desc_nm, d_nm), desc_nm


def test_points_carry_altitude_time_and_phase():
    path = compute_flight_path(
        [0, 10, 75, 140, 150], 10000, PRESETS["hp_piston"], 200, 400, cruise_ias_kt=150,
    )
    phases = [p.phase for p in path.points]
    assert phases == ["climb", "climb", "cruise", "descent", "descent"]
    assert path.points[0].altitude_ft == pytest.approx(200)
    assert path.points[-1].altitude_ft == pytest.approx(400)
    assert path.points[2].altitude_ft == pytest.approx(10000)
    times = [p.time_offset_s for p in path.points]
    assert times == sorted(times) and times[0] == 0
    assert times[-1] == pytest.approx(path.duration_h * 3600, rel=1e-3)


def test_field_elevations_shorten_the_climb_and_descent():
    flat = compute_flight_path([0, 150], 10000, PRESETS["hp_piston"], cruise_ias_kt=150)
    high = compute_flight_path([0, 150], 10000, PRESETS["hp_piston"], 3000, 3000, cruise_ias_kt=150)
    assert high.toc_nm < flat.toc_nm
    assert high.tod_nm > flat.tod_nm


def test_short_leg_does_not_reach_cruise():
    path = compute_flight_path([0, 10, 20], 10000, PRESETS["hp_piston"], cruise_ias_kt=150)
    assert not path.cruise_reached
    assert path.peak_ft < 10000
    assert path.toc_nm == pytest.approx(path.tod_nm)
    assert 0 < path.toc_nm < 20
    alts = [p.altitude_ft for p in path.points]
    assert alts[1] > alts[0] and alts[1] > alts[2]


def test_turbo_holds_its_rate_to_the_critical_altitude():
    perf = PRESETS["turbo_piston"]
    assert climb_rate_fpm(perf, 2000) == climb_rate_fpm(perf, 17000) == 1000
    assert climb_rate_fpm(perf, 21500) < 1000
    assert climb_rate_fpm(perf, 25000) == pytest.approx(300)


def test_jet_climb_crosses_over_to_mach():
    perf = PRESETS["light_jet"]
    # 260 KIAS is above M0.70 high up: the schedule caps at the Mach.
    assert cas_to_mach_isa(260, 35000) > 0.70
    from weatherbrief.analysis.flight_path import climb_tas_kt
    assert climb_tas_kt(perf, 35000) == pytest.approx(mach_to_tas_isa(0.70, 35000))
    # Below FL100 the 250 kt limit applies.
    assert climb_tas_kt(perf, 5000) == pytest.approx(cas_to_tas_isa(250, 5000))


def test_compressible_speeds_agree_with_isa_at_low_speed():
    assert cas_to_tas_isa(100, 0) == pytest.approx(100, rel=1e-3)
    assert cas_to_tas_isa(120, 8000) == pytest.approx(ias_to_tas_isa(120, 8000), rel=0.01)
    assert mach_to_tas_isa(1.0, 0) == pytest.approx(661.5, rel=1e-3)


def test_headwind_lengthens_the_time():
    still = compute_flight_path([0, 200], 8000, PRESETS["hp_piston"], cruise_ias_kt=140)
    windy = compute_flight_path(
        [0, 200], 8000, PRESETS["hp_piston"], cruise_ias_kt=140, wind_at=lambda d, a: 20.0,
    )
    assert windy.duration_h > still.duration_h * 1.1
    # A headwind also steepens the climb over the ground.
    assert windy.toc_nm < still.toc_nm


def test_planned_duration_scales_the_times_not_the_rates():
    path = compute_flight_path([0, 100, 200], 8000, PRESETS["hp_piston"], cruise_ias_kt=140)
    slow = compute_flight_path(
        [0, 100, 200], 8000, PRESETS["hp_piston"], cruise_ias_kt=140,
        planned_duration_h=path.duration_h * 1.5,
    )
    assert slow.points[-1].time_offset_s == pytest.approx(path.points[-1].time_offset_s * 1.5, rel=1e-3)
    climb = path.slice(0, path.toc_nm).climb
    assert slow.slice(0, slow.toc_nm).climb.fpm == pytest.approx(climb.fpm)


def test_slice_splits_a_cell_into_its_parts():
    path = compute_flight_path([0, 100, 200], 8000, PRESETS["hp_piston"], cruise_ias_kt=140)
    cell = path.slice(path.toc_nm - 5, path.toc_nm + 5)
    assert cell.climb is not None and cell.descent is None
    assert cell.level_nm == pytest.approx(5)
    assert cell.climb.top_ft == pytest.approx(8000)
    assert 0 < cell.climb.fpm < 1000


# --- performance resolution ------------------------------------------------


@pytest.mark.parametrize(("category", "ias", "turbo", "expected"), [
    ("SEP", 100, False, "trainer_piston"),
    ("SEP", 150, False, "hp_piston"),
    ("SEP", 150, True, "turbo_piston"),
    ("MEP", 160, False, "twin_piston"),
    ("SET", 200, False, "single_turboprop"),
    ("MET", 220, False, "twin_turboprop"),
    ("JET", 260, False, "light_jet"),
    (None, None, False, "hp_piston"),
])
def test_preset_from_icao_category(category, ias, turbo, expected):
    assert preset_for(category, ias, turbo) == expected


def test_sr22t_climbs_at_120_kias():
    perf = resolve_performance(category="SEP", cruise_ias_kt=170, turbo=True)
    assert perf.preset == "turbo_piston"
    assert perf.climb_ias_kt == 120


def test_overrides_win_over_the_preset():
    perf = resolve_performance(
        category="SEP", cruise_ias_kt=150, ceiling_ft=15000,
        climb_ias_kt=95, climb_rate_fpm=800, descent_angle_deg=4,
    )
    assert perf.source == "aircraft"
    assert perf.climb_ias_kt == 95
    assert perf.climb_rate_low_fpm == 800
    # The near-ceiling rate keeps the preset's proportion.
    assert perf.climb_rate_high_fpm == pytest.approx(80)
    assert perf.ceiling_ft == 15000
    assert perf.descent_mode == "angle" and perf.descent_angle_deg == 4


def test_unknown_aircraft_gets_the_default():
    assert resolve_performance(category="JET", cruise_ias_kt=100, known=False).preset == "trainer_piston"
    perf = resolve_performance(category="SEP", cruise_ias_kt=150, known=False, climb_ias_kt=60)
    assert perf.preset == "hp_piston" and perf.source == "default" and perf.climb_ias_kt == 110


def test_iced_rate_factor_is_seven_tenths():
    assert ICED_RATE_FACTOR == 0.7


# --- builders, recompute and the flag ----------------------------------------


def test_recompute_for_cruise_keeps_inputs():
    path = compute_flight_path(
        [0, 50, 100], 8000, PRESETS["twin_piston"], 300, 200, cruise_ias_kt=160,
        point_indices=[3, 4, 5],
    )
    same = recompute_for_cruise(path, 8000)
    assert same is path
    higher = recompute_for_cruise(path, 12000)
    assert higher.peak_ft > path.peak_ft
    assert [p.point_index for p in higher.points] == [3, 4, 5]
    assert higher.departure_elevation_ft == 300


def test_estimate_duration_is_longer_than_still_air_cruise_only():
    perf = PRESETS["hp_piston"]
    h = estimate_duration_h(150, 10000, perf, cruise_ias_kt=150)
    cruise_only = 150 / ias_to_tas_isa(150, 10000)
    assert h > cruise_only
    assert math.isclose(h, compute_flight_path([0, 150], 10000, perf, cruise_ias_kt=150).duration_h)


def test_flag_defaults_to_shadow(monkeypatch):
    monkeypatch.delenv("WB_FLIGHT_PATH", raising=False)
    assert flight_path_mode() == "shadow"
    monkeypatch.setenv("WB_FLIGHT_PATH", "live")
    assert flight_path_mode() == "live"
    monkeypatch.setenv("WB_FLIGHT_PATH", "nonsense")
    assert flight_path_mode() == "shadow"


def test_off_builds_no_path(monkeypatch):
    monkeypatch.setenv("WB_FLIGHT_PATH", "off")
    assert analyses_flight_path([], 8000) is None


def test_build_never_raises_on_bad_input():
    assert build_flight_path([0.0], [0], 8000) is None
    assert build_flight_path([0.0, 0.0], [0, 1], 8000) is None
