"""Tests for the EDR (Sharman & Pearson 2017) remap + calibration (issue #221)."""

from __future__ import annotations

import math

import pytest

from weatherbrief.analysis.sounding.edr import (
    ALL_BAND,
    C1_C2_BY_BAND,
    DIAGNOSTIC_RICHARDSON,
    RI_FLOOR,
    EdrAccumulator,
    band_for_altitude_ft,
    coefficients_from_accumulator,
    diagnostic_to_edr,
    richardson_to_d,
)


class _Level:
    """Minimal stand-in for DerivedLevel (only the fields edr.py reads)."""

    def __init__(self, richardson_number=None, altitude_ft=None):
        self.richardson_number = richardson_number
        self.altitude_ft = altitude_ft


# --------------------------------------------------------------------------
# band_for_altitude_ft
# --------------------------------------------------------------------------

@pytest.mark.parametrize("alt,expected", [
    (0, "0_10kft"),
    (9_999, "0_10kft"),
    (10_000, "10_20kft"),
    (19_999, "10_20kft"),
    (20_000, "20_45kft"),
    (45_000, "20_45kft"),
    (60_000, "20_45kft"),  # >45 kft clipped into the top band (v0 scope)
])
def test_band_for_altitude_ft_boundaries(alt, expected):
    assert band_for_altitude_ft(alt) == expected


def test_band_for_altitude_ft_unknown():
    assert band_for_altitude_ft(None) is None
    assert band_for_altitude_ft(float("nan")) is None


# --------------------------------------------------------------------------
# richardson_to_d
# --------------------------------------------------------------------------

def test_richardson_to_d_inverse():
    assert richardson_to_d(1.0) == pytest.approx(1.0)
    assert richardson_to_d(2.0) == pytest.approx(0.5)


def test_richardson_to_d_floor_caps_small_ri():
    # Ri below the floor is clamped so D doesn't blow up.
    assert richardson_to_d(0.0) == pytest.approx(1.0 / RI_FLOOR)
    assert richardson_to_d(1e-9) == pytest.approx(1.0 / RI_FLOOR)


def test_richardson_to_d_rejects_missing_or_nonfinite():
    assert richardson_to_d(None) is None
    assert richardson_to_d(float("nan")) is None
    assert richardson_to_d(float("inf")) is None


# --------------------------------------------------------------------------
# coefficients_from_accumulator + diagnostic_to_edr
# --------------------------------------------------------------------------

def test_coefficients_need_two_samples():
    assert coefficients_from_accumulator(0, 0.0, 0.0, -2.5, 0.5) is None
    assert coefficients_from_accumulator(1, 1.0, 1.0, -2.5, 0.5) is None


def test_coefficients_match_moment_formula():
    # Construct moments for a known ln(D): mean=1.0, var=4.0 (sd=2.0).
    n, mean, sd = 100, 1.0, 2.0
    sum_ln = n * mean
    sum_ln2 = n * (sd * sd + mean * mean)
    c1, c2 = -2.5, 0.6
    a, b = coefficients_from_accumulator(n, sum_ln, sum_ln2, c1, c2)
    assert b == pytest.approx(c2 / sd)
    assert a == pytest.approx(c1 - b * mean)


def test_remap_maps_geometric_mean_to_exp_c1():
    # At D = exp(<lnD>), the remap gives exactly exp(C1) (the climatological
    # median EDR) regardless of spread — the core invariant of the method.
    n, mean, sd = 1000, 0.7, 1.3
    sum_ln = n * mean
    sum_ln2 = n * (sd * sd + mean * mean)
    c1, c2 = -2.578, 0.557
    a, b = coefficients_from_accumulator(n, sum_ln, sum_ln2, c1, c2)
    edr_at_median = diagnostic_to_edr(math.exp(mean), a, b)
    assert edr_at_median == pytest.approx(math.exp(c1), rel=1e-9)


def test_diagnostic_to_edr_clips_and_filters():
    a, b = -2.5, 0.5
    # Huge D would exceed 1.0 → clipped.
    assert diagnostic_to_edr(1e9, a, b) == 1.0
    # Non-positive / non-finite D rejected.
    assert diagnostic_to_edr(0.0, a, b) is None
    assert diagnostic_to_edr(-1.0, a, b) is None
    assert diagnostic_to_edr(float("inf"), a, b) is None


def test_remap_monotonic_increasing_in_d():
    a, b = coefficients_from_accumulator(
        500, 500 * 0.5, 500 * (1.0 + 0.25), *C1_C2_BY_BAND["all"]
    )
    vals = [diagnostic_to_edr(d, a, b) for d in (0.5, 1.0, 5.0, 50.0)]
    assert all(x < y for x, y in zip(vals, vals[1:]))


# --------------------------------------------------------------------------
# EdrAccumulator
# --------------------------------------------------------------------------

def test_accumulator_counts_band_and_all():
    acc = EdrAccumulator()
    # Two valid levels in the 10-20 kft band, plus one invalid (None Ri).
    acc.observe_richardson_levels("gfs", [
        _Level(richardson_number=1.0, altitude_ft=12_000),
        _Level(richardson_number=4.0, altitude_ft=15_000),
        _Level(richardson_number=None, altitude_ft=14_000),
    ])
    rows = {(m, diag, band): (n, s1, s2) for m, diag, band, n, s1, s2 in acc.rows()}

    band_key = ("gfs", DIAGNOSTIC_RICHARDSON, "10_20kft")
    all_key = ("gfs", DIAGNOSTIC_RICHARDSON, ALL_BAND)
    assert band_key in rows and all_key in rows
    # Each valid level counted once in its band and once in "all".
    assert rows[band_key][0] == 2
    assert rows[all_key][0] == 2

    # ln(D): D=1/1=1 → ln 0; D=1/4=0.25 → ln(0.25).
    expected_sum = math.log(1.0) + math.log(0.25)
    assert rows[band_key][1] == pytest.approx(expected_sum)
    assert rows[band_key][2] == pytest.approx(math.log(0.25) ** 2)


def test_accumulator_separates_models():
    acc = EdrAccumulator()
    acc.observe_richardson_levels("gfs", [_Level(2.0, 12_000)])
    acc.observe_richardson_levels("icon", [_Level(2.0, 12_000)])
    models = {m for m, _, _, _, _, _ in acc.rows()}
    assert models == {"gfs", "icon"}


def test_accumulator_level_without_altitude_still_counts_all():
    acc = EdrAccumulator()
    acc.observe_richardson_levels("gfs", [_Level(richardson_number=1.0, altitude_ft=None)])
    rows = {(m, diag, band): n for m, diag, band, n, *_ in acc.rows()}
    # No altitude band, but the "all" cross-check band still gets the sample.
    assert rows.get(("gfs", DIAGNOSTIC_RICHARDSON, ALL_BAND)) == 1
    assert all(band == ALL_BAND for _, _, band in rows)


def test_accumulator_additive_across_calls():
    acc = EdrAccumulator()
    acc.observe_richardson_levels("gfs", [_Level(1.0, 5_000)])
    acc.observe_richardson_levels("gfs", [_Level(1.0, 5_000)])
    rows = {(m, diag, band): n for m, diag, band, n, *_ in acc.rows()}
    assert rows[("gfs", DIAGNOSTIC_RICHARDSON, "0_10kft")] == 2
