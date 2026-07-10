import math

import pytest

from weatherbrief.analysis.advisories.dd_nwp_agreement import _cloud_overlap_fraction
from weatherbrief.models import EnhancedCloudLayer


def _layer(base: float, top: float) -> EnhancedCloudLayer:
    return EnhancedCloudLayer(base_ft=base, top_ft=top)


def test_cloud_overlap_merges_internal_overlaps_before_jaccard():
    dd = [_layer(0, 10_000), _layer(5_000, 15_000)]
    nwp = [_layer(0, 15_000)]
    overlap = _cloud_overlap_fraction(dd, nwp)
    assert overlap == 1.0
    assert 0.0 <= overlap <= 1.0


def test_cloud_overlap_handles_disjoint_unions():
    dd = [_layer(0, 5_000), _layer(10_000, 15_000)]
    nwp = [_layer(2_500, 12_500)]
    assert _cloud_overlap_fraction(dd, nwp) == pytest.approx(1 / 3)


def test_cloud_overlap_merges_touching_spans():
    dd = [_layer(0, 5_000), _layer(5_000, 10_000)]
    nwp = [_layer(0, 10_000)]
    assert _cloud_overlap_fraction(dd, nwp) == 1.0


@pytest.mark.parametrize(
    ("base_ft", "top_ft"),
    [(10_000, 10_000), (10_000, 5_000)],
)
def test_cloud_overlap_canonicalizes_nonpositive_spans_before_empty_contract(
    base_ft: float,
    top_ft: float,
):
    assert _cloud_overlap_fraction([], [_layer(base_ft, top_ft)]) == 1.0


def test_cloud_overlap_empty_contract():
    assert _cloud_overlap_fraction([], []) == 1.0
    assert _cloud_overlap_fraction([], [_layer(0, 10_000)]) == 0.0


@pytest.mark.parametrize(
    ("base_ft", "top_ft"),
    [
        (math.nan, 10_000),
        (0, math.nan),
        (-math.inf, 10_000),
        (0, math.inf),
    ],
)
def test_cloud_overlap_ignores_nonfinite_spans(base_ft: float, top_ft: float):
    invalid = _layer(base_ft, top_ft)
    valid = _layer(0, 10_000)

    assert _cloud_overlap_fraction([], [invalid]) == 1.0
    assert _cloud_overlap_fraction([valid], [valid, invalid]) == 1.0
