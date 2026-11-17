"""Phase-1 cards on the flight path (#1005): aircraft icing, VMC, cloud tops, solver."""

from __future__ import annotations

from dataclasses import replace

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._flight_path import (
    FLIGHT_PATH_SHADOW_RULE,
    flown_band,
    path_rate_limit,
)
from weatherbrief.analysis.advisories.aircraft_icing import (
    AircraftIcingEvaluator,
    grade_aircraft_icing_model,
    resolve_aircraft_icing_params,
)
from weatherbrief.analysis.advisories.cloud_top import CloudTopEvaluator
from weatherbrief.analysis.advisories.vertical_profile import CostModel, Profile, solve
from weatherbrief.analysis.advisories.vmc_cruise import VMCCruiseEvaluator
from weatherbrief.analysis.flight_path import ICED_RATE_FACTOR, PRESETS, compute_flight_path
from weatherbrief.models import (
    AdvisoryStatus,
    AircraftEquipage,
    CloudCoverage,
    EnhancedCloudLayer,
    IcingRisk,
    IcingType,
    IcingZone,
)

from .conftest import _make_elevation, _make_rpa, _make_sounding

CRUISE = 8000
N = 21  # 0..200 nm every 10 nm
FIKI = AircraftEquipage(known=True, fiki=True, ceiling_ft=20000)
NON_FIKI = AircraftEquipage(known=True, fiki=False, ceiling_ft=20000)


def _zone(base, top, risk=IcingRisk.LIGHT, kind=IcingType.RIME):
    return IcingZone(base_ft=base, top_ft=top, risk=risk, icing_type=kind)


def _path():
    return compute_flight_path(
        [i * 10.0 for i in range(N)], CRUISE, PRESETS["hp_piston"], 500, 500,
        cruise_ias_kt=120,
    )


def _ctx(soundings: dict[int, dict], equipage=NON_FIKI, *, live=True, path=True) -> RouteContext:
    analyses = []
    for i in range(N):
        kwargs = soundings.get(i, {"freezing_level_ft": 5000})
        analyses.append(_make_rpa(i, i * 10.0, {"gfs": _make_sounding(**kwargs)}))
    return RouteContext(
        analyses=analyses, cross_sections=[], elevation=_make_elevation(500, 21, 200),
        models=["gfs"], cruise_altitude_ft=CRUISE, flight_ceiling_ft=18000,
        total_distance_nm=200.0, cruise_speed_ias_kt=120, equipage=equipage,
        flight_path=_path() if path else None, flight_path_live=live,
    )


def _grade(ctx):
    return grade_aircraft_icing_model(ctx, "gfs", resolve_aircraft_icing_params(ctx))


# --- the path on the context -------------------------------------------------


def test_flown_band_is_none_in_flat_mode_and_on_the_ground_at_departure():
    assert flown_band(_ctx({}, live=False), 0) is None
    lo, hi = flown_band(_ctx({}), 0)
    assert lo == pytest.approx(500)
    assert hi < 4000  # the first 5 nm of the climb
    assert flown_band(_ctx({}), 10) == (CRUISE, CRUISE)


# --- aircraft icing ------------------------------------------------------------


def test_cruise_level_ice_over_the_airport_is_not_flown_on_the_path():
    """Ice at 7-9,000 ft over the departure only: flat mode counts it, the path climbs under it."""
    iced = {0: {"freezing_level_ft": 5000, "icing_zones": [_zone(7000, 9000)]}}
    assert _grade(_ctx(iced, live=False)).status != AdvisoryStatus.GREEN
    assert _grade(_ctx(iced)).status == AdvisoryStatus.GREEN


def test_climb_through_ice_is_placed_on_the_route_and_timed_at_the_iced_rate():
    zone = _zone(3000, 5000)
    iced = {i: {"freezing_level_ft": 2500, "icing_zones": [zone]} for i in range(0, 4)}
    fiki = _grade(_ctx(iced, FIKI))
    non = _grade(_ctx(iced, NON_FIKI))
    assert fiki.stretches and non.stretches
    climb_fiki = fiki.stretches[0]
    assert climb_fiki.phases == ["climb"]
    lo, hi = climb_fiki.spans["climb"]
    assert 0 < lo < hi < 20
    # 2,000 ft of ice: the non-FIKI minutes are the FIKI minutes / 0.7.
    assert non.stretches[0].minutes == pytest.approx(climb_fiki.minutes / ICED_RATE_FACTOR, rel=1e-6)
    assert "climb, nm" in non.detail


def test_descent_through_ice_names_the_distance_out():
    zone = _zone(3000, 5000)
    iced = {i: {"freezing_level_ft": 2500, "icing_zones": [zone]} for i in range(15, N)}
    grade = _grade(_ctx(iced))
    assert grade.stretches[-1].phases[-1] == "descent"
    assert "nm out" in grade.detail


def test_shadow_records_the_path_grade_and_keeps_the_flat_one(monkeypatch):
    monkeypatch.delenv("WB_SLD_ICING", raising=False)
    iced = {0: {"freezing_level_ft": 5000, "icing_zones": [_zone(7000, 9000)]}}
    ctx = _ctx(iced, live=False)
    params = resolve_aircraft_icing_params(ctx)
    result = AircraftIcingEvaluator.evaluate(ctx, params)
    model = result.per_model[0]
    assert model.status != AdvisoryStatus.GREEN  # flat grade stands
    shadow = [s for s in model.shadow if s.rule == FLIGHT_PATH_SHADOW_RULE]
    assert len(shadow) == 1 and shadow[0].status == AdvisoryStatus.GREEN


def test_no_path_no_shadow():
    ctx = _ctx({}, live=False, path=False)
    result = AircraftIcingEvaluator.evaluate(ctx, resolve_aircraft_icing_params(ctx))
    assert not [s for s in result.per_model[0].shadow if s.rule == FLIGHT_PATH_SHADOW_RULE]


# --- cloud / VMC -----------------------------------------------------------------


LOW_DECK = [EnhancedCloudLayer(base_ft=2000, top_ft=4000, coverage=CloudCoverage.OVC)]
CRUISE_DECK = [EnhancedCloudLayer(base_ft=7000, top_ft=9000, coverage=CloudCoverage.OVC)]


def _vmc(ctx):
    return VMCCruiseEvaluator.evaluate(ctx, {}).per_model[0]


def test_vmc_low_deck_on_the_climb_is_reported_on_the_path():
    soundings = {i: {"cloud_layers": LOW_DECK} for i in range(0, 3)}
    flat = _vmc(_ctx(soundings, live=False))
    live = _vmc(_ctx(soundings))
    assert flat.status == AdvisoryStatus.GREEN and "in cloud" not in flat.detail
    assert "in cloud on the climb from nm" in live.detail
    assert live.affected_points > flat.affected_points


def test_vmc_cruise_deck_over_the_airports_is_climbed_under_on_the_path():
    soundings = {0: {"cloud_layers": CRUISE_DECK}, N - 1: {"cloud_layers": CRUISE_DECK}}
    assert _vmc(_ctx(soundings, live=False)).affected_points == 2
    assert _vmc(_ctx(soundings)).affected_points == 0


def test_vmc_shadow_records_without_changing_the_grade():
    soundings = {i: {"cloud_layers": LOW_DECK} for i in range(0, 3)}
    model = _vmc(_ctx(soundings, live=False))
    shadow = [s for s in model.shadow if s.rule == FLIGHT_PATH_SHADOW_RULE]
    assert len(shadow) == 1 and "in cloud on the climb" in shadow[0].detail


def test_cloud_tops_only_meet_layers_the_band_reaches():
    high = [EnhancedCloudLayer(base_ft=8500, top_ft=19000, coverage=CloudCoverage.OVC)]
    soundings = {0: {"cloud_layers": high}}
    flat = CloudTopEvaluator.evaluate(_ctx(soundings, live=False), {}).per_model[0]
    live = CloudTopEvaluator.evaluate(_ctx(soundings), {}).per_model[0]
    assert flat.affected_points == 1
    assert live.affected_points == 0


# --- solver rate limit -----------------------------------------------------------


def test_rate_limit_comes_from_the_path_only_when_live():
    assert path_rate_limit(_ctx({}, live=False)) is None
    climb, descent = path_rate_limit(_ctx({}))
    assert 100 < climb < 600 and 100 < descent < 400


def test_solver_rate_limit_forbids_a_too_steep_descent():
    """From 8,000 ft, preferring 2,000 ft: the free solve dives in one 5 nm step."""
    alts = [2000, 4000, 6000, 8000]
    model = CostModel(
        distances_nm=[0, 5, 10, 15], bin_altitudes_ft=alts,
        cost_field=[[0.0] * 4 for _ in range(4)], allowed_start_bins={3},
    )
    free = solve(model, preferred_alt_ft=2000)
    assert isinstance(free, Profile)
    assert free.transitions[0].to_alt_ft - free.transitions[0].from_alt_ft == -6000
    # 100 ft/nm over 5 nm is less than one bin: one bin per step at most.
    limited = solve(model, preferred_alt_ft=2000, rate_limit=(100.0, 100.0))
    assert isinstance(limited, Profile)
    assert all(abs(t.to_alt_ft - t.from_alt_ft) <= 2000 for t in limited.transitions)
    assert limited.segments[-1].alt_ft == 2000
