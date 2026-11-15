"""Two-level SLD on the aircraft icing card (#411 step 4, meteorology-decisions §84).

Confirmed SLD (a freezing-rain zone that reaches the surface) is RED when the
climb, cruise or descent crosses it and a wall for the solver. Unconfirmed SLD
aloft (rain water colder than about -2 °C) is AMBER at most and never a wall.
``WB_SLD_ICING`` runs it in shadow by default: recorded, not graded.
"""

from __future__ import annotations

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.aircraft_icing import (
    SLD_MODE_ENV,
    AircraftIcingEvaluator,
    grade_aircraft_icing_model,
    icing_cell_cost,
    resolve_aircraft_icing_params,
    sld_icing_mode,
)
from weatherbrief.analysis.advisories.vertical_profile import INF
from weatherbrief.analysis.sounding.sld import (
    SLD_ALOFT_MAX_TEMP_C,
    assess_sld_aloft_zones,
)
from weatherbrief.models import (
    AdvisoryStatus,
    AircraftEquipage,
    DerivedLevel,
    IcingRisk,
    IcingType,
    IcingZone,
    SldZone,
)

from .conftest import _make_elevation, _make_rpa, _make_sounding

CRUISE = 8000
N_POINTS = 21
FIKI = AircraftEquipage(known=True, fiki=True, ceiling_ft=20000)
NON_FIKI = AircraftEquipage(known=True, fiki=False, ceiling_ft=20000)

CONFIRMED = SldZone(base_ft=0, top_ft=3000, risk=IcingRisk.MODERATE, mechanism="warm_nose")


def _level(p: int, alt: float, t: float, qr_g_kg: float | None) -> DerivedLevel:
    return DerivedLevel(pressure_hpa=p, altitude_ft=alt, temperature_c=t, rain_water_g_kg=qr_g_kg)


def _aloft_levels(base=7000, top=9000, t=-5.0, qr=0.05) -> list[DerivedLevel]:
    """A clean profile with a rain-water band from ``base`` to ``top``."""
    out = []
    for i, alt in enumerate(range(0, 16000, 1000)):
        in_band = base <= alt <= top
        out.append(_level(1000 - 30 * i, alt, 10.0 - alt / 500 if not in_band else t,
                          qr if in_band else 0.0))
    return out


def _ctx(points: dict[int, dict], equipage=FIKI) -> RouteContext:
    analyses = []
    for i in range(N_POINTS):
        sounding = _make_sounding(freezing_level_ft=5000)
        if i in points:
            sounding = sounding.model_copy(update=points[i])
        analyses.append(_make_rpa(i, i * 10.0, {"gfs": sounding}))
    return RouteContext(
        analyses=analyses, cross_sections=[], elevation=_make_elevation(0, 21, 200),
        models=["gfs"], cruise_altitude_ft=CRUISE, flight_ceiling_ft=18000,
        total_distance_nm=200.0, cruise_speed_ias_kt=120, equipage=equipage,
    )


def _grade(ctx):
    return grade_aircraft_icing_model(ctx, "gfs", resolve_aircraft_icing_params(ctx))


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv(SLD_MODE_ENV, "live")


# --- the detector ----------------------------------------------------------


def test_aloft_band_needs_depth_and_cold():
    assert len(assess_sld_aloft_zones(_aloft_levels(7000, 9000, t=-5.0))) == 1
    # A single level has no depth.
    assert assess_sld_aloft_zones(_aloft_levels(8000, 8000, t=-5.0)) == []
    # Rain water just below freezing is the ordinary shower case: not a band.
    assert assess_sld_aloft_zones(_aloft_levels(7000, 9000, t=-1.0)) == []
    # Rain water under the supercooled-rain floor does not count.
    assert assess_sld_aloft_zones(_aloft_levels(7000, 9000, qr=0.0001)) == []


def test_aloft_band_shape_and_thresholds_are_tunable(monkeypatch):
    zones = assess_sld_aloft_zones(_aloft_levels(7000, 9000, t=-5.0))
    z = zones[0]
    assert (z.base_ft, z.top_ft, z.mechanism, z.confirmed) == (7000, 9000, "rain_aloft", False)
    assert z.mean_temperature_c == -5.0
    assert SLD_ALOFT_MAX_TEMP_C == -2.0
    monkeypatch.setenv("WB_SLD_ALOFT_MIN_DEPTH_FT", "3000")
    assert assess_sld_aloft_zones(_aloft_levels(7000, 9000, t=-5.0)) == []
    monkeypatch.setenv("WB_SLD_ALOFT_MAX_TEMP_C", "-6")
    monkeypatch.setenv("WB_SLD_ALOFT_MIN_DEPTH_FT", "1000")
    assert assess_sld_aloft_zones(_aloft_levels(7000, 9000, t=-5.0)) == []


def test_aloft_band_overlapping_a_confirmed_zone_is_dropped():
    confirmed = SldZone(base_ft=0, top_ft=7500, mechanism="warm_nose")
    assert assess_sld_aloft_zones(_aloft_levels(7000, 9000), [confirmed]) == []


def test_models_without_rain_water_yield_nothing():
    levels = [lv.model_copy(update={"rain_water_g_kg": None}) for lv in _aloft_levels()]
    assert assess_sld_aloft_zones(levels) == []


def test_only_warm_nose_is_confirmed():
    assert SldZone(base_ft=0, top_ft=1, mechanism="warm_nose").confirmed
    assert not SldZone(base_ft=0, top_ft=1, mechanism="coalescence").confirmed
    assert not SldZone(base_ft=0, top_ft=1, mechanism="rain_aloft").confirmed


# --- mode ------------------------------------------------------------------


@pytest.mark.parametrize(("value", "mode"), [
    (None, "shadow"), ("", "shadow"), ("live", "live"), ("LIVE", "live"),
    ("off", "off"), ("bogus", "shadow"),
])
def test_mode(monkeypatch, value, mode):
    if value is None:
        monkeypatch.delenv(SLD_MODE_ENV, raising=False)
    else:
        monkeypatch.setenv(SLD_MODE_ENV, value)
    assert sld_icing_mode() == mode


# --- live grading ----------------------------------------------------------


def test_confirmed_zone_on_the_climb_is_red(live):
    ctx = _ctx({0: {"sld_zones": [CONFIRMED]}})
    g = _grade(ctx)
    assert g.status == AdvisoryStatus.RED
    assert g.worst_reason == "sld"
    assert "climb" in g.detail
    assert g.shadow == []


def test_confirmed_zone_on_the_descent_is_red(live):
    g = _grade(_ctx({N_POINTS - 1: {"sld_zones": [CONFIRMED]}}))
    assert g.status == AdvisoryStatus.RED and "descent" in g.detail


def test_confirmed_zone_below_cruise_mid_route_is_not_crossed(live):
    """En route the aircraft is at cruise, above the surface-based zone."""
    g = _grade(_ctx({10: {"sld_zones": [CONFIRMED]}}))
    assert g.status == AdvisoryStatus.GREEN


def test_aloft_band_at_cruise_is_amber_for_both_columns(live):
    for equipage in (FIKI, NON_FIKI):
        g = _grade(_ctx({10: {"derived_levels": _aloft_levels(7000, 9000)}}, equipage))
        assert g.status == AdvisoryStatus.AMBER, equipage
        assert g.worst_reason == "sld_aloft"
        assert "cannot confirm" in g.detail and "-2°C" in g.detail


def test_aloft_band_never_lowers_a_red_stretch(live):
    """A long no-escape stretch stays RED; the aloft sentence follows it."""
    iced = {i: {"icing_zones": [IcingZone(base_ft=7000, top_ft=19500, risk=IcingRisk.MODERATE,
                                          icing_type=IcingType.MIXED)],
                "indices": None}
            for i in range(3, 15)}
    iced[10]["derived_levels"] = _aloft_levels(7000, 9000)
    g = _grade(_ctx(iced, NON_FIKI))
    assert g.status == AdvisoryStatus.RED
    assert g.worst_reason != "sld_aloft"
    assert "Possible SLD aloft" in g.detail


def test_aloft_band_highlight_carries_its_reason(live):
    ctx = _ctx({10: {"derived_levels": _aloft_levels(7000, 9000)}})
    g = _grade(ctx)
    cell = g.region_cells[10][1]
    assert cell is not None and cell.reason_code == "sld_aloft"
    assert (cell.base_ft, cell.top_ft) == (7000, 9000)


def test_solver_walls_confirmed_and_prices_aloft(live):
    confirmed = _make_sounding().model_copy(update={"sld_zones": [CONFIRMED]})
    assert icing_cell_cost(confirmed, 2000, fiki=True) == INF
    assert icing_cell_cost(confirmed, 5000, fiki=True) == 0.0
    aloft = _make_sounding().model_copy(update={"derived_levels": _aloft_levels(7000, 9000)})
    cost = icing_cell_cost(aloft, 8000, fiki=False)
    assert 0 < cost < INF


# --- shadow (the default) --------------------------------------------------


def test_shadow_records_without_grading(monkeypatch):
    monkeypatch.delenv(SLD_MODE_ENV, raising=False)
    ctx = _ctx({0: {"sld_zones": [CONFIRMED]}, 10: {"derived_levels": _aloft_levels()}})
    g = _grade(ctx)
    assert g.status == AdvisoryStatus.GREEN
    assert [(s.rule, s.status, s.reason_code, s.points) for s in g.shadow] == [
        ("sld_icing", AdvisoryStatus.RED, "sld", 2),
    ]
    assert icing_cell_cost(ctx.analyses[0].sounding["gfs"], 2000, fiki=True) == 0.0
    result = AircraftIcingEvaluator.evaluate(ctx, resolve_aircraft_icing_params(ctx))
    assert result.aggregate_status == AdvisoryStatus.GREEN
    assert result.per_model[0].shadow[0].status == AdvisoryStatus.RED


def test_shadow_aloft_only_is_amber(monkeypatch):
    monkeypatch.delenv(SLD_MODE_ENV, raising=False)
    g = _grade(_ctx({10: {"derived_levels": _aloft_levels()}}))
    assert g.status == AdvisoryStatus.GREEN
    assert g.shadow[0].status == AdvisoryStatus.AMBER
    assert g.shadow[0].reason_code == "sld_aloft"


def test_off_skips_it(monkeypatch):
    monkeypatch.setenv(SLD_MODE_ENV, "off")
    g = _grade(_ctx({0: {"sld_zones": [CONFIRMED]}}))
    assert g.status == AdvisoryStatus.GREEN and g.shadow == []
