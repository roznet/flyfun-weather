"""Sounding analysis subpackage — MetPy-based atmospheric analysis.

Public API: analyze_sounding() takes pressure level data and returns a
SoundingAnalysis with thermodynamic indices, enhanced cloud layers,
icing zones, and convective assessment.
"""

from __future__ import annotations

import logging

from weatherbrief.models import HourlyForecast, PressureLevelData, SoundingAnalysis

logger = logging.getLogger(__name__)


def analyze_sounding(
    levels: list[PressureLevelData],
    hourly: HourlyForecast | None = None,
) -> SoundingAnalysis | None:
    """Run full sounding analysis on pressure level data.

    Pipeline: prepare → thermodynamics → clouds → icing → convective.
    Returns None if profile preparation fails (insufficient data).
    """
    from weatherbrief.analysis.sounding.clouds import detect_cloud_layers
    from weatherbrief.analysis.sounding.convective import assess_convective
    from weatherbrief.analysis.sounding.icing import assess_icing_zones
    from weatherbrief.analysis.sounding.prepare import prepare_profile
    from weatherbrief.analysis.sounding.thermodynamics import (
        compute_derived_levels,
        compute_indices,
    )

    profile = prepare_profile(levels, hourly)
    if profile is None:
        return None

    # Thermodynamic indices and per-level derived values
    indices = compute_indices(profile)
    derived_levels = compute_derived_levels(profile)

    # Enhanced cloud detection
    cloud_layers = detect_cloud_layers(
        derived_levels,
        lcl_altitude_ft=indices.lcl_altitude_ft,
    )

    # Enhanced icing assessment
    icing_zones = assess_icing_zones(
        derived_levels,
        cloud_layers,
        precipitable_water_mm=indices.precipitable_water_mm,
    )

    # Convective assessment
    convective = assess_convective(indices)

    return SoundingAnalysis(
        indices=indices,
        derived_levels=derived_levels,
        cloud_layers=cloud_layers,
        icing_zones=icing_zones,
        convective=convective,
    )
