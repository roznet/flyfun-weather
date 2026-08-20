"""Value-tests for the hand-rolled thermodynamics helpers.

`_find_temperature_crossing` (freezing / −10 / −20 °C levels) and
`_compute_bulk_shear` are the two pieces of `thermodynamics.py` that are NOT
delegated to MetPy, so their arithmetic needs an independent oracle. Every
expected value here is computed by hand from the inputs.
"""

from __future__ import annotations

import numpy as np
import pytest
from metpy.units import units

from weatherbrief.analysis.sounding.prepare import PreparedProfile
from weatherbrief.analysis.sounding.thermodynamics import (
    _compute_bulk_shear,
    _find_temperature_crossing,
)


def _profile(temps_c: list[float], heights_ft: list[float]) -> PreparedProfile:
    """Build a minimal profile with explicit geopotential height (in meters).

    Only temperature + height are read by _find_temperature_crossing; the rest
    are filler. Heights are supplied in feet here and converted to the meters
    the profile stores (1 ft = 0.3048 m exactly).
    """
    n = len(temps_c)
    heights_m = np.array(heights_ft) * 0.3048
    return PreparedProfile(
        pressure=np.linspace(1000, 1000 - 25 * (n - 1), n) * units.hPa,
        temperature=np.array(temps_c) * units.degC,
        dewpoint=np.array(temps_c) * units.degC,
        wind_speed=None,
        wind_direction=None,
        height=heights_m * units.meter,
        omega=None,
        surface_pressure=None,
        surface_temperature=None,
        surface_dewpoint=None,
    )


# --- _find_temperature_crossing: linear interpolation oracle ---


def test_crossing_linear_interpolation_zero():
    """T +5 @0ft → −5 @2000ft. 0 °C crossing at frac 0.5 → 1000 ft."""
    p = _profile([5.0, -5.0, -15.0], [0.0, 2000.0, 4000.0])
    assert _find_temperature_crossing(p, 0.0) == pytest.approx(1000.0)


def test_crossing_in_upper_segment():
    """−10 °C lies between −5 @2000ft and −15 @4000ft → frac 0.5 → 3000 ft."""
    p = _profile([5.0, -5.0, -15.0], [0.0, 2000.0, 4000.0])
    assert _find_temperature_crossing(p, -10.0) == pytest.approx(3000.0)


def test_crossing_absent_returns_none():
    """Profile never reaches −20 °C (min −15) → None."""
    p = _profile([5.0, -5.0, -15.0], [0.0, 2000.0, 4000.0])
    assert _find_temperature_crossing(p, -20.0) is None


def test_crossing_isothermal_layer_at_target_is_skipped():
    """A layer sitting exactly at the target (t0 == t1) must not yield a bogus
    crossing — the `t0 != t1` guard skips it, so an all-+5 °C profile has no
    0... wait, target is +5 here → returns None (no genuine crossing)."""
    p = _profile([5.0, 5.0, 5.0], [0.0, 2000.0, 4000.0])
    assert _find_temperature_crossing(p, 5.0) is None


def test_crossing_returns_first_of_multiple():
    """+5 → −5 → +5 crosses 0 °C twice; surface-upward walk returns the first
    (1000 ft), not the second."""
    p = _profile([5.0, -5.0, 5.0], [0.0, 2000.0, 4000.0])
    assert _find_temperature_crossing(p, 0.0) == pytest.approx(1000.0)


# --- _compute_bulk_shear: vector magnitude + nearest-level behavior ---


def test_bulk_shear_exact_magnitude_kt():
    """Δu 30, Δv 40 m/s between picked levels → 50 m/s → 97.2 kt (50·3600/1852)."""
    u = np.array([0.0, 0.0, 30.0]) * units("m/s")
    v = np.array([0.0, 0.0, 40.0]) * units("m/s")
    heights_m = np.array([0.0, 1000.0, 6000.0])
    assert _compute_bulk_shear(u, v, heights_m, 0.0, 6000.0) == pytest.approx(97.2)


def test_bulk_shear_uses_nearest_level_not_interpolation():
    """Requesting top=3000 m picks the NEAREST level (1000 m), not an
    interpolated 3000 m. Documents the known approximation: here that level
    still has zero wind, so the 0–3000 m "shear" reads 0.0 kt.

    TODO (accuracy): consider interpolating u/v to the exact target heights
    instead of argmin level-snapping — tracked in testing-accuracy-review.md.
    """
    u = np.array([0.0, 0.0, 30.0]) * units("m/s")
    v = np.array([0.0, 0.0, 40.0]) * units("m/s")
    heights_m = np.array([0.0, 1000.0, 6000.0])
    # top=3000 → |3000-1000|=2000 < |3000-6000|=3000 → snaps to the 1000 m level.
    assert _compute_bulk_shear(u, v, heights_m, 0.0, 3000.0) == pytest.approx(0.0)


def test_bulk_shear_same_level_returns_none():
    """When bottom and top snap to the same level, shear is undefined → None."""
    u = np.array([0.0, 30.0]) * units("m/s")
    v = np.array([0.0, 40.0]) * units("m/s")
    heights_m = np.array([0.0, 5000.0])
    assert _compute_bulk_shear(u, v, heights_m, 100.0, 200.0) is None


class TestMagUnwrapsSizeOneArrays:
    """`_mag` must survive MetPy returning a one-element array (#565).

    MetPy returns several indices as a size-1 array rather than a scalar, and
    NumPy 2 refuses `float()` on any array with ndim > 0. The old
    `float(quantity.magnitude)` therefore returned None, the caller's
    `round(None, 1)` raised TypeError, and the surrounding `except Exception`
    swallowed it — leaving `sounding_lifted_index` NULL on every profile ever
    written, silently, for years.
    """

    def test_scalar_quantity(self):
        from weatherbrief.analysis.sounding.thermodynamics import _mag
        assert _mag(3.5 * units.degC) == pytest.approx(3.5)

    def test_size_one_array_is_unwrapped(self):
        from weatherbrief.analysis.sounding.thermodynamics import _mag
        assert _mag(np.array([-2.5]) * units.delta_degC) == pytest.approx(-2.5)

    def test_multi_element_array_returns_none(self):
        """A real profile must never be silently collapsed to its first value."""
        from weatherbrief.analysis.sounding.thermodynamics import _mag
        assert _mag(np.array([1.0, 2.0, 3.0]) * units.degC) is None

    def test_none_and_non_quantity(self):
        from weatherbrief.analysis.sounding.thermodynamics import _mag
        assert _mag(None) is None
        assert _mag("not a quantity") is None


class TestLiftedIndexIsActuallyComputed:
    """Regression for the bug above, at the level that matters.

    `compute_indices_core` claims lifted index among the indices it computes,
    and `snapshot_fields` persists it to `sounding_lifted_index`. It was None
    on 110,801 of 110,801 rows on a sampled archive day, across all three
    models.
    """

    def _indices(self):
        from weatherbrief.analysis.sounding.prepare import prepare_profile
        from weatherbrief.analysis.sounding.thermodynamics import (
            compute_indices_core,
        )
        from weatherbrief.models.analysis import PressureLevelData

        levels = [
            PressureLevelData(
                pressure_hpa=hpa, temperature_c=t, relative_humidity_pct=rh,
                wind_speed_kt=20, wind_direction_deg=270,
            )
            for hpa, t, rh in [
                (1000, 20, 80), (925, 15, 75), (850, 11, 70), (700, 3, 60),
                (600, -4, 55), (500, -13, 50), (400, -25, 40), (300, -40, 30),
                (250, -50, 25), (200, -56, 20), (150, -58, 15), (100, -60, 10),
            ]
        ]
        profile = prepare_profile(levels, None)
        assert profile is not None
        return compute_indices_core(profile).indices

    def test_lifted_index_is_not_none(self):
        li = self._indices().lifted_index
        assert li is not None, (
            "lifted index is None again — _mag has probably stopped unwrapping "
            "MetPy's size-1 array, and the caller's except swallows the error"
        )
        assert -20.0 < li < 20.0

    def test_lfc_and_el_are_available_in_the_core_path(self):
        """Both are persisted by #565, so they must come from *core*, not extended."""
        idx = self._indices()
        assert idx.lfc_altitude_ft is not None
        assert idx.el_altitude_ft is not None

    def test_bulk_shear_is_not_in_the_core_path(self):
        """Pins why no bulk-shear snapshot column exists (#565).

        It is computed by `compute_indices_extended`, which
        `analyze_sounding_lite` skips — a column for it would be NULL on every
        standalone row. If this ever starts passing a non-None value, the
        column becomes worth adding.
        """
        assert self._indices().bulk_shear_0_6km_kt is None
