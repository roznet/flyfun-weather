"""Altitude band grouping for cross-model sounding comparison.

Groups sounding analysis results into standard altitude bands and compares
worst-case icing/cloud conditions across models within each band.
"""

from __future__ import annotations

from weatherbrief.models import (
    AltitudeBand,
    AltitudeBandComparison,
    BandModelSummary,
    CloudCoverage,
    IcingRisk,
    IcingType,
    SoundingAnalysis,
)

DEFAULT_BANDS = [
    AltitudeBand(name="SFC-6000ft", floor_ft=0, ceiling_ft=6000),
    AltitudeBand(name="6000-12000ft", floor_ft=6000, ceiling_ft=12000),
    AltitudeBand(name="12000-18000ft", floor_ft=12000, ceiling_ft=18000),
    AltitudeBand(name="18000-25000ft", floor_ft=18000, ceiling_ft=25000),
    AltitudeBand(name="25000ft+", floor_ft=25000, ceiling_ft=60000),
]

_ICING_ORDER = [IcingRisk.NONE, IcingRisk.LIGHT, IcingRisk.MODERATE, IcingRisk.SEVERE]
_COVERAGE_ORDER = [CloudCoverage.SCT, CloudCoverage.BKN, CloudCoverage.OVC]


def _overlaps(zone_base: float, zone_top: float, band: AltitudeBand) -> bool:
    """Check if a zone [base, top] overlaps an altitude band."""
    return zone_base < band.ceiling_ft and zone_top > band.floor_ft


def _summarize_model(analysis: SoundingAnalysis, band: AltitudeBand) -> BandModelSummary:
    """Summarize a single model's sounding within an altitude band."""
    worst_icing = IcingRisk.NONE
    worst_icing_type = IcingType.NONE
    sld_risk = False
    cloud_coverage: CloudCoverage | None = None
    temps: list[float] = []

    # Icing zones overlapping this band
    for zone in analysis.icing_zones:
        if _overlaps(zone.base_ft, zone.top_ft, band):
            if _ICING_ORDER.index(zone.risk) > _ICING_ORDER.index(worst_icing):
                worst_icing = zone.risk
                worst_icing_type = zone.icing_type
            if zone.sld_risk:
                sld_risk = True
            if zone.mean_temperature_c is not None:
                temps.append(zone.mean_temperature_c)

    # Cloud layers overlapping this band
    for cl in analysis.cloud_layers:
        if _overlaps(cl.base_ft, cl.top_ft, band):
            if cloud_coverage is None or _COVERAGE_ORDER.index(cl.coverage) > _COVERAGE_ORDER.index(cloud_coverage):
                cloud_coverage = cl.coverage
            if cl.mean_temperature_c is not None:
                temps.append(cl.mean_temperature_c)

    # Temperature range from derived levels in band
    for lv in analysis.derived_levels:
        if lv.altitude_ft is not None and band.floor_ft <= lv.altitude_ft < band.ceiling_ft:
            if lv.temperature_c is not None:
                temps.append(lv.temperature_c)

    return BandModelSummary(
        worst_icing_risk=worst_icing,
        worst_icing_type=worst_icing_type,
        sld_risk=sld_risk,
        cloud_coverage=cloud_coverage,
        temperature_min_c=round(min(temps), 1) if temps else None,
        temperature_max_c=round(max(temps), 1) if temps else None,
    )


def summarize_by_bands(
    soundings: dict[str, SoundingAnalysis],
    bands: list[AltitudeBand] | None = None,
) -> list[AltitudeBandComparison]:
    """Compare sounding analyses across models within altitude bands.

    Args:
        soundings: model_key -> SoundingAnalysis mapping.
        bands: Altitude bands to group by. Defaults to standard aviation bands.

    Returns:
        List of AltitudeBandComparison, one per band.
    """
    if bands is None:
        bands = DEFAULT_BANDS

    comparisons: list[AltitudeBandComparison] = []
    for band in bands:
        model_summaries: dict[str, BandModelSummary] = {}
        for model_key, analysis in soundings.items():
            model_summaries[model_key] = _summarize_model(analysis, band)

        # Agreement: icing spread <= 1 category
        icing_risks = [s.worst_icing_risk for s in model_summaries.values()]
        icing_agreement = True
        if len(icing_risks) >= 2:
            indices = [_ICING_ORDER.index(r) for r in icing_risks]
            icing_agreement = (max(indices) - min(indices)) <= 1

        # Agreement: cloud coverage spread <= 1 category
        cloud_covs = [s.cloud_coverage for s in model_summaries.values() if s.cloud_coverage is not None]
        cloud_agreement = True
        if len(cloud_covs) >= 2:
            cov_indices = [_COVERAGE_ORDER.index(c) for c in cloud_covs]
            cloud_agreement = (max(cov_indices) - min(cov_indices)) <= 1

        comparisons.append(AltitudeBandComparison(
            band=band,
            models=model_summaries,
            icing_agreement=icing_agreement,
            cloud_agreement=cloud_agreement,
        ))

    return comparisons
