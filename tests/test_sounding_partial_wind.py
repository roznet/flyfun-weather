"""Sounding robustness when a GRIB fetch delivers wind on only SOME levels (#478).

DWD publishes one file per (variable, level), so a handful of failed downloads
— or a failure at the top of the column — leaves levels carrying temperature
but no u/v. The decode completeness check (#478) skips an hour whose per-level
cache is short, but wind can still be missing for other reasons: an ICON-EU
whole-column blob that is itself partial, a variable absent from the feed, or a
model that simply doesn't publish wind at every level.

The invariant these pin: **missing wind on some levels must never silently
become "no wind anywhere"**, because that collapses Richardson → CAT layers →
the turbulence advisory to a false GREEN "smooth" (the #391/#393
absence-reads-as-clear failure) instead of grading the levels that do have wind.
"""

from __future__ import annotations

import numpy as np

from weatherbrief.analysis.sounding import analyze_sounding
from weatherbrief.analysis.sounding.prepare import prepare_profile
from weatherbrief.models import PressureLevelData


def _lv(p, gph_m, *, t=None, rh=None, ws=None, wd=None, w=None, caf=None):
    return PressureLevelData(
        pressure_hpa=p,
        geopotential_height_m=gph_m,
        temperature_c=t,
        relative_humidity_pct=rh,
        wind_speed_kt=ws,
        wind_direction_deg=wd,
        vertical_velocity_pa_s=w,
        cloud_area_fraction_pct=caf,
    )


def _partial_wind_column() -> list[PressureLevelData]:
    """A column whose top two levels lost their u/v files.

    Lower levels carry wind/omega/cloud; the 500 and 300 hPa levels kept
    temperature and humidity but no wind. A strong directional shear near
    700 hPa seeds a CAT layer low down, so the turbulence path has something to
    grade if — and only if — the wind that DID arrive is used.
    """
    return [
        _lv(1000, 110, t=15, rh=80, ws=10, wd=200, w=0.0, caf=0),
        _lv(900, 990, t=9, rh=75, ws=15, wd=210, w=0.1, caf=0),
        _lv(800, 1950, t=3, rh=70, ws=20, wd=225, w=0.1, caf=0),
        _lv(700, 3010, t=-3, rh=65, ws=55, wd=300, w=0.2, caf=0),  # shear kink
        _lv(600, 4200, t=-10, rh=60, ws=60, wd=305, w=0.1, caf=0),
        # --- u/v missing above here ---
        _lv(500, 5570, t=-20, rh=55),
        _lv(300, 9160, t=-45, rh=40),
    ]


# ---------------------------------------------------------------------------
# prepare.py — per-level wind gate (NaN fill, not all-or-nothing)
# ---------------------------------------------------------------------------


def test_prepare_keeps_wind_for_levels_that_have_it():
    profile = prepare_profile(_partial_wind_column())
    assert profile is not None
    assert profile.wind_speed is not None  # NOT dropped for the whole profile
    ws = profile.wind_speed.to("knot").magnitude
    # Surface-first order: the levels that arrived keep real wind, the two that
    # didn't are NaN rather than poisoning the rest.
    assert not np.isnan(ws[0])
    assert np.isnan(ws[-1]) and np.isnan(ws[-2])
    assert np.count_nonzero(~np.isnan(ws)) == 5


def test_prepare_all_windless_stays_none():
    """NaN-fill only engages when SOME level has wind.

    A genuinely wind-less profile still yields None — we are not manufacturing
    an all-NaN array that downstream code would have to special-case.
    """
    levels = [
        _lv(1000, 110, t=15, rh=80),
        _lv(900, 990, t=9, rh=75),
        _lv(800, 1950, t=3, rh=70),
    ]
    profile = prepare_profile(levels)
    assert profile is not None
    assert profile.wind_speed is None


# ---------------------------------------------------------------------------
# The headline failure: turbulence must still grade
# ---------------------------------------------------------------------------


def test_turbulence_axis_alive_when_upper_wind_missing():
    """#391/#393: an all-or-nothing gate leaves EVERY level with no Richardson
    number → ``cat_risk_layers == []`` → turbulence reports a false smooth
    GREEN. With the per-level gate the levels that have wind still grade."""
    result = analyze_sounding(_partial_wind_column())
    assert result is not None
    ri = [
        dl.richardson_number for dl in result.derived_levels
        if dl.wind_speed_kt is not None
    ]
    assert any(r is not None for r in ri)
    # And the levels that lost wind report it honestly rather than as calm.
    assert all(
        dl.wind_speed_kt is None
        for dl in result.derived_levels if dl.pressure_hpa < 550
    )


def test_bulk_shear_missing_endpoint_is_none_not_nan():
    """NaN must never reach the indices — it would serialise as invalid JSON
    and read downstream as a number."""
    result = analyze_sounding(_partial_wind_column())
    assert result is not None and result.indices is not None
    # 0-6 km top sits in the wind-less region → unavailable → None.
    assert result.indices.bulk_shear_0_6km_kt is None
    # 0-1 km is entirely inside the wind-carrying region → still computed.
    assert result.indices.bulk_shear_0_1km_kt is not None


def test_full_wind_column_unaffected():
    """The inverse: a complete column must behave exactly as before."""
    levels = _partial_wind_column()
    levels[-1] = _lv(300, 9160, t=-45, rh=40, ws=70, wd=310, w=0.1)
    levels[-2] = _lv(500, 5570, t=-20, rh=55, ws=65, wd=308, w=0.1)
    result = analyze_sounding(levels)
    assert result is not None and result.indices is not None
    assert result.indices.bulk_shear_0_6km_kt is not None
    profile = prepare_profile(levels)
    ws = profile.wind_speed.to("knot").magnitude
    assert not np.isnan(ws).any()
