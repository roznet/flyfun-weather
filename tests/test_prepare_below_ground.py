"""Profiles over high terrain drop the model's below-ground levels (#807).

Pressure-level products deliver every requested level whatever the terrain:
under 850 hPa ground the 1000/975/950/925/900/875 hPa levels are the model's
downward extrapolation (here deliberately warm and saturated, the shape that
painted phantom low cloud). ``prepare_profile`` clips them at the hour's
surface pressure and, when the first real level is well above the ground,
inserts the 2 m observation so the surface parcel launches from the ground.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from metpy.calc import lcl
from metpy.units import units

from weatherbrief.analysis.sounding import analyze_sounding, analyze_sounding_lite
from weatherbrief.analysis.sounding.prepare import prepare_profile
from weatherbrief.models import HourlyForecast, PressureLevelData

_LEVELS = [1000, 975, 950, 925, 900, 875, 850, 825, 800, 775, 750, 700,
           650, 600, 550, 500, 450, 400, 350, 300]


def _isa_height_m(p_hpa: float) -> float:
    return 44330.8 * (1.0 - (p_hpa / 1013.25) ** 0.190263)


def _column(
    *, ground_hpa: float | None, saturated_below_ground: bool = True,
) -> list[PressureLevelData]:
    """Unstable-ish column; levels under ``ground_hpa`` are saturated phantoms."""
    out = []
    for p in _LEVELS:
        z = _isa_height_m(p)
        t = 22.0 - 7.0 * z / 1000.0  # 7 K/km
        below = ground_hpa is not None and p > ground_hpa
        rh = 100.0 if (below and saturated_below_ground) else 45.0
        out.append(PressureLevelData(
            pressure_hpa=p,
            geopotential_height_m=round(z, 1),
            temperature_c=round(t, 2),
            relative_humidity_pct=rh,
            wind_speed_kt=10.0 + p / 100.0,
            wind_direction_deg=250.0,
            vertical_velocity_pa_s=-0.1,
        ))
    return out


def _hourly(levels, sp, *, t2=None, td2=None, ws10=6.0, wd10=200.0):
    if sp is not None and t2 is None:
        z = _isa_height_m(sp)
        t2 = 22.0 - 7.0 * z / 1000.0 + 2.0  # sunny surface, 2 K above the lapse
    return HourlyForecast(
        time="2026-07-01T14:00:00Z",
        surface_pressure_hpa=sp,
        temperature_2m_c=t2,
        dewpoint_2m_c=td2 if td2 is not None else (t2 - 8.0 if t2 is not None else None),
        wind_speed_10m_kt=ws10,
        wind_direction_10m_deg=wd10,
        pressure_levels=levels,
    )


_SP_TERRAIN = 840.0  # ~1,550 m model ground (850 hPa is 10 hPa inside it)


# --- clipping -------------------------------------------------------------


def test_terrain_profile_drops_below_ground_levels():
    levels = _column(ground_hpa=_SP_TERRAIN)
    prof = prepare_profile(levels, _hourly(levels, _SP_TERRAIN))
    p = prof.pressure.to("hPa").magnitude
    assert 1000 not in p and 925 not in p and 850 not in p
    assert p.max() == pytest.approx(_SP_TERRAIN)
    assert prof.clipped_levels == 7  # 1000..850
    assert prof.surface_level_inserted


def test_surface_level_is_the_2m_observation_and_arrays_stay_aligned():
    levels = _column(ground_hpa=_SP_TERRAIN)
    hourly = _hourly(levels, _SP_TERRAIN)
    prof = prepare_profile(levels, hourly)
    n = len(prof.pressure)
    assert n == len(_LEVELS) - 7 + 1
    for arr in (prof.temperature, prof.dewpoint, prof.wind_speed,
                prof.wind_direction, prof.height, prof.omega):
        assert len(arr) == n
    assert prof.temperature[0].m_as("degC") == pytest.approx(hourly.temperature_2m_c)
    assert prof.dewpoint[0].m_as("degC") == pytest.approx(hourly.dewpoint_2m_c)
    assert prof.wind_speed[0].m_as("knot") == pytest.approx(6.0)
    assert prof.wind_direction[0].m_as("degree") == pytest.approx(200.0)
    assert math.isnan(prof.omega[0].m_as("Pa/s"))
    # Surface height is the model orography (log-p between 850 and 825 hPa),
    # below the first kept level, and the height axis stays monotonic.
    h = prof.height.m_as("meter")
    assert _isa_height_m(850) < h[0] < _isa_height_m(825)
    assert np.all(np.diff(h) > 0)
    assert np.all(np.diff(prof.pressure.m_as("hPa")) < 0)


def test_cape_and_lcl_launch_from_the_surface():
    levels = _column(ground_hpa=_SP_TERRAIN)
    hourly = _hourly(levels, _SP_TERRAIN)
    sa = analyze_sounding_lite(levels, hourly)
    lcl_p, _ = lcl(_SP_TERRAIN * units.hPa,
                   hourly.temperature_2m_c * units.degC,
                   hourly.dewpoint_2m_c * units.degC)
    assert sa.indices.lcl_pressure_hpa == pytest.approx(lcl_p.m_as("hPa"), abs=2.0)
    assert sa.indices.lcl_pressure_hpa < _SP_TERRAIN
    assert sa.parcel_path[0].pressure_hpa == pytest.approx(_SP_TERRAIN, abs=1)

    # Pre-#807 the saturated 1000 hPa phantom was the parcel's origin.
    legacy = analyze_sounding_lite(levels, _hourly(levels, None, t2=None))
    assert legacy.indices.lcl_pressure_hpa > 990


def test_phantom_below_ground_saturation_no_longer_makes_cloud():
    levels = _column(ground_hpa=_SP_TERRAIN)
    ground_ft = _isa_height_m(_SP_TERRAIN) * 3.28084

    legacy = analyze_sounding(levels, _hourly(levels, None, t2=None))
    assert any(c.base_ft < ground_ft for c in legacy.dd_cloud_layers), (
        "fixture must reproduce the phantom deck without the clip"
    )

    fixed = analyze_sounding(levels, _hourly(levels, _SP_TERRAIN))
    assert all(c.base_ft >= ground_ft - 50 for c in fixed.dd_cloud_layers)
    assert fixed.derived_levels[0].pressure_hpa == int(_SP_TERRAIN)
    assert all(lv.pressure_hpa <= _SP_TERRAIN for lv in fixed.derived_levels)


# --- unchanged cases --------------------------------------------------------


def _arrays(prof):
    return [None if a is None else a.magnitude for a in (
        prof.pressure, prof.temperature, prof.dewpoint, prof.wind_speed,
        prof.wind_direction, prof.height, prof.omega)]


def _assert_same(a, b):
    for x, y in zip(_arrays(a), _arrays(b)):
        if x is None or y is None:
            assert x is y
        else:
            np.testing.assert_array_equal(x, y)


@pytest.mark.parametrize("sp", [1013.0, 1003.0, 997.0])
def test_sea_level_profile_unchanged(sp):
    levels = _column(ground_hpa=None)
    with_sp = prepare_profile(levels, _hourly(levels, sp))
    without = prepare_profile(levels, None)
    _assert_same(with_sp, without)
    assert with_sp.clipped_levels == 0
    assert not with_sp.surface_level_inserted

    a = analyze_sounding(levels, _hourly(levels, sp))
    b = analyze_sounding(levels, _hourly(levels, None, t2=_hourly(levels, sp).temperature_2m_c))
    assert a.indices.cape_surface_jkg == b.indices.cape_surface_jkg
    assert a.indices.lcl_pressure_hpa == b.indices.lcl_pressure_hpa
    assert [c.base_ft for c in a.dd_cloud_layers] == [c.base_ft for c in b.dd_cloud_layers]


def test_missing_surface_pressure_keeps_the_full_column():
    levels = _column(ground_hpa=_SP_TERRAIN)
    prof = prepare_profile(levels, _hourly(levels, None, t2=20.0))
    assert len(prof.pressure) == len(_LEVELS)
    assert prof.clipped_levels == 0


@pytest.mark.parametrize("sp", [250.0, 1200.0])
def test_implausible_surface_pressure_is_ignored(sp):
    levels = _column(ground_hpa=None)
    prof = prepare_profile(levels, _hourly(levels, sp, t2=10.0))
    assert len(prof.pressure) == len(_LEVELS)


# --- tolerances and edges ---------------------------------------------------


def test_level_just_below_ground_is_kept_without_inserting_a_surface():
    """850 hPa sits 3 hPa (~25 m) inside an 847 hPa ground: kept, it is the
    near-surface parcel; no 2 m level squeezed in 3 hPa above it."""
    levels = _column(ground_hpa=847.0)
    prof = prepare_profile(levels, _hourly(levels, 847.0))
    p = prof.pressure.m_as("hPa")
    assert p[0] == 850
    assert prof.clipped_levels == 6
    assert not prof.surface_level_inserted


def test_first_level_close_above_ground_gets_no_surface_level():
    levels = _column(ground_hpa=855.0)
    prof = prepare_profile(levels, _hourly(levels, 855.0))
    assert prof.pressure.m_as("hPa")[0] == 850
    assert not prof.surface_level_inserted


def test_deep_low_at_sea_level_clips_1000_and_launches_from_surface():
    levels = _column(ground_hpa=990.0)
    prof = prepare_profile(levels, _hourly(levels, 990.0))
    p = prof.pressure.m_as("hPa")
    assert 1000 not in p
    assert p[0] == pytest.approx(990.0)
    assert p[1] == 975


def test_no_2m_temperature_clips_without_inserting():
    levels = _column(ground_hpa=_SP_TERRAIN)
    hourly = _hourly(levels, _SP_TERRAIN)
    hourly.temperature_2m_c = None
    prof = prepare_profile(levels, hourly)
    assert prof.pressure.m_as("hPa")[0] == 825
    assert not prof.surface_level_inserted
    assert len(prof.height) == len(prof.pressure)


def test_dewpoint_derived_from_2m_rh_when_missing():
    levels = _column(ground_hpa=_SP_TERRAIN)
    hourly = _hourly(levels, _SP_TERRAIN)
    hourly.dewpoint_2m_c = None
    hourly.relative_humidity_2m_pct = 50.0
    prof = prepare_profile(levels, hourly)
    assert prof.surface_level_inserted
    assert prof.dewpoint[0].m_as("degC") < prof.temperature[0].m_as("degC")


def test_missing_10m_wind_is_nan_not_dropped():
    levels = _column(ground_hpa=_SP_TERRAIN)
    hourly = _hourly(levels, _SP_TERRAIN, ws10=None)
    prof = prepare_profile(levels, hourly)
    assert prof.surface_level_inserted
    assert math.isnan(prof.wind_speed[0].m_as("knot"))
    assert not math.isnan(prof.wind_speed[1].m_as("knot"))


def test_column_mostly_inside_the_ground_is_rejected():
    """A column that keeps fewer than MIN_LEVELS after the clip is rejected
    like any other thin column, never analysed on its extrapolation."""
    levels = [lv for lv in _column(ground_hpa=None) if lv.pressure_hpa >= 850]
    hourly = _hourly(levels, 860.0)  # keeps 850 only (+ surface = 2)
    assert prepare_profile(levels, hourly) is None
    assert prepare_profile(levels, None) is not None


def test_columns_without_heights_still_get_a_surface_level():
    levels = _column(ground_hpa=_SP_TERRAIN)
    for lv in levels:
        lv.geopotential_height_m = None
    prof = prepare_profile(levels, _hourly(levels, _SP_TERRAIN))
    assert prof.height is None
    assert prof.surface_level_inserted
    assert prof.pressure.m_as("hPa")[0] == pytest.approx(_SP_TERRAIN)


def test_cloud_fraction_layers_ignore_below_ground_levels():
    """ICON-style CLC on sub-ground levels must not make an NWP deck (#807)."""
    from weatherbrief.analysis.sounding.prepare import levels_above_ground

    levels = _column(ground_hpa=_SP_TERRAIN)
    for lv in levels:
        lv.cloud_area_fraction_pct = 95.0 if lv.pressure_hpa > _SP_TERRAIN else 0.0
    hourly = _hourly(levels, _SP_TERRAIN)

    kept = levels_above_ground(levels, hourly)
    assert all(lv.pressure_hpa <= _SP_TERRAIN + 5 for lv in kept)
    assert levels_above_ground(levels, _hourly(levels, None, t2=None)) is levels

    fixed = analyze_sounding(levels, hourly)
    assert not fixed.nwp_cloud_layers
