"""Fast wet-bulb path must be numerically indistinguishable from MetPy.

The fast path (vectorized RK4 with MetPy's own integrand and LCL) replaces
``mpcalc.wet_bulb_temperature`` in compute_derived_levels_extended. Its only
consumer stores the value rounded to 0.1 degC (DerivedLevel.wet_bulb_c), so
the contract is: agree with MetPy well inside that rounding everywhere in
the aviation-relevant envelope.
"""

from __future__ import annotations

import metpy.calc as mpcalc
import numpy as np
import pytest
from metpy.units import units

from weatherbrief.analysis.sounding.wet_bulb import _wet_bulb_fast, wet_bulb_degc


def _grid():
    """p 150-1013 hPa x T -55..+40 degC x dewpoint depression 0-35 degC."""
    ps, ts, dds = np.meshgrid(
        np.array([1013.0, 1000, 950, 925, 850, 800, 700, 600, 500, 400, 300, 200, 150]),
        np.arange(-55, 41, 5, dtype=float),
        np.array([0.0, 0.5, 1, 2, 3, 5, 8, 12, 18, 25, 35]),
        indexing="ij",
    )
    return ps.ravel(), ts.ravel(), (ts - dds).ravel()


def test_matches_metpy_across_envelope():
    p, t, td = _grid()
    ref = mpcalc.wet_bulb_temperature(
        p * units.hPa, t * units.degC, td * units.degC,
    ).to("degC").magnitude
    fast = _wet_bulb_fast(p * units.hPa, t * units.degC, td * units.degC)

    valid = ~np.isnan(ref)
    assert valid.sum() > 2500  # sanity: the grid actually exercised the range
    diff = np.abs(fast[valid] - ref[valid])
    # Well inside the 0.1 degC storage rounding; observed max ~2.4e-5 degC.
    assert diff.max() < 0.001
    # And the stored (rounded) values are identical everywhere.
    np.testing.assert_array_equal(
        np.round(fast[valid], 1), np.round(ref[valid], 1),
    )


def test_saturated_levels_return_temperature():
    """At saturation (Td == T) the wet bulb is the dry bulb (LCL descent is a no-op)."""
    p = np.array([1000.0, 850.0, 700.0]) * units.hPa
    t = np.array([15.0, 2.0, -8.0]) * units.degC
    out = _wet_bulb_fast(p, t, t)
    np.testing.assert_allclose(out, [15.0, 2.0, -8.0], atol=1e-6)


def test_wet_bulb_between_dewpoint_and_temperature():
    p, t, td = _grid()
    fast = _wet_bulb_fast(p * units.hPa, t * units.degC, td * units.degC)
    valid = ~np.isnan(fast)
    # Physical bracket with a small numerical margin.
    assert np.all(fast[valid] <= t[valid] + 1e-6)
    assert np.all(fast[valid] >= td[valid] - 1e-6)


def test_public_entry_falls_back_to_metpy(monkeypatch):
    import weatherbrief.analysis.sounding.wet_bulb as wb_mod

    def boom(*args, **kwargs):
        raise RuntimeError("fast path unavailable")

    monkeypatch.setattr(wb_mod, "_wet_bulb_fast", boom)
    p = np.array([1000.0, 850.0]) * units.hPa
    t = np.array([10.0, 0.0]) * units.degC
    td = np.array([5.0, -5.0]) * units.degC
    out = wet_bulb_degc(p, t, td)
    ref = mpcalc.wet_bulb_temperature(p, t, td).to("degC").magnitude
    np.testing.assert_allclose(out, ref, atol=1e-9)


def test_missing_nounit_constants_fall_back(monkeypatch):
    """A future MetPy dropping the nounit namespace must degrade to the
    stock solver at call time, not break at import time."""
    import weatherbrief.analysis.sounding.wet_bulb as wb_mod

    monkeypatch.setattr(wb_mod, "nounit", None)
    p = np.array([1000.0, 850.0]) * units.hPa
    t = np.array([10.0, 0.0]) * units.degC
    td = np.array([5.0, -5.0]) * units.degC
    out = wet_bulb_degc(p, t, td)
    ref = mpcalc.wet_bulb_temperature(p, t, td).to("degC").magnitude
    np.testing.assert_allclose(out, ref, atol=1e-9)


def test_derived_levels_wet_bulb_unchanged(sample_pressure_levels):
    """End-to-end: DerivedLevel.wet_bulb_c equals the MetPy-derived value."""
    from weatherbrief.analysis.sounding.prepare import prepare_profile
    from weatherbrief.analysis.sounding.thermodynamics import compute_derived_levels

    profile = prepare_profile(sample_pressure_levels)
    assert profile is not None
    levels = compute_derived_levels(profile)

    ref = mpcalc.wet_bulb_temperature(
        profile.pressure, profile.temperature, profile.dewpoint,
    ).to("degC").magnitude
    for lv, ref_wb in zip(levels, ref):
        if np.isnan(ref_wb):
            assert lv.wet_bulb_c is None
        else:
            assert lv.wet_bulb_c == pytest.approx(round(float(ref_wb), 1), abs=1e-9)
