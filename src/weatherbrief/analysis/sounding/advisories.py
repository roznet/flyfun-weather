"""Dynamic altitude advisories derived from sounding analysis.

Replaces the static altitude band system with:
1. Vertical regimes — dynamic slices per model from actual weather boundaries
2. Altitude advisories — actionable highlights aggregated across models
"""

from __future__ import annotations

from weatherbrief.models import (
    AltitudeAdvisories,
    AltitudeAdvisory,
    IcingRisk,
    IcingType,
    SoundingAnalysis,
    VerticalRegime,
)

_ICING_ORDER = [IcingRisk.NONE, IcingRisk.LIGHT, IcingRisk.MODERATE, IcingRisk.SEVERE]

_ICING_MARGIN_FT = 500


def compute_altitude_advisories(
    soundings: dict[str, SoundingAnalysis],
    cruise_altitude_ft: int,
    flight_ceiling_ft: int,
) -> AltitudeAdvisories:
    """Build vertical regimes and altitude advisories from sounding analyses.

    Args:
        soundings: model_key → SoundingAnalysis mapping.
        cruise_altitude_ft: Planned cruise altitude in feet.
        flight_ceiling_ft: Maximum altitude the aircraft can reach.

    Returns:
        AltitudeAdvisories with per-model regimes and cross-model advisories.
    """
    regimes: dict[str, list[VerticalRegime]] = {}
    for model_key, analysis in soundings.items():
        regimes[model_key] = _compute_regimes(analysis, flight_ceiling_ft)

    cruise_in_icing, cruise_icing_risk = _cruise_icing_status(
        soundings, cruise_altitude_ft
    )

    advisories: list[AltitudeAdvisory] = []
    descend = _descend_below_icing(soundings)
    if descend is not None:
        advisories.append(descend)
    climb = _climb_above_icing(soundings, flight_ceiling_ft)
    if climb is not None:
        advisories.append(climb)

    return AltitudeAdvisories(
        regimes=regimes,
        advisories=advisories,
        cruise_in_icing=cruise_in_icing,
        cruise_icing_risk=cruise_icing_risk,
    )


def _round_alt(ft: float, step: int = 1000) -> float:
    """Round altitude to the nearest step (default 1000ft)."""
    return round(ft / step) * step


def _compute_regimes(
    analysis: SoundingAnalysis, ceiling_ft: int
) -> list[VerticalRegime]:
    """Compute vertical regimes for a single model.

    1. Collect transition altitudes into a sorted set
    2. Classify each pair by checking midpoint against cloud/icing data
    3. Merge adjacent regimes with identical conditions
    """
    # Collect all transition altitudes, rounded to nearest 1000ft
    # to avoid tiny slivers from slightly different model boundaries
    transitions: set[float] = {0.0, float(ceiling_ft)}

    for cl in analysis.cloud_layers:
        transitions.add(_round_alt(cl.base_ft))
        transitions.add(_round_alt(cl.top_ft))

    for zone in analysis.icing_zones:
        transitions.add(_round_alt(zone.base_ft))
        transitions.add(_round_alt(zone.top_ft))

    if analysis.indices and analysis.indices.freezing_level_ft is not None:
        transitions.add(_round_alt(analysis.indices.freezing_level_ft))

    # Clamp to [0, ceiling_ft] and sort
    sorted_alts = sorted(t for t in transitions if 0 <= t <= ceiling_ft)

    # Ensure we have at least two points
    if len(sorted_alts) < 2:
        return [VerticalRegime(
            floor_ft=0,
            ceiling_ft=float(ceiling_ft),
            in_cloud=False,
            label="Clear",
        )]

    # Classify each segment
    raw_regimes: list[VerticalRegime] = []
    for i in range(len(sorted_alts) - 1):
        floor = sorted_alts[i]
        ceil = sorted_alts[i + 1]
        if ceil - floor < 1:  # skip degenerate slivers
            continue

        midpoint = (floor + ceil) / 2
        in_cloud = _point_in_cloud(midpoint, analysis)
        icing_risk, icing_type = _point_icing(midpoint, analysis)
        label = _regime_label(in_cloud, icing_risk, icing_type)

        raw_regimes.append(VerticalRegime(
            floor_ft=floor,
            ceiling_ft=ceil,
            in_cloud=in_cloud,
            icing_risk=icing_risk,
            icing_type=icing_type,
            label=label,
        ))

    # Merge adjacent regimes with identical conditions
    if not raw_regimes:
        return [VerticalRegime(
            floor_ft=0,
            ceiling_ft=float(ceiling_ft),
            in_cloud=False,
            label="Clear",
        )]

    merged: list[VerticalRegime] = [raw_regimes[0]]
    for regime in raw_regimes[1:]:
        prev = merged[-1]
        if (
            prev.in_cloud == regime.in_cloud
            and prev.icing_risk == regime.icing_risk
            and prev.icing_type == regime.icing_type
        ):
            # Extend the previous regime
            merged[-1] = VerticalRegime(
                floor_ft=prev.floor_ft,
                ceiling_ft=regime.ceiling_ft,
                in_cloud=prev.in_cloud,
                icing_risk=prev.icing_risk,
                icing_type=prev.icing_type,
                label=prev.label,
            )
        else:
            merged.append(regime)

    return merged


def _point_in_cloud(altitude_ft: float, analysis: SoundingAnalysis) -> bool:
    """Check if an altitude falls within any cloud layer."""
    for cl in analysis.cloud_layers:
        if cl.base_ft <= altitude_ft <= cl.top_ft:
            return True
    return False


def _point_icing(
    altitude_ft: float, analysis: SoundingAnalysis
) -> tuple[IcingRisk, IcingType]:
    """Return the worst icing risk/type at an altitude."""
    worst_risk = IcingRisk.NONE
    worst_type = IcingType.NONE
    for zone in analysis.icing_zones:
        if zone.base_ft <= altitude_ft <= zone.top_ft:
            if _ICING_ORDER.index(zone.risk) > _ICING_ORDER.index(worst_risk):
                worst_risk = zone.risk
                worst_type = zone.icing_type
    return worst_risk, worst_type


def _regime_label(in_cloud: bool, icing_risk: IcingRisk, icing_type: IcingType) -> str:
    """Generate a human-readable label for a regime."""
    if not in_cloud and icing_risk == IcingRisk.NONE:
        return "Clear"

    parts: list[str] = []
    if in_cloud:
        parts.append("In cloud")
    if icing_risk != IcingRisk.NONE:
        icing_str = f"icing {icing_risk.value.upper()}"
        if icing_type != IcingType.NONE:
            icing_str += f" ({icing_type.value})"
        parts.append(icing_str)

    return ", ".join(parts)


def _cruise_icing_status(
    soundings: dict[str, SoundingAnalysis],
    cruise_altitude_ft: int,
) -> tuple[bool, IcingRisk]:
    """Check if cruise altitude is in icing across any model.

    Returns (cruise_in_icing, worst_icing_risk).
    """
    cruise_in_icing = False
    worst_risk = IcingRisk.NONE

    for analysis in soundings.values():
        for zone in analysis.icing_zones:
            if zone.base_ft <= cruise_altitude_ft <= zone.top_ft:
                cruise_in_icing = True
                if _ICING_ORDER.index(zone.risk) > _ICING_ORDER.index(worst_risk):
                    worst_risk = zone.risk

    return cruise_in_icing, worst_risk


def _descend_below_icing(
    soundings: dict[str, SoundingAnalysis],
) -> AltitudeAdvisory | None:
    """Compute descend-below-icing advisory aggregated across models.

    Per model: escape altitude = max(freezing_level, lowest_cloud_base) to exit
    icing via warm air or clear air. Falls back to lowest icing zone base.
    Subtract margin. Aggregate: min() across models.
    """
    has_icing = any(
        len(sa.icing_zones) > 0 for sa in soundings.values()
    )
    if not has_icing:
        return None

    per_model_ft: dict[str, float | None] = {}

    for model_key, analysis in soundings.items():
        if not analysis.icing_zones:
            per_model_ft[model_key] = None
            continue

        # Freezing level
        fz_level = (
            analysis.indices.freezing_level_ft
            if analysis.indices and analysis.indices.freezing_level_ft is not None
            else None
        )

        # Lowest cloud base that has icing (cloud layers in icing temp range)
        lowest_cloud_base: float | None = None
        for cl in analysis.cloud_layers:
            # Check if this cloud overlaps any icing zone
            for zone in analysis.icing_zones:
                if cl.base_ft < zone.top_ft and cl.top_ft > zone.base_ft:
                    if lowest_cloud_base is None or cl.base_ft < lowest_cloud_base:
                        lowest_cloud_base = cl.base_ft
                    break

        # Escape altitude: want to be below both freezing and cloud
        candidates: list[float] = []
        if fz_level is not None:
            candidates.append(fz_level)
        if lowest_cloud_base is not None:
            candidates.append(lowest_cloud_base)

        if candidates:
            escape = min(candidates) - _ICING_MARGIN_FT
        else:
            # Fallback: lowest icing zone base
            escape = min(z.base_ft for z in analysis.icing_zones) - _ICING_MARGIN_FT

        per_model_ft[model_key] = max(escape, 0)

    valid_alts = [v for v in per_model_ft.values() if v is not None]
    if not valid_alts:
        return None

    worst_case = min(valid_alts)

    return AltitudeAdvisory(
        advisory_type="descend_below_icing",
        altitude_ft=worst_case,
        feasible=True,
        reason=f"Descend below {worst_case:.0f}ft to exit icing conditions",
        per_model_ft=per_model_ft,
    )


def _climb_above_icing(
    soundings: dict[str, SoundingAnalysis],
    flight_ceiling_ft: int,
) -> AltitudeAdvisory | None:
    """Compute climb-above-icing advisory aggregated across models.

    Per model: max(highest_icing_zone_top, highest_cloud_top_in_icing_temps) + margin.
    Aggregate: max() across models. Feasible if <= flight_ceiling_ft.
    """
    has_icing = any(
        len(sa.icing_zones) > 0 for sa in soundings.values()
    )
    if not has_icing:
        return None

    per_model_ft: dict[str, float | None] = {}

    for model_key, analysis in soundings.items():
        if not analysis.icing_zones:
            per_model_ft[model_key] = None
            continue

        highest_icing_top = max(z.top_ft for z in analysis.icing_zones)

        # Highest cloud top that overlaps icing
        highest_cloud_in_icing: float = 0
        for cl in analysis.cloud_layers:
            for zone in analysis.icing_zones:
                if cl.base_ft < zone.top_ft and cl.top_ft > zone.base_ft:
                    highest_cloud_in_icing = max(highest_cloud_in_icing, cl.top_ft)
                    break

        escape = max(highest_icing_top, highest_cloud_in_icing) + _ICING_MARGIN_FT
        per_model_ft[model_key] = escape

    valid_alts = [v for v in per_model_ft.values() if v is not None]
    if not valid_alts:
        return None

    worst_case = max(valid_alts)
    feasible = worst_case <= flight_ceiling_ft

    return AltitudeAdvisory(
        advisory_type="climb_above_icing",
        altitude_ft=worst_case,
        feasible=feasible,
        reason=(
            f"Climb above {worst_case:.0f}ft to exit icing conditions"
            if feasible
            else f"Climb above {worst_case:.0f}ft needed but exceeds ceiling ({flight_ceiling_ft}ft)"
        ),
        per_model_ft=per_model_ft,
    )
