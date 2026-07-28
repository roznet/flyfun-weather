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


def test_descend_freezing_rain_one_model_makes_escape_infeasible():
    """When ANY model shows FZRA, descent is not a safe universal escape.

    Regression for #391 (safety-relevant): the FZRA model contributes
    per_model_ft=None (descending stays in freezing rain), which was filtered
    out so min() of the remaining models still offered a descent — recommending
    "descend to 6500ft" while gfs says descending keeps you in FZRA. The
    meteorological altitude for the non-FZRA model is still reported, but the
    advisory must be marked infeasible and call out the FZRA model.
    """
    adv = compute_altitude_advisories({
        "gfs": _sounding(freezing_rain=True),
        "ecmwf": _sounding(freezing_level_ft=7000),
    }, 8000, 18000)
    descend = _descend(adv)
    assert descend.altitude_ft == 6500
    assert descend.per_model_ft["gfs"] is None
    assert descend.feasible is False
    assert "gfs" in descend.reason


def test_descend_fallback_to_icing_zone_base():
    """No freezing level and no icing-overlapping cloud → clear-air exit below
    the icing zone itself."""
    adv = compute_altitude_advisories(
        {"gfs": _sounding(freezing_level_ft=None, cloud_base_ft=None)},
        8000, 18000,
    )
    descend = _descend(adv)
    assert descend.altitude_ft == 3500  # icing base 4000 − 500


# ── risk=NONE zones are not icing ────────────────────────────────────


def _climb(advisories):
    return next(
        (a for a in advisories.advisories if a.advisory_type == "climb_above_icing"),
        None,
    )


def _none_risk_sounding() -> SoundingAnalysis:
    """A sounding whose only zone is Ogimet's "assessed here, no icing" marker.

    Ogimet emits a zone wherever cloud sits in the icing temperature band and
    stamps it with the computed index, so risk=NONE is the method reporting no
    icing — not a hazard. Geometry is deliberately identical to `_sounding()`'s
    so only the risk differs.
    """
    return SoundingAnalysis(
        indices=ThermodynamicIndices(freezing_level_ft=9000),
        icing_zones=[IcingZone(
            base_ft=4000, top_ft=10000,
            risk=IcingRisk.NONE, icing_type=IcingType.CLEAR,
            mean_icing_index=0.8,
        )],
        cloud_layers=[EnhancedCloudLayer(
            base_ft=3000, top_ft=12000, coverage=CloudCoverage.OVC,
        )],
        precipitation=PrecipitationAssessment(),
    )


def test_no_escape_advisories_for_none_risk_zones():
    """Zones that exist but carry risk=NONE must not produce escape advice.

    On the briefing that surfaced this, 10 of 30 route points carried zones that
    were entirely risk=NONE and each emitted a descend/climb escape for icing the
    method itself said was absent.
    """
    adv = compute_altitude_advisories({"gfs": _none_risk_sounding()}, 8000, 18000)
    assert _descend(adv) is None
    assert _climb(adv) is None


def test_none_risk_model_does_not_drag_the_aggregate():
    """A NONE-risk model must be ignored, not treated as a no-escape model.

    Aggregation is min() across models for the descent, and a model reported as
    "has icing but no escape" contributes None — which would wrongly mark the
    advisory infeasible. It must simply not participate.
    """
    adv = compute_altitude_advisories({
        "gfs": _sounding(),                # real MODERATE icing, escape 8500
        "ecmwf": _none_risk_sounding(),    # assessed, clean
    }, 8000, 18000)
    descend = _descend(adv)
    assert descend is not None
    assert descend.altitude_ft == 8500
    assert descend.per_model_ft["ecmwf"] is None
    assert descend.feasible is True


def test_real_icing_still_produces_escape_advisories():
    """No over-correction — the same geometry at MODERATE still advises."""
    adv = compute_altitude_advisories({"gfs": _sounding()}, 8000, 18000)
    assert _descend(adv) is not None
