"""Tests for the per-waypoint altitude advisories (sounding/advisories.py).

Focused on the descend-below-icing escape logic: warm-air OR clear-air escape
(max of the two), terrain feasibility, and the freezing-rain guard.
"""

from __future__ import annotations

from weatherbrief.analysis.sounding.advisories import compute_altitude_advisories
from weatherbrief.models import (
    CloudCoverage,
    EnhancedCloudLayer,
    IcingRisk,
    IcingType,
    IcingZone,
    PrecipitationAssessment,
    SoundingAnalysis,
    ThermodynamicIndices,
)


def _sounding(
    freezing_level_ft: float | None = 9000,
    icing_base_ft: float = 4000,
    icing_top_ft: float = 10000,
    cloud_base_ft: float | None = 3000,
    cloud_top_ft: float = 12000,
    freezing_rain: bool = False,
) -> SoundingAnalysis:
    clouds = []
    if cloud_base_ft is not None:
        clouds = [EnhancedCloudLayer(
            base_ft=cloud_base_ft, top_ft=cloud_top_ft, coverage=CloudCoverage.OVC,
        )]
    return SoundingAnalysis(
        indices=ThermodynamicIndices(freezing_level_ft=freezing_level_ft),
        icing_zones=[IcingZone(
            base_ft=icing_base_ft, top_ft=icing_top_ft,
            risk=IcingRisk.MODERATE, icing_type=IcingType.MIXED,
        )],
        cloud_layers=clouds,
        precipitation=PrecipitationAssessment(freezing_rain_risk=freezing_rain),
    )


def _descend(advisories):
    return next(
        (a for a in advisories.advisories if a.advisory_type == "descend_below_icing"),
        None,
    )


def test_descend_escape_uses_higher_of_warm_or_clear_air():
    """Warm air (below FZL) OR clear air (below cloud base) each exit icing —
    the escape is the HIGHER of the two minus margin, not the lower."""
    # FZL 9000, icing-bearing cloud base 3000 → escape = 9000 − 500 = 8500
    # (warm-air escape; the old min() logic would have demanded 2500).
    adv = compute_altitude_advisories({"gfs": _sounding()}, 8000, 18000)
    descend = _descend(adv)
    assert descend is not None
    assert descend.altitude_ft == 8500
    assert descend.feasible is True


def test_descend_aggregates_min_across_models():
    """Aggregate stays worst-case (lowest) across models."""
    adv = compute_altitude_advisories({
        "gfs": _sounding(freezing_level_ft=9000),
        "ecmwf": _sounding(freezing_level_ft=7000),
    }, 8000, 18000)
    descend = _descend(adv)
    assert descend.altitude_ft == 6500
    assert descend.per_model_ft == {"gfs": 8500, "ecmwf": 6500}


def test_descend_below_terrain_marked_infeasible():
    """Escape below terrain clearance keeps the meteorological altitude but is
    flagged infeasible with the terrain in the reason."""
    adv = compute_altitude_advisories(
        {"gfs": _sounding(freezing_level_ft=9000)}, 8000, 18000,
        terrain_elevation_ft=8200,
    )
    descend = _descend(adv)
    assert descend.altitude_ft == 8500
    assert descend.feasible is False
    assert "terrain" in descend.reason


def test_descend_above_terrain_clearance_feasible():
    adv = compute_altitude_advisories(
        {"gfs": _sounding(freezing_level_ft=9000)}, 8000, 18000,
        terrain_elevation_ft=7000,
    )
    descend = _descend(adv)
    assert descend.feasible is True


def test_descend_freezing_rain_has_no_escape():
    """A freezing-rain profile (warm nose over sub-zero surface) offers NO
    descent escape — below-cloud air carries supercooled precipitation."""
    adv = compute_altitude_advisories(
        {"gfs": _sounding(freezing_rain=True)}, 8000, 18000,
    )
    descend = _descend(adv)
    assert descend is not None
    assert descend.altitude_ft is None
    assert descend.feasible is False
    assert "reezing precipitation" in descend.reason
    assert descend.per_model_ft == {"gfs": None}


def test_descend_freezing_rain_one_model_blocks_cross_model_escape():
    adv = compute_altitude_advisories({
        "gfs": _sounding(freezing_rain=True),
        "ecmwf": _sounding(freezing_level_ft=7000),
    }, 8000, 18000)
    descend = _descend(adv)
    assert descend.altitude_ft is None
    assert descend.feasible is False
    assert descend.per_model_ft == {"gfs": None, "ecmwf": 6500}
    assert "no descent escape" in descend.reason
    assert "gfs" in descend.reason
    assert "Descend below" not in descend.reason


def test_descend_model_without_icing_does_not_block_finite_escape():
    adv = compute_altitude_advisories({
        "gfs": _sounding(freezing_level_ft=7000),
        "ecmwf": SoundingAnalysis(),
    }, 8000, 18000)
    descend = _descend(adv)
    assert descend.altitude_ft == 6500
    assert descend.feasible is True
    assert descend.per_model_ft == {"gfs": 6500, "ecmwf": None}


def test_descend_fallback_to_icing_zone_base():
    """No freezing level and no icing-overlapping cloud → clear-air exit below
    the icing zone itself."""
    adv = compute_altitude_advisories(
        {"gfs": _sounding(freezing_level_ft=None, cloud_base_ft=None)},
        8000, 18000,
    )
    descend = _descend(adv)
    assert descend.altitude_ft == 3500  # icing base 4000 − 500
