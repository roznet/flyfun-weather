"""Stability indices never carry NaN out of MetPy (#807 surface clip)."""

import math

from weatherbrief.analysis.sounding.thermodynamics import _finite_round


def test_nan_becomes_none():
    assert _finite_round(float("nan")) is None
    assert _finite_round(math.inf) is None


def test_finite_value_rounds():
    assert _finite_round(23.456) == 23.5


def test_clipped_terrain_profile_has_no_nan_index():
    from weatherbrief.analysis.sounding.prepare import prepare_profile
    from weatherbrief.analysis.sounding.thermodynamics import compute_indices_extended
    from weatherbrief.models import ThermodynamicIndices
    from tests.test_prepare_below_ground import _SP_TERRAIN, _column, _hourly

    levels = _column(ground_hpa=_SP_TERRAIN)
    prof = prepare_profile(levels, _hourly(levels, _SP_TERRAIN))
    idx = ThermodynamicIndices()
    compute_indices_extended(prof, idx)
    for v in (idx.k_index, idx.total_totals, idx.showalter_index,
              idx.precipitable_water_mm):
        assert v is None or math.isfinite(v)
