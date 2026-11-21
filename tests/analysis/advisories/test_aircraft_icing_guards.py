"""Guards for aircraft-icing behaviour that the mutation run found unchecked.

A mutmut run over ``aircraft_icing.py`` and ``sounding/sld.py`` (2026-11-21)
killed 63% of mutants at 96% line coverage: the lines ran, their results were
not asserted. Each test here pins one behaviour a surviving mutant could break
without any other test noticing. The three ``test_metamorphic_*`` checks run
rules (not fixed outputs) over every example route built in this file.
"""

from __future__ import annotations

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.aircraft_icing import (
    SLD_MODE_ENV,
    grade_aircraft_icing_model,
    icing_picture,
    resolve_aircraft_icing_params,
)
from weatherbrief.analysis.flight_path import PRESETS, compute_flight_path
from weatherbrief.analysis.sounding.sld import assess_sld_aloft_zones
from weatherbrief.models import (
    AdvisoryStatus,
    AircraftEquipage,
    DerivedLevel,
    IcingRisk,
    IcingType,
    IcingZone,
    SldZone,
    SoundingAnalysis,
)

from .conftest import _make_elevation, _make_rpa, _make_sounding

CRUISE = 8000
N = 21  # 0..200 nm every 10 nm
FIKI = AircraftEquipage(known=True, fiki=True, ceiling_ft=20000)
NON_FIKI = AircraftEquipage(known=True, fiki=False, ceiling_ft=20000)
RANK = {AdvisoryStatus.GREEN: 0, AdvisoryStatus.AMBER: 1, AdvisoryStatus.RED: 2}

CLEAN = {"freezing_level_ft": 5000}   # cold and clear at cruise: no reset
WARM = {"freezing_level_ft": 12000}   # cruise below 0 °C level: the clock resets
CONFIRMED_SLD = SldZone(base_ft=0, top_ft=3000, risk=IcingRisk.MODERATE, mechanism="warm_nose")


@pytest.fixture(autouse=True)
def _shadow_sld(monkeypatch):
    """Every test starts from the production default unless it says otherwise."""
    monkeypatch.delenv(SLD_MODE_ENV, raising=False)


def _zone(base=7000, top=9000, risk=IcingRisk.MODERATE, kind=IcingType.MIXED):
    return IcingZone(base_ft=base, top_ft=top, risk=risk, icing_type=kind)


def _rime(base=7000, top=9000):
    return _zone(base, top, IcingRisk.LIGHT, IcingType.RIME)


def _sounding(spec) -> SoundingAnalysis | None:
    """``None`` = no sounding (an unassessed point); a dict = ``_make_sounding`` kwargs
    plus optional ``sld_zones`` / ``derived_levels``."""
    if spec is None:
        return None
    spec = dict(spec)
    extra = {k: spec.pop(k) for k in ("sld_zones", "derived_levels") if k in spec}
    s = _make_sounding(**spec)
    return s.model_copy(update=extra) if extra else s


def _ctx(points: dict[int, dict | None], equipage=NON_FIKI, *, path=False,
         terrain_ft=500.0, default=CLEAN) -> RouteContext:
    analyses = []
    for i in range(N):
        s = _sounding(points.get(i, default))
        analyses.append(_make_rpa(i, i * 10.0, {"gfs": s} if s is not None else {}))
    fp = None
    if path:
        fp = compute_flight_path(
            [i * 10.0 for i in range(N)], CRUISE, PRESETS["hp_piston"], terrain_ft, terrain_ft,
            cruise_ias_kt=120,
        )
    return RouteContext(
        analyses=analyses, cross_sections=[], elevation=_make_elevation(terrain_ft, 21, 200),
        models=["gfs"], cruise_altitude_ft=CRUISE, flight_ceiling_ft=18000,
        total_distance_nm=200.0, cruise_speed_ias_kt=120, equipage=equipage,
        flight_path=fp, flight_path_live=path,
    )


def _grade(ctx):
    return grade_aircraft_icing_model(ctx, "gfs", resolve_aircraft_icing_params(ctx))


def _iced(indices, zones=None, **sounding) -> dict[int, dict]:
    base = {**CLEAN, **sounding}
    return {i: {**base, "icing_zones": list(zones or [_zone()])} for i in indices}


# Every example route below is registered here so the metamorphic checks can
# run their rules over all of them.
EXAMPLES: dict[str, dict] = {}


def _example(name, points, *, path=False, terrain_ft=500.0, default=CLEAN):
    EXAMPLES[name] = {"points": points, "path": path, "terrain_ft": terrain_ft, "default": default}
    return points


# --- 1. the flight path (#1005, live in prod) --------------------------------


def _path_cruise_minutes(ctx, indices):
    picture = icing_picture(ctx, "gfs", resolve_aircraft_icing_params(ctx))
    return sum(picture[i].level_nm for i in indices) / ctx.cruise_groundspeed_kt * 60.0


@pytest.mark.parametrize("indices,expected", [((10,), AdvisoryStatus.AMBER),
                                               ((10, 11), AdvisoryStatus.RED)])
def test_path_cruise_ice_is_timed_from_the_level_leg(indices, expected):
    """Ice at cruise mid-route on the path: the level leg's minutes at groundspeed,
    short (one cell, ~4.4 min) AMBER with an escape, long (two cells) RED."""
    points = _example(f"path_cruise_{len(indices)}", _iced(indices), path=True)
    ctx = _ctx(points, path=True)
    grade = _grade(ctx)
    [stretch] = grade.stretches
    assert stretch.phases == ["cruise"]
    assert stretch.spans["cruise"] == [indices[0] * 10.0, indices[-1] * 10.0]
    assert stretch.minutes == pytest.approx(_path_cruise_minutes(ctx, indices))
    assert stretch.escape
    assert grade.status == expected


def test_path_escape_down_is_blocked_by_high_terrain():
    """Same short cruise exposure: escape below 4,500 ft is AMBER over 500 ft terrain,
    RED over 4,000 ft terrain (floor 5,000 ft)."""
    points = _iced([10])
    low = _grade(_ctx(points, path=True))
    _example("path_high_terrain", points, path=True, terrain_ft=4000.0)
    high = _grade(_ctx(points, path=True, terrain_ft=4000.0))
    assert low.status == AdvisoryStatus.AMBER and low.worst.escape
    assert high.status == AdvisoryStatus.RED and not high.worst.escape
    point = high.worst.points[0]
    assert point.escape_floor_ft == pytest.approx(5000)
    assert point.down_block == "terrain"


def test_path_fiki_escape_up_needs_the_ceiling():
    """FIKI on the path: up-escape 500 ft above the top, but only below ceiling - 1,000 ft."""
    points = _iced([10], freezing_level_ft=1000)  # no escape down (terrain + margin)
    ok = _grade(_ctx(points, FIKI, path=True))
    assert ok.worst.points[0].escape_up_ft == pytest.approx(9500)
    low_ceiling = AircraftEquipage(known=True, fiki=True, ceiling_ft=10000)
    blocked = _grade(_ctx(points, low_ceiling, path=True))
    assert blocked.worst.points[0].escape_up_ft is None


@pytest.mark.parametrize("spans,feet", [
    ([], 0), ([(5000, 5000)], 0),
    ([(3000, 5000), (4000, 6000)], 3000),   # overlap counted once
    ([(4000, 6000), (3000, 5000)], 3000),   # order does not matter
    ([(3000, 6000), (4000, 5000)], 3000),   # nested
    ([(0, 1000), (1000, 2000)], 2000),      # touching
    ([(0, 1000), (2000, 3000)], 2000),      # disjoint: both counted
    ([(0, 1000), (2000, 3000), (2500, 4000)], 3000),
])
def test_iced_thickness_is_the_union_of_the_zones(spans, feet):
    from weatherbrief.analysis.advisories.aircraft_icing import _iced_thickness
    assert _iced_thickness([_rime(lo, hi) for lo, hi in spans]) == pytest.approx(feet)


def test_path_overlapping_zones_count_once_in_the_climb():
    """Two overlapping zones (3-5,000 and 4-6,000 ft) ice 3,000 ft of the climb, not 4,000."""
    split = _example("path_overlap", _iced(range(4), [_rime(3000, 5000), _rime(4000, 6000)],
                                          freezing_level_ft=2500), path=True)
    merged = _iced(range(4), [_rime(3000, 6000)], freezing_level_ft=2500)
    a = _grade(_ctx(split, path=True)).stretches[0]
    b = _grade(_ctx(merged, path=True)).stretches[0]
    assert a.phases == b.phases == ["climb"]
    assert a.minutes == pytest.approx(b.minutes)


def test_path_warm_cruise_resets_the_clock_but_cold_clear_air_does_not():
    ice = {**_iced(range(4), [_rime(3000, 5000)], freezing_level_ft=2500),
           **_iced(range(17, N), [_rime(3000, 5000)], freezing_level_ft=2500)}
    warm = _example("path_warm_between", {**{i: WARM for i in range(4, 17)}, **ice}, path=True)
    cold = _example("path_cold_between", ice, path=True)
    assert [s.phases for s in _grade(_ctx(warm, path=True)).stretches] == [["climb"], ["descent"]]
    [one] = _grade(_ctx(cold, path=True)).stretches
    assert one.phases == ["climb", "descent"]


# --- 2. an unassessed point neither runs nor stops the clock -----------------


@pytest.mark.parametrize("path", [False, True])
def test_icing_after_an_unassessed_point_is_still_graded(path):
    points = _example(f"gap_then_ice_{'path' if path else 'flat'}",
                      {5: None, **_iced([8, 9, 10])}, path=path)
    grade = _grade(_ctx(points, path=path))
    assert grade.status == AdvisoryStatus.RED
    assert {p.index for p in grade.worst.points} == {8, 9, 10}
    assert grade.samples[5].assessed is False


@pytest.mark.parametrize("path", [False, True])
def test_an_unassessed_point_inside_a_stretch_does_not_reset_it(path):
    """Two short exposures either side of a gap are one long stretch (RED), not two."""
    points = _example(f"gap_inside_{'path' if path else 'flat'}",
                      {**_iced([9]), 10: None, **_iced([11])}, path=path)
    grade = _grade(_ctx(points, path=path))
    assert len(grade.stretches) == 1
    assert grade.status == AdvisoryStatus.RED


# --- 3. descent exposure in flat mode ----------------------------------------


def test_flat_descent_through_ice_at_the_destination_is_timed_at_500_fpm():
    points = _example("flat_descent", _iced([N - 1], [_rime(3000, 5000)], freezing_level_ft=2500))
    grade = _grade(_ctx(points))
    [stretch] = grade.stretches
    assert stretch.phases == ["descent"]
    assert stretch.minutes == pytest.approx(2000 / 500)
    assert grade.status != AdvisoryStatus.GREEN


def test_flat_descent_minutes_add_to_a_cruise_stretch_at_the_destination():
    """Cruise ice at 190 nm, then the descent at 200 nm through 1,000 ft (7-8,000) +
    2,000 ft (3-5,000) of ice. The last cell's cruise time (5 nm) is covered by the
    descent's 6 min, so it adds nothing on top (max, not sum)."""
    points = _example("flat_cruise_then_descent", {
        **_iced([N - 2], [_rime(7000, 9000)]),
        **_iced([N - 1], [_rime(7000, 9000), _rime(3000, 5000)], freezing_level_ft=2500),
    })
    ctx = _ctx(points)
    [stretch] = _grade(ctx).stretches
    assert stretch.phases == ["cruise", "descent"]
    per_nm = 60.0 / ctx.cruise_groundspeed_kt
    descent = 3000 / 500
    assert stretch.minutes == pytest.approx(10 * per_nm + max(5 * per_nm - descent, 0) + descent)


# --- 4. SLD on the card in live mode (before WB_SLD_ICING=live) --------------


def _aloft_levels(base, top, t=-5.0, qr=0.05) -> list[DerivedLevel]:
    """Levels every 1,000 ft with rain water at ``t`` from ``base`` to ``top``."""
    out = []
    for i, alt in enumerate(range(0, 16000, 1000)):
        inside = any(lo <= alt <= hi for lo, hi in ([(base, top)] if base is not None else []))
        out.append(DerivedLevel(
            pressure_hpa=1000 - 30 * i, altitude_ft=alt,
            temperature_c=t if inside else 10.0 - alt / 500,
            rain_water_g_kg=qr if inside else 0.0,
        ))
    return out


def test_live_confirmed_sld_in_the_climb_takes_over_a_lesser_stretch(monkeypatch):
    """A short light-rime stretch (AMBER) plus confirmed SLD in the climb: RED, SLD leads."""
    monkeypatch.setenv(SLD_MODE_ENV, "live")
    points = {0: {**CLEAN, "sld_zones": [CONFIRMED_SLD]}, **_iced([10], [_rime()])}
    grade = _grade(_ctx(points, terrain_ft=0.0))
    assert grade.stretches  # the rime stretch is still there ...
    assert grade.status == AdvisoryStatus.RED  # ... but SLD sets the grade
    assert grade.worst_reason == "sld"
    # Per point, each highlight carries its own worst: the SLD climb RED, the rime AMBER.
    assert grade.samples[0].severity.value == "red"
    assert grade.samples[10].severity.value == "amber"
    assert (grade.samples[10].region.base_ft, grade.samples[10].region.top_ft) == (7000, 9000)
    label = grade.detail.split(":")[0]
    rest = grade.detail[len(label) + 2:]
    assert rest[0].isupper()  # the stretch sentence follows, re-capitalised


def test_live_sld_aloft_leads_an_equal_amber_stretch(monkeypatch):
    monkeypatch.setenv(SLD_MODE_ENV, "live")
    points = {10: {**CLEAN, "derived_levels": _aloft_levels(7000, 9000),
                   "icing_zones": [_rime()]}}
    grade = _grade(_ctx(points, FIKI, terrain_ft=0.0))
    assert grade.status == AdvisoryStatus.AMBER
    assert grade.worst_reason == "sld_aloft"


def test_live_sld_in_the_path_descent_and_cruise(monkeypatch):
    """On the path, SLD is read from each part the cell flies: the descent at the
    end (confirmed: RED) and the level leg mid-route (aloft: AMBER)."""
    monkeypatch.setenv(SLD_MODE_ENV, "live")
    descent = {i: {**CLEAN, "sld_zones": [CONFIRMED_SLD]} for i in range(N - 3, N)}
    grade = _grade(_ctx(descent, FIKI, path=True, terrain_ft=0.0))
    assert grade.status == AdvisoryStatus.RED and grade.worst_reason == "sld"
    assert "descent" in grade.detail.lower()
    cruise = {10: {**CLEAN, "derived_levels": _aloft_levels(7000, 9000)}}
    grade = _grade(_ctx(cruise, FIKI, path=True, terrain_ft=0.0))
    assert grade.status == AdvisoryStatus.AMBER and grade.worst_reason == "sld_aloft"
    assert grade.samples[10].severity.value == "amber"
    assert grade.samples[9].severity.value == "green"


def test_live_confirmed_sld_in_the_path_climb_is_red(monkeypatch):
    monkeypatch.setenv(SLD_MODE_ENV, "live")
    points = {i: {**CLEAN, "sld_zones": [CONFIRMED_SLD]} for i in range(3)}
    grade = _grade(_ctx(points, FIKI, path=True, terrain_ft=0.0))
    assert grade.status == AdvisoryStatus.RED
    assert grade.worst_reason == "sld"
    assert "climb" in grade.detail.lower()


def test_sld_off_mode_ignores_sld_entirely(monkeypatch):
    monkeypatch.setenv(SLD_MODE_ENV, "off")
    points = {0: {**CLEAN, "sld_zones": [CONFIRMED_SLD]}, **_iced([10], [_rime()])}
    grade = _grade(_ctx(points, FIKI, terrain_ft=0.0))
    assert grade.status == AdvisoryStatus.AMBER
    assert grade.worst_reason != "sld"
    assert grade.shadow == []


# --- 5. SLD bands aloft --------------------------------------------------------


def test_a_too_shallow_band_does_not_hide_a_deeper_band_above_it():
    levels = _aloft_levels(8000, 10000)
    # A single cold rain-water level at 4,000 ft: no depth, never a band.
    levels[4] = levels[4].model_copy(update={"temperature_c": -5.0, "rain_water_g_kg": 0.05})
    zones = assess_sld_aloft_zones(levels, max_temp_c=-2.0, min_depth_ft=1000.0)
    assert [(z.base_ft, z.top_ft) for z in zones] == [(8000, 10000)]


def test_a_band_overlapping_a_confirmed_zone_is_dropped_but_a_touching_one_is_kept():
    levels = _aloft_levels(7000, 9000)
    over = SldZone(base_ft=8000, top_ft=12000, risk=IcingRisk.MODERATE, mechanism="warm_nose")
    below = SldZone(base_ft=0, top_ft=7000, risk=IcingRisk.MODERATE, mechanism="warm_nose")
    above = SldZone(base_ft=9000, top_ft=12000, risk=IcingRisk.MODERATE, mechanism="warm_nose")
    assert assess_sld_aloft_zones(levels, [over], max_temp_c=-2.0, min_depth_ft=1000.0) == []
    for touch in (below, above):
        kept = assess_sld_aloft_zones(levels, [touch], max_temp_c=-2.0, min_depth_ft=1000.0)
        assert [(z.base_ft, z.top_ft) for z in kept] == [(7000, 9000)]


def test_explicit_thresholds_win_over_the_defaults(monkeypatch):
    monkeypatch.delenv("WB_SLD_ALOFT_MAX_TEMP_C", raising=False)
    monkeypatch.delenv("WB_SLD_ALOFT_MIN_DEPTH_FT", raising=False)
    levels = _aloft_levels(7000, 9000, t=-3.0)  # caught by the defaults (-2 °C, 1,000 ft)
    assert assess_sld_aloft_zones(levels)
    assert assess_sld_aloft_zones(levels, max_temp_c=-4.0, min_depth_ft=1000.0) == []
    assert assess_sld_aloft_zones(levels, max_temp_c=-2.0, min_depth_ft=3000.0) == []
    # Either one alone overrides its default and keeps the other's.
    assert assess_sld_aloft_zones(levels, max_temp_c=-4.0) == []
    assert assess_sld_aloft_zones(levels, min_depth_ft=3000.0) == []
    assert assess_sld_aloft_zones(levels, max_temp_c=-2.0)
    assert assess_sld_aloft_zones(levels, min_depth_ft=1000.0)


def test_a_band_exactly_the_minimum_depth_is_kept():
    zones = assess_sld_aloft_zones(_aloft_levels(7000, 8000), max_temp_c=-2.0, min_depth_ft=1000.0)
    assert [(z.base_ft, z.top_ft) for z in zones] == [(7000, 8000)]


def test_a_dropped_band_does_not_hide_the_next_one():
    """Lower band overlaps a confirmed zone (dropped); the band above it stays."""
    levels = _aloft_levels(3000, 5000)
    for i in range(9, 12):  # second band 9-11,000 ft
        levels[i] = levels[i].model_copy(update={"temperature_c": -8.0, "rain_water_g_kg": 0.05})
    confirmed = SldZone(base_ft=0, top_ft=4000, risk=IcingRisk.MODERATE, mechanism="warm_nose")
    zones = assess_sld_aloft_zones(levels, [confirmed], max_temp_c=-2.0, min_depth_ft=1000.0)
    assert [(z.base_ft, z.top_ft) for z in zones] == [(9000, 11000)]


# --- metamorphic checks over every example above -------------------------------

# Extra examples that only the metamorphic checks use.
_example("flat_long_moderate", _iced(range(8, 14)))
_example("flat_short_rime", _iced([10], [_rime()]))
_example("flat_no_escape", _iced([9, 10], freezing_level_ft=1000))
_example("flat_severe", _iced([10], [_zone(risk=IcingRisk.SEVERE)]))
_example("flat_warm_split", {**_iced([5]), **{i: WARM for i in range(6, 12)}, **_iced([12])})
_example("path_climb_ice", _iced(range(4), [_rime(3000, 5000)], freezing_level_ft=2500), path=True)
_example("clean_route", {})


def _build(example, equipage=NON_FIKI, points=None):
    return _ctx(points if points is not None else example["points"], equipage,
                path=example["path"], terrain_ft=example["terrain_ft"],
                default=example["default"])


def _status(ctx):
    return RANK[_grade(ctx).status]


def _with_zone(spec, zone):
    spec = dict(spec or CLEAN)
    spec["icing_zones"] = [*spec.get("icing_zones", []), zone]
    return spec


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_metamorphic_fiki_is_never_graded_worse_than_non_fiki(name):
    ex = EXAMPLES[name]
    assert _status(_build(ex, FIKI)) <= _status(_build(ex, NON_FIKI))


@pytest.mark.parametrize("name", sorted(EXAMPLES))
@pytest.mark.parametrize("equipage", [FIKI, NON_FIKI], ids=["fiki", "non_fiki"])
def test_metamorphic_adding_an_icing_zone_never_lowers_the_grade(name, equipage):
    ex = EXAMPLES[name]
    before = _status(_build(ex, equipage))
    for i in (0, 5, 10, 15, N - 1):
        spec = ex["points"].get(i, ex["default"])
        if spec is None:
            continue
        for zone in (_rime(), _zone(3000, 5000), _zone(risk=IcingRisk.SEVERE)):
            points = {**ex["points"], i: _with_zone(spec, zone)}
            after = _status(_build(ex, equipage, points))
            assert after >= before, f"zone {zone.base_ft}-{zone.top_ft} {zone.risk} at {i}"


@pytest.mark.parametrize("name", sorted(EXAMPLES))
@pytest.mark.parametrize("equipage", [FIKI, NON_FIKI], ids=["fiki", "non_fiki"])
def test_metamorphic_blanking_an_ice_free_point_never_lowers_the_grade(name, equipage):
    """A missing sounding where there is no ice can only remove a reset, never hide ice.

    (Blanking an *iced* point can rightly lower the grade: that ice is no
    longer known. The sample then reads UNAVAILABLE, which the card shows.)
    """
    ex = EXAMPLES[name]
    ctx = _build(ex, equipage)
    before = _status(ctx)
    picture = icing_picture(ctx, "gfs", resolve_aircraft_icing_params(ctx))
    for p in picture:
        iced = p.cruise_zones or p.column_zones or any(v.zones for v in p.vertical)
        if not p.assessed or iced or p.sld_cruise or p.sld_column:
            continue
        points = {**ex["points"], p.index: None}
        assert _status(_build(ex, equipage, points)) >= before, f"blanked {p.index}"
