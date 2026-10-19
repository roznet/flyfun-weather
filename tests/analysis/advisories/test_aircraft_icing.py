"""The aircraft_icing card (#827): the grade table, exposure clock and escapes."""

from __future__ import annotations

import math

import pytest

from weatherbrief.analysis.advisories import RouteContext, evaluate_all, get_catalog
from weatherbrief.analysis.advisories.aircraft_icing import (
    AircraftIcingEvaluator,
    grade_aircraft_icing_model,
    resolve_aircraft_icing_params,
)
from weatherbrief.analysis.advisories.icing_settings_migration import migrate_icing_enabled
from weatherbrief.analysis.advisories.ifr_feasibility import IFRFeasibilityEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    AircraftEquipage,
    IcingRisk,
    IcingType,
    IcingZone,
    PrecipitationAssessment,
)

from .conftest import _make_elevation, _make_rpa, _make_sounding

CRUISE = 8000
N_POINTS = 21  # 0..200 nm every 10 nm
FIKI = AircraftEquipage(known=True, fiki=True, ceiling_ft=20000)
NON_FIKI = AircraftEquipage(known=True, fiki=False, ceiling_ft=20000)


def _zone(risk=IcingRisk.MODERATE, kind=IcingType.MIXED, base=7000, top=9000, sld=False):
    return IcingZone(base_ft=base, top_ft=top, risk=risk, icing_type=kind, sld_risk=sld)


def _ctx(iced: dict[int, dict], equipage: AircraftEquipage | None, *, warm: set[int] = frozenset()):
    """A 200 nm route at 8,000 ft. ``iced`` maps point index -> sounding kwargs."""
    analyses = []
    for i in range(N_POINTS):
        if i in iced:
            kwargs = {k: v for k, v in iced[i].items() if k != "_precip"}
            sounding = _make_sounding(**kwargs)
            precip = iced[i].get("_precip")
            if precip is not None:
                sounding = sounding.model_copy(update={"precipitation": precip})
        elif i in warm:
            sounding = _make_sounding(freezing_level_ft=12000)
        else:
            sounding = _make_sounding(freezing_level_ft=5000)
        analyses.append(_make_rpa(i, i * 10.0, {"gfs": sounding}))
    return RouteContext(
        analyses=analyses, cross_sections=[], elevation=_make_elevation(500, 21, 200),
        models=["gfs"], cruise_altitude_ft=CRUISE, flight_ceiling_ft=18000,
        total_distance_nm=200.0, cruise_speed_ias_kt=120, equipage=equipage,
    )


def _cells_for(ctx: RouteContext, minutes: float) -> int:
    """Smallest number of 10 nm cells whose flying time exceeds ``minutes``."""
    per_cell = 10.0 / ctx.cruise_groundspeed_kt * 60.0
    return math.floor(minutes / per_cell) + 1


def _stretch(start: int, n: int, **sounding) -> dict[int, dict]:
    clean = {k: v for k, v in sounding.items() if not k.startswith("_")}
    out = {}
    for i in range(start, start + n):
        out[i] = {"icing_zones": [_zone()], **clean}
        if "_precip" in sounding:
            out[i]["_precip"] = sounding["_precip"]
    if "icing_zones" in sounding:
        for i in out:
            out[i]["icing_zones"] = sounding["icing_zones"]
    return out


ESCAPE = {"freezing_level_ft": 5000}        # escape below 4,500 ft, terrain 500 ft
NO_ESCAPE = {"freezing_level_ft": 1000}     # 500 ft escape is below terrain + 1,000


def _status(ctx) -> AdvisoryStatus:
    params = resolve_aircraft_icing_params(ctx)
    return grade_aircraft_icing_model(ctx, "gfs", params).status


def _short_long(equipage):
    probe = _ctx({}, equipage)
    long_n = _cells_for(probe, 5)
    return 1, long_n


# --- the grade table, cell by cell -----------------------------------------


@pytest.mark.parametrize(
    ("equipage", "length", "escape", "expected"),
    [
        (FIKI, "short", True, AdvisoryStatus.AMBER),
        (FIKI, "short", False, AdvisoryStatus.AMBER),
        (FIKI, "long", True, AdvisoryStatus.AMBER),
        (FIKI, "long", False, AdvisoryStatus.RED),
        (NON_FIKI, "short", True, AdvisoryStatus.AMBER),
        (NON_FIKI, "short", False, AdvisoryStatus.RED),
        (NON_FIKI, "long", True, AdvisoryStatus.RED),
        (NON_FIKI, "long", False, AdvisoryStatus.RED),
    ],
)
def test_grade_table(equipage, length, escape, expected):
    short_n, long_n = _short_long(equipage)
    n = short_n if length == "short" else long_n
    sounding = dict(ESCAPE if escape else NO_ESCAPE)
    if not escape:
        # Block the FIKI climb-out too: tops above the aircraft ceiling - 1,000.
        sounding["icing_zones"] = [_zone(top=19500)]
    ctx = _ctx(_stretch(5, n, **sounding), equipage)
    assert _status(ctx) == expected


def test_no_icing_is_green_for_both_columns():
    for eq in (FIKI, NON_FIKI, None):
        assert _status(_ctx({}, eq)) == AdvisoryStatus.GREEN


@pytest.mark.parametrize("equipage", [FIKI, NON_FIKI])
@pytest.mark.parametrize("zone", [
    _zone(risk=IcingRisk.SEVERE),
    _zone(risk=IcingRisk.LIGHT, kind=IcingType.RIME, sld=True),
])
def test_beyond_certification_is_red_for_both(equipage, zone):
    ctx = _ctx(_stretch(5, 1, icing_zones=[zone], **ESCAPE), equipage)
    assert _status(ctx) == AdvisoryStatus.RED


def test_light_rime_gets_the_longer_short_limit():
    probe = _ctx({}, NON_FIKI)
    rime = [_zone(risk=IcingRisk.LIGHT, kind=IcingType.RIME)]
    # Longer than 5 min but within 15: short for rime, long for mixed.
    n = _cells_for(probe, 5)
    assert n * 10.0 / probe.cruise_groundspeed_kt * 60.0 <= 15
    assert _status(_ctx(_stretch(5, n, icing_zones=rime, **ESCAPE), NON_FIKI)) == AdvisoryStatus.AMBER
    assert _status(_ctx(_stretch(5, n, **ESCAPE), NON_FIKI)) == AdvisoryStatus.RED
    long_rime = _cells_for(probe, 15)
    assert _status(
        _ctx(_stretch(3, long_rime, icing_zones=rime, **ESCAPE), NON_FIKI)
    ) == AdvisoryStatus.RED


# --- the exposure clock ----------------------------------------------------


def test_clock_resets_only_in_above_freezing_air():
    """Two one-cell stretches stay short across warm air, add up across cold clear air."""
    probe = _ctx({}, NON_FIKI)
    per_cell = 10.0 / probe.cruise_groundspeed_kt * 60.0
    params = {**resolve_aircraft_icing_params(probe), "short_other_min": 1.5 * per_cell}
    both = {**_stretch(5, 1, **ESCAPE), **_stretch(7, 1, **ESCAPE)}

    def status(ctx):
        return grade_aircraft_icing_model(ctx, "gfs", params).status

    assert status(_ctx(both, NON_FIKI, warm={6})) == AdvisoryStatus.AMBER
    assert status(_ctx(both, NON_FIKI)) == AdvisoryStatus.RED
    assert len(grade_aircraft_icing_model(_ctx(both, NON_FIKI, warm={6}), "gfs", params).stretches) == 2


# --- escapes ---------------------------------------------------------------


def test_up_escape_only_for_fiki():
    """Down blocked by terrain, tops reachable: FIKI escapes up, non-FIKI cannot."""
    probe = _ctx({}, FIKI)
    n = _cells_for(probe, 5)
    iced = _stretch(5, n, **NO_ESCAPE)  # tops 9,000 ft -> 9,500 ft up-escape
    assert _status(_ctx(iced, FIKI)) == AdvisoryStatus.AMBER
    assert _status(_ctx(iced, NON_FIKI)) == AdvisoryStatus.RED
    # An aircraft ceiling too low for the climb-out removes it.
    low = AircraftEquipage(known=True, fiki=True, ceiling_ft=10000)
    assert _status(_ctx(iced, low)) == AdvisoryStatus.RED


def test_no_down_escape_under_freezing_rain():
    fzra = PrecipitationAssessment(freezing_rain_risk=True, warm_nose_base_ft=3000)
    ctx = _ctx(_stretch(5, 1, _precip=fzra, **ESCAPE), NON_FIKI)
    params = resolve_aircraft_icing_params(ctx)
    grade = grade_aircraft_icing_model(ctx, "gfs", params)
    assert grade.status == AdvisoryStatus.RED
    assert grade.worst_reason == "short_no_escape"
    # Without the warm nose the same stretch has its escape back.
    assert _status(_ctx(_stretch(5, 1, **ESCAPE), NON_FIKI)) == AdvisoryStatus.AMBER


def test_unknown_terrain_blocks_the_down_escape():
    ctx = _ctx(_stretch(5, 1, **ESCAPE), NON_FIKI)
    ctx = RouteContext(**{**ctx.__dict__, "elevation": None})
    assert _status(ctx) == AdvisoryStatus.RED


def test_climb_through_cloud_counts():
    """Icing only in the departure column: climb minutes at 500 fpm."""
    ctx = _ctx({0: {"freezing_level_ft": 1000, "icing_zones": [_zone(base=2000, top=6000)]}}, NON_FIKI)
    params = resolve_aircraft_icing_params(ctx)
    grade = grade_aircraft_icing_model(ctx, "gfs", params)
    assert grade.status == AdvisoryStatus.RED
    assert grade.worst is not None and grade.worst.phases == ["climb"]
    assert grade.worst.minutes == pytest.approx(8.0)


# --- labels and equipage ---------------------------------------------------


@pytest.mark.parametrize(("equipage", "label"), [
    (FIKI, "Graded for FIKI"),
    (NON_FIKI, "Graded for non-FIKI"),
    (None, "Graded as non-FIKI (set your aircraft's equipage)"),
    (AircraftEquipage(known=False, fiki=True), "Graded as non-FIKI (set your aircraft's equipage)"),
])
def test_card_states_its_column(equipage, label):
    ctx = _ctx(_stretch(5, 1, **ESCAPE), equipage)
    result = AircraftIcingEvaluator.evaluate(ctx, resolve_aircraft_icing_params(ctx))
    assert result.aggregate_detail.startswith(label)
    green = AircraftIcingEvaluator.evaluate(_ctx({}, equipage), resolve_aircraft_icing_params(ctx))
    assert green.aggregate_detail.startswith(label)


def test_unknown_aircraft_grades_as_non_fiki():
    probe = _ctx({}, None)
    iced = _stretch(5, _cells_for(probe, 5), **NO_ESCAPE)  # FIKI would escape up
    assert _status(_ctx(iced, None)) == AdvisoryStatus.RED
    assert _status(_ctx(iced, AircraftEquipage(known=False, fiki=True))) == AdvisoryStatus.RED
    assert _status(_ctx(iced, FIKI)) == AdvisoryStatus.AMBER


def test_escape_level_is_a_mitigation():
    ctx = _ctx(_stretch(5, 1, **ESCAPE), NON_FIKI)
    result = AircraftIcingEvaluator.evaluate(ctx, resolve_aircraft_icing_params(ctx))
    escapes = [m for m in result.aggregate_mitigations if m.addresses == "icing_escape"]
    assert escapes and escapes[0].altitude_ft == 4500
    assert result.aggregate_status == AdvisoryStatus.AMBER  # advice only


# --- consumers -------------------------------------------------------------


@pytest.mark.parametrize("equipage", [FIKI, NON_FIKI, None])
def test_ifr_feasibility_icing_axis_is_the_card(equipage):
    probe = _ctx({}, equipage)
    ctx = _ctx(_stretch(5, _cells_for(probe, 5), **ESCAPE), equipage)
    card = AircraftIcingEvaluator.evaluate(ctx, resolve_aircraft_icing_params(ctx))
    ifr = IFRFeasibilityEvaluator.evaluate(ctx, {"min_dep_ceiling_ft": 200, "min_arr_ceiling_ft": 400})
    # No airports or convection here: the composite is exactly the icing axis.
    assert [m.status for m in ifr.per_model] == [m.status for m in card.per_model]


def test_retired_cards_are_never_evaluated():
    ids = {e.id for e in get_catalog()}
    assert "aircraft_icing" in ids
    assert not {"icing_escape", "fiki_icing"} & ids
    assert {"icing_escape", "fiki_icing"} <= {e.id for e in get_catalog(include_retired=True)}
    ctx = _ctx(_stretch(5, 1, **ESCAPE), NON_FIKI)
    results = evaluate_all(ctx, enabled_ids={"icing_escape", "fiki_icing", "aircraft_icing"})
    assert [r.advisory_id for r in results] == ["aircraft_icing"]


# --- settings migration ----------------------------------------------------


@pytest.mark.parametrize(("enabled", "expected"), [
    ({"icing_escape": False}, False),                       # fiki default off
    ({"icing_escape": False, "fiki_icing": False}, False),
    ({"icing_escape": False, "fiki_icing": True}, None),    # FIKI card was on
    ({"icing_escape": True}, None),
    ({"cloud_top": False}, None),                           # icing_escape default on
    ({"icing_escape": False, "aircraft_icing": True}, True),  # already decided
])
def test_settings_migration(enabled, expected):
    settings = {"advisories": {"enabled": dict(enabled)}}
    new, changed = migrate_icing_enabled(settings)
    assert new["advisories"]["enabled"].get("aircraft_icing") == expected
    assert changed == (expected is False and "aircraft_icing" not in enabled)
    # Old keys are kept, inert.
    for k, v in enabled.items():
        assert new["advisories"]["enabled"][k] == v


def test_settings_migration_leaves_uncustomized_profiles():
    for settings in ({}, {"advisories": {}}, {"advisories": {"enabled": {}}}):
        assert migrate_icing_enabled(settings) == (settings, False)


def test_no_single_escape_level_quoted_into_higher_terrain():
    """Point A escapes below 4,500 ft over 500 ft terrain; point B only below
    6,500 ft over 4,000 ft terrain. "Descend below 4,500 ft" would be into B's
    hill, so no single level is quoted, yet the escape still exists."""
    from weatherbrief.models import ElevationPoint, ElevationProfile

    iced = {
        5: {"freezing_level_ft": 5000, "icing_zones": [_zone()]},
        6: {"freezing_level_ft": 7000, "icing_zones": [_zone()]},
    }
    ctx = _ctx(iced, NON_FIKI)
    elev = ElevationProfile(
        route_name="t", total_distance_nm=200.0, max_elevation_ft=4000,
        points=[
            ElevationPoint(distance_nm=d, elevation_ft=4000 if 56 <= d <= 64 else 500, lat=0, lon=0)
            for d in range(0, 201, 2)
        ],
    )
    ctx = RouteContext(**{**ctx.__dict__, "elevation": elev})
    params = {**resolve_aircraft_icing_params(ctx), "short_other_min": 60}
    grade = grade_aircraft_icing_model(ctx, "gfs", params)
    assert grade.status == AdvisoryStatus.AMBER
    assert "level varies with terrain" in grade.detail
    result = AircraftIcingEvaluator.evaluate(ctx, params)
    assert not [m for m in result.aggregate_mitigations if m.addresses == "icing_escape"]
