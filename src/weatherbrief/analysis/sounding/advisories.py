"""Dynamic altitude advisories derived from sounding analysis.

Replaces the static altitude band system with:
1. Vertical regimes — dynamic slices per model from actual weather boundaries
2. Altitude advisories — actionable highlights aggregated across models
"""

from __future__ import annotations

from weatherbrief.models import (
    AltitudeAdvisories,
    AltitudeAdvisory,
    CATRiskLevel,
    IcingRisk,
    IcingType,
    SoundingAnalysis,
    VerticalRegime,
)

_ICING_ORDER = [IcingRisk.NONE, IcingRisk.LIGHT, IcingRisk.MODERATE, IcingRisk.SEVERE]

_ICING_MARGIN_FT = 500

# ICAO cloud level boundaries (feet AGL)
_CLOUD_LOW_CEILING_FT = 6500
_CLOUD_MID_CEILING_FT = 20000


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
    cat = _cat_turbulence_advisory(soundings)
    if cat is not None:
        advisories.append(cat)
    strong = _strong_motion_advisory(soundings)
    if strong is not None:
        advisories.append(strong)
    cloud_top = _cloud_top_uncertainty_advisory(soundings)
    if cloud_top is not None:
        advisories.append(cloud_top)

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

    for inv in analysis.inversion_layers:
        transitions.add(_round_alt(inv.base_ft))
        transitions.add(_round_alt(inv.top_ft))

    if analysis.indices and analysis.indices.freezing_level_ft is not None:
        transitions.add(_round_alt(analysis.indices.freezing_level_ft))

    # Add ICAO cloud-level boundaries when NWP cloud data is available
    has_nwp_cloud = analysis.cloud_cover_low_pct is not None
    if has_nwp_cloud:
        transitions.add(float(_CLOUD_LOW_CEILING_FT))
        transitions.add(float(_CLOUD_MID_CEILING_FT))

    # Clamp to [0, ceiling_ft] and sort
    sorted_alts = sorted(t for t in transitions if 0 <= t <= ceiling_ft)

    # Ensure we have at least two points
    if len(sorted_alts) < 2:
        cc = _nwp_cloud_cover_at(float(ceiling_ft) / 2, analysis)
        return [VerticalRegime(
            floor_ft=0,
            ceiling_ft=float(ceiling_ft),
            in_cloud=False,
            cloud_cover_pct=cc,
            label=_regime_label(False, IcingRisk.NONE, IcingType.NONE, cc),
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
        inversion = _point_in_inversion(midpoint, analysis)
        cloud_cover = _nwp_cloud_cover_at(midpoint, analysis)
        cat_risk = _point_cat_risk(midpoint, analysis)
        strong_motion = _point_strong_motion(midpoint, analysis)
        label = _regime_label(in_cloud, icing_risk, icing_type, cloud_cover,
                              cat_risk, strong_motion, inversion)

        raw_regimes.append(VerticalRegime(
            floor_ft=floor,
            ceiling_ft=ceil,
            in_cloud=in_cloud,
            icing_risk=icing_risk,
            icing_type=icing_type,
            inversion=inversion,
            cloud_cover_pct=cloud_cover,
            cat_risk=cat_risk,
            strong_vertical_motion=strong_motion,
            label=label,
        ))

    # Merge adjacent regimes with identical conditions
    if not raw_regimes:
        cc = _nwp_cloud_cover_at(float(ceiling_ft) / 2, analysis)
        return [VerticalRegime(
            floor_ft=0,
            ceiling_ft=float(ceiling_ft),
            in_cloud=False,
            cloud_cover_pct=cc,
            label=_regime_label(False, IcingRisk.NONE, IcingType.NONE, cc),
        )]

    merged: list[VerticalRegime] = [raw_regimes[0]]
    for regime in raw_regimes[1:]:
        prev = merged[-1]
        if (
            prev.in_cloud == regime.in_cloud
            and prev.icing_risk == regime.icing_risk
            and prev.icing_type == regime.icing_type
            and prev.inversion == regime.inversion
            and prev.cloud_cover_pct == regime.cloud_cover_pct
            and prev.cat_risk == regime.cat_risk
            and prev.strong_vertical_motion == regime.strong_vertical_motion
        ):
            # Extend the previous regime
            merged[-1] = VerticalRegime(
                floor_ft=prev.floor_ft,
                ceiling_ft=regime.ceiling_ft,
                in_cloud=prev.in_cloud,
                icing_risk=prev.icing_risk,
                icing_type=prev.icing_type,
                inversion=prev.inversion,
                cloud_cover_pct=prev.cloud_cover_pct,
                cat_risk=prev.cat_risk,
                strong_vertical_motion=prev.strong_vertical_motion,
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


def _point_in_inversion(altitude_ft: float, analysis: SoundingAnalysis) -> bool:
    """Check if an altitude falls within any inversion layer."""
    for inv in analysis.inversion_layers:
        if inv.base_ft <= altitude_ft <= inv.top_ft:
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


def _nwp_cloud_cover_at(
    altitude_ft: float, analysis: SoundingAnalysis
) -> float | None:
    """Return the NWP cloud cover % for the ICAO band containing the altitude.

    ICAO bands: Low SFC–6500ft, Mid 6500–20000ft, High 20000ft+.
    Returns None when NWP cloud data is unavailable (e.g. ECMWF).
    """
    if analysis.cloud_cover_low_pct is None:
        return None
    if altitude_ft < _CLOUD_LOW_CEILING_FT:
        return analysis.cloud_cover_low_pct
    if altitude_ft < _CLOUD_MID_CEILING_FT:
        return analysis.cloud_cover_mid_pct
    return analysis.cloud_cover_high_pct


def _regime_label(
    in_cloud: bool,
    icing_risk: IcingRisk,
    icing_type: IcingType,
    cloud_cover_pct: float | None = None,
    cat_risk: str | None = None,
    strong_vertical_motion: bool = False,
    inversion: bool = False,
) -> str:
    """Generate a human-readable label for a regime."""
    parts: list[str] = []

    if not in_cloud and icing_risk == IcingRisk.NONE:
        if cloud_cover_pct is not None and cloud_cover_pct > 0:
            parts.append(f"Clear (cloud {cloud_cover_pct:.0f}%)")
        else:
            parts.append("Clear")
    else:
        if in_cloud:
            if cloud_cover_pct is not None:
                parts.append(f"In cloud {cloud_cover_pct:.0f}%")
            else:
                parts.append("In cloud")
        if icing_risk != IcingRisk.NONE:
            icing_str = f"icing {icing_risk.value.upper()}"
            if icing_type != IcingType.NONE:
                icing_str += f" ({icing_type.value})"
            parts.append(icing_str)

    if inversion:
        parts.append("inversion")
    if cat_risk is not None:
        parts.append(f"CAT {cat_risk.upper()}")
    if strong_vertical_motion:
        parts.append("strong motion")

    return ", ".join(parts)


_CAT_RISK_ORDER = [CATRiskLevel.NONE, CATRiskLevel.LIGHT, CATRiskLevel.MODERATE, CATRiskLevel.SEVERE]

_STRONG_W_FPM = 200.0
_STRONG_MOTION_PROXIMITY_FT = 2000.0


def _point_cat_risk(
    altitude_ft: float, analysis: SoundingAnalysis,
) -> str | None:
    """Return the worst CAT risk level at an altitude, or None."""
    if analysis.vertical_motion is None:
        return None
    worst = CATRiskLevel.NONE
    for layer in analysis.vertical_motion.cat_risk_layers:
        if layer.base_ft <= altitude_ft <= layer.top_ft:
            if _CAT_RISK_ORDER.index(layer.risk) > _CAT_RISK_ORDER.index(worst):
                worst = layer.risk
    if worst == CATRiskLevel.NONE:
        return None
    return worst.value


def _point_strong_motion(
    altitude_ft: float, analysis: SoundingAnalysis,
) -> bool:
    """Check if |w| > 200 fpm at or near an altitude."""
    for lv in analysis.derived_levels:
        if lv.altitude_ft is not None and lv.w_fpm is not None:
            if abs(lv.altitude_ft - altitude_ft) < _STRONG_MOTION_PROXIMITY_FT and abs(lv.w_fpm) > _STRONG_W_FPM:
                return True
    return False


def _cat_turbulence_advisory(
    soundings: dict[str, SoundingAnalysis],
) -> AltitudeAdvisory | None:
    """Generate advisory for significant CAT turbulence."""
    has_cat = any(
        sa.vertical_motion is not None and len(sa.vertical_motion.cat_risk_layers) > 0
        for sa in soundings.values()
    )
    if not has_cat:
        return None

    per_model_ft: dict[str, float | None] = {}
    worst_risk = CATRiskLevel.NONE

    for model_key, analysis in soundings.items():
        if analysis.vertical_motion is None or not analysis.vertical_motion.cat_risk_layers:
            per_model_ft[model_key] = None
            continue
        # Report the altitude of the worst CAT layer
        worst_layer = max(
            analysis.vertical_motion.cat_risk_layers,
            key=lambda l: _CAT_RISK_ORDER.index(l.risk),
        )
        per_model_ft[model_key] = worst_layer.base_ft
        if _CAT_RISK_ORDER.index(worst_layer.risk) > _CAT_RISK_ORDER.index(worst_risk):
            worst_risk = worst_layer.risk

    if worst_risk == CATRiskLevel.NONE:
        return None

    # Collect all CAT layer ranges across models for the reason text
    all_bases = []
    all_tops = []
    for sa in soundings.values():
        if sa.vertical_motion:
            for layer in sa.vertical_motion.cat_risk_layers:
                if _CAT_RISK_ORDER.index(layer.risk) >= _CAT_RISK_ORDER.index(CATRiskLevel.MODERATE):
                    all_bases.append(layer.base_ft)
                    all_tops.append(layer.top_ft)

    if all_bases:
        reason = (
            f"CAT turbulence {worst_risk.value.upper()} "
            f"{min(all_bases):.0f}-{max(all_tops):.0f}ft (low Richardson number)"
        )
    else:
        reason = f"CAT turbulence risk {worst_risk.value.upper()}"

    valid_alts = [v for v in per_model_ft.values() if v is not None]
    if not valid_alts:
        return None

    return AltitudeAdvisory(
        advisory_type="cat_turbulence",
        altitude_ft=min(valid_alts),
        feasible=True,
        reason=reason,
        per_model_ft=per_model_ft,
    )


def _strong_motion_advisory(
    soundings: dict[str, SoundingAnalysis],
) -> AltitudeAdvisory | None:
    """Generate advisory for strong vertical motion (|w| > 200 fpm)."""
    has_strong = any(
        sa.vertical_motion is not None
        and sa.vertical_motion.max_w_fpm is not None
        and abs(sa.vertical_motion.max_w_fpm) > _STRONG_W_FPM
        for sa in soundings.values()
    )
    if not has_strong:
        return None

    per_model_ft: dict[str, float | None] = {}
    max_w = 0.0

    for model_key, analysis in soundings.items():
        vm = analysis.vertical_motion
        if vm is None or vm.max_w_fpm is None or abs(vm.max_w_fpm) <= _STRONG_W_FPM:
            per_model_ft[model_key] = None
            continue
        per_model_ft[model_key] = vm.max_w_level_ft
        max_w = max(max_w, abs(vm.max_w_fpm))

    valid = [v for v in per_model_ft.values() if v is not None]
    if not valid:
        return None

    return AltitudeAdvisory(
        advisory_type="strong_vertical_motion",
        altitude_ft=min(valid),
        feasible=True,
        reason=f"Strong vertical motion up to {max_w:.0f} ft/min",
        per_model_ft=per_model_ft,
    )


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

    # Include cloud top uncertainty in reason when available
    reason_suffix = ""
    for analysis in soundings.values():
        for cl in analysis.cloud_layers:
            if cl.theoretical_max_top_ft is not None:
                reason_suffix = (
                    f" (cloud top {cl.top_ft:.0f}ft, "
                    f"theoretical max {cl.theoretical_max_top_ft:.0f}ft)"
                )
                break
        if reason_suffix:
            break

    if feasible:
        reason = f"Climb above {worst_case:.0f}ft to exit icing conditions{reason_suffix}"
    else:
        reason = (
            f"Climb above {worst_case:.0f}ft needed but exceeds ceiling "
            f"({flight_ceiling_ft}ft){reason_suffix}"
        )

    return AltitudeAdvisory(
        advisory_type="climb_above_icing",
        altitude_ft=worst_case,
        feasible=feasible,
        reason=reason,
        per_model_ft=per_model_ft,
    )


_CLOUD_TOP_UNCERTAINTY_GAP_FT = 2000.0


def _cloud_top_uncertainty_advisory(
    soundings: dict[str, SoundingAnalysis],
) -> AltitudeAdvisory | None:
    """Generate advisory when cloud top uncertainty is significant.

    Triggered when the highest cloud layer has theoretical_max_top_ft
    significantly above its sounding-derived top (>2000ft gap).
    """
    worst_gap = 0.0
    worst_top = 0.0
    worst_max = 0.0
    source = ""

    for model_key, analysis in soundings.items():
        if not analysis.cloud_layers:
            continue
        highest = max(analysis.cloud_layers, key=lambda cl: cl.top_ft)
        if highest.theoretical_max_top_ft is None:
            continue
        gap = highest.theoretical_max_top_ft - highest.top_ft
        if gap > worst_gap:
            worst_gap = gap
            worst_top = highest.top_ft
            worst_max = highest.theoretical_max_top_ft
            # Determine source label
            if (
                analysis.indices
                and analysis.indices.cape_surface_jkg is not None
                and analysis.indices.cape_surface_jkg > 500
                and analysis.indices.el_altitude_ft is not None
            ):
                source = "EL"
            else:
                source = "\u221220\u00b0C"

    if worst_gap < _CLOUD_TOP_UNCERTAINTY_GAP_FT:
        return None

    return AltitudeAdvisory(
        advisory_type="cloud_top_uncertainty",
        altitude_ft=worst_max,
        feasible=True,
        reason=(
            f"Cloud top uncertainty: sounding top {worst_top:.0f}ft, "
            f"theoretical max {worst_max:.0f}ft ({source})"
        ),
    )
