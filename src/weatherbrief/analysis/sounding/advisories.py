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
# Minimum clearance above terrain for a descent escape altitude to be flyable
# (matches the IcingEscape route advisory's terrain_margin_ft default).
_TERRAIN_CLEARANCE_FT = 1000

# ICAO cloud level boundaries (feet AGL)
# Single definition lives in `clouds.py` (the cloud module) — re-exported
# here so existing references keep working. (PR #508 review)
from weatherbrief.analysis.sounding.clouds import (  # noqa: E402
    _CLOUD_LOW_CEILING_FT,
    _CLOUD_MID_CEILING_FT,
)


def compute_altitude_advisories(
    soundings: dict[str, SoundingAnalysis],
    cruise_altitude_ft: int,
    flight_ceiling_ft: int,
    terrain_elevation_ft: float | None = None,
) -> AltitudeAdvisories:
    """Build vertical regimes and altitude advisories from sounding analyses.

    Args:
        soundings: model_key → SoundingAnalysis mapping.
        cruise_altitude_ft: Planned cruise altitude in feet.
        flight_ceiling_ft: Maximum altitude the aircraft can reach.
        terrain_elevation_ft: Terrain elevation at this point (MSL), when
            known — used to mark descent escapes below terrain as infeasible.

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
    descend = _descend_below_icing(soundings, terrain_elevation_ft)
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


def _cloud_diagnostics_at(
    altitude_ft: float, analysis: SoundingAnalysis,
) -> tuple[str | None, float | None, float | None]:
    """Return (coverage, mean_temperature_c, mean_dewpoint_depression_c) at altitude."""
    for cl in analysis.cloud_layers:
        if cl.base_ft <= altitude_ft <= cl.top_ft:
            return cl.coverage.value if hasattr(cl.coverage, "value") else cl.coverage, cl.mean_temperature_c, cl.mean_dewpoint_depression_c
    return None, None, None


def _icing_diagnostics_at(
    altitude_ft: float, analysis: SoundingAnalysis,
) -> tuple[bool, float | None, float | None, float | None]:
    """Return (sld_risk, mean_wet_bulb_c, mean_rh_pct, mean_icing_index) at altitude.

    Returns values from the worst-risk zone when multiple overlap.
    """
    worst_risk = IcingRisk.NONE
    result: tuple[bool, float | None, float | None, float | None] = (False, None, None, None)
    for zone in analysis.icing_zones:
        if zone.base_ft <= altitude_ft <= zone.top_ft:
            if _ICING_ORDER.index(zone.risk) > _ICING_ORDER.index(worst_risk):
                worst_risk = zone.risk
                result = (zone.sld_risk, zone.mean_wet_bulb_c, zone.mean_rh_pct, zone.mean_icing_index)
    return result


def _inversion_diagnostics_at(
    altitude_ft: float, analysis: SoundingAnalysis,
) -> tuple[float | None, bool]:
    """Return (strength_c, surface_based) at altitude."""
    for inv in analysis.inversion_layers:
        if inv.base_ft <= altitude_ft <= inv.top_ft:
            return inv.strength_c, inv.surface_based
    return None, False


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

    # Add cloud boundaries from GFS diagnostics when available;
    # fall back to fixed ICAO boundaries otherwise
    diag = analysis.nwp_cloud_diagnostics
    if diag is not None:
        for layer in (diag.low, diag.mid, diag.high):
            if layer.base_ft is not None and layer.cover_pct and layer.cover_pct > 0:
                transitions.add(_round_alt(layer.base_ft))
            if layer.top_ft is not None and layer.cover_pct and layer.cover_pct > 0:
                transitions.add(_round_alt(layer.top_ft))
        if (diag.convective_base_ft is not None
                and diag.convective_cover_pct and diag.convective_cover_pct > 0):
            transitions.add(_round_alt(diag.convective_base_ft))
        if (diag.convective_top_ft is not None
                and diag.convective_cover_pct and diag.convective_cover_pct > 0):
            transitions.add(_round_alt(diag.convective_top_ft))
    elif analysis.cloud_cover_low_pct is not None:
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

        # Diagnostic values from underlying layers
        cloud_cov, cloud_temp, cloud_dd = _cloud_diagnostics_at(midpoint, analysis)
        sld, tw, rh, ix = _icing_diagnostics_at(midpoint, analysis)
        inv_strength, inv_sfc = _inversion_diagnostics_at(midpoint, analysis)

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
            cloud_coverage=cloud_cov,
            mean_temperature_c=cloud_temp,
            mean_dewpoint_depression_c=cloud_dd,
            sld_risk=sld,
            mean_wet_bulb_c=tw,
            mean_rh_pct=rh,
            mean_icing_index=ix,
            inversion_strength_c=inv_strength,
            inversion_surface_based=inv_sfc,
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
            and prev.cloud_coverage == regime.cloud_coverage
            and prev.sld_risk == regime.sld_risk
        ):
            # Extend the previous regime
            merged[-1] = prev.model_copy(update={"ceiling_ft": regime.ceiling_ft})
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
    """Return the NWP cloud cover % for the band containing the altitude.

    When the diagnostics carry real cloud base/top boundaries, uses them to
    decide whether the altitude falls inside a diagnosed layer. Falls back to
    fixed ICAO bands (SFC–6500ft, 6500–20000ft, 20000ft+) otherwise.

    **The geometry branch is gated on GEOMETRY, not on cover** (PR #508
    review). Band covers without base/top are common — ECMWF publishes
    lcc/mcc/hcc with no per-band boundaries, and so does HRRR — and keying
    the branch on "some band reports a cover" sent those models down the
    geometry path, where no layer could ever match and every altitude fell
    through to ``return 0.0``. That reported a confident 0 % over an
    overcast column. This mirrors the gate ``icing_common
    .nwp_cloud_cover_at_altitude`` already applies (its ``any_diag`` flag).
    """
    diag = analysis.nwp_cloud_diagnostics
    if diag is not None:
        # Only trust the geometry branch when at least one band actually has
        # both bounds. Cover alone is not enough to answer "is this altitude
        # inside the deck?" — the ICAO fallback is the honest answer there.
        has_layer_geometry = any(
            layer.base_ft is not None and layer.top_ft is not None
            for layer in (diag.low, diag.mid, diag.high)
        )
        has_convective_geometry = (
            diag.convective_base_ft is not None
            and diag.convective_top_ft is not None
        )
        if has_layer_geometry or has_convective_geometry:
            # Full diagnostics (GFS): use actual cloud boundaries per layer
            for layer in (diag.low, diag.mid, diag.high):
                if (layer.cover_pct is not None and layer.cover_pct > 0
                        and layer.base_ft is not None and layer.top_ft is not None
                        and layer.base_ft <= altitude_ft <= layer.top_ft):
                    return layer.cover_pct
            # Check convective layer
            if (diag.convective_cover_pct is not None and diag.convective_cover_pct > 0
                    and diag.convective_base_ft is not None
                    and diag.convective_top_ft is not None
                    and diag.convective_base_ft <= altitude_ft <= diag.convective_top_ft):
                return diag.convective_cover_pct
            # Altitude doesn't fall within any diagnosed cloud layer
            return 0.0
        # No usable geometry (ECMWF, HRRR, partial ICON-EU) — ICAO bands.

    # Fallback to ICAO bands with Open-Meteo cloud cover
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
            # ``is_hazardous``: a risk-NONE zone spanning cruise means the method
            # assessed cruise and found no icing — reporting "cruise in icing"
            # there is exactly the existence-vs-hazard confusion this guards.
            if zone.is_hazardous and zone.base_ft <= cruise_altitude_ft <= zone.top_ft:
                cruise_in_icing = True
                if _ICING_ORDER.index(zone.risk) > _ICING_ORDER.index(worst_risk):
                    worst_risk = zone.risk

    return cruise_in_icing, worst_risk


def _descend_below_icing(
    soundings: dict[str, SoundingAnalysis],
    terrain_elevation_ft: float | None = None,
) -> AltitudeAdvisory | None:
    """Compute descend-below-icing advisory aggregated across models.

    Per model: escape altitude = max(freezing_level, lowest icing-cloud base)
    − margin. Either condition alone exits airframe icing — warm air (below
    the freezing level, even in cloud) or clear air (below the lowest
    icing-bearing cloud base, even sub-zero) — so the *higher* of the two is
    the least-penalising valid escape. Falls back to the lowest icing zone
    base (clear-air exit) when neither is known. Aggregate: min() across
    models (worst case).

    Two guards:
    - A model whose precipitation profile flags freezing rain (warm nose over
      a sub-zero surface layer) has NO descent escape — below-cloud air
      carries supercooled precipitation. Its escape is None.
    - When terrain elevation is known and the aggregate escape leaves less
      than ``_TERRAIN_CLEARANCE_FT`` above it, the advisory is kept (the
      meteorological altitude is still true) but marked ``feasible=False``.
    """
    # Only hazardous zones: an Ogimet zone at risk NONE is "assessed, no icing",
    # so counting it would advertise a descent escape from icing that isn't there.
    has_icing = any(
        any(z.is_hazardous for z in sa.icing_zones) for sa in soundings.values()
    )
    if not has_icing:
        return None

    per_model_ft: dict[str, float | None] = {}
    fzra_models: list[str] = []

    for model_key, analysis in soundings.items():
        icing_zones = [z for z in analysis.icing_zones if z.is_hazardous]
        if not icing_zones:
            per_model_ft[model_key] = None
            continue

        # Freezing rain profile: descending stays in (or enters) icing.
        precip = analysis.precipitation
        if precip is not None and precip.freezing_rain_risk:
            per_model_ft[model_key] = None
            fzra_models.append(model_key)
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
            for zone in icing_zones:
                if cl.base_ft < zone.top_ft and cl.top_ft > zone.base_ft:
                    if lowest_cloud_base is None or cl.base_ft < lowest_cloud_base:
                        lowest_cloud_base = cl.base_ft
                    break

        # Escape altitude: below freezing (warm air) OR below cloud (clear
        # air) — the higher of the two suffices.
        candidates: list[float] = []
        if fz_level is not None:
            candidates.append(fz_level)
        if lowest_cloud_base is not None:
            candidates.append(lowest_cloud_base)

        if candidates:
            escape = max(candidates) - _ICING_MARGIN_FT
        else:
            # Fallback: lowest icing zone base
            escape = min(z.base_ft for z in icing_zones) - _ICING_MARGIN_FT

        per_model_ft[model_key] = max(escape, 0)

    valid_alts = [v for v in per_model_ft.values() if v is not None]

    if not valid_alts:
        if fzra_models:
            # Icing exists but every model's profile is freezing rain —
            # there is no descent escape to offer.
            return AltitudeAdvisory(
                advisory_type="descend_below_icing",
                altitude_ft=None,
                feasible=False,
                reason=(
                    "Freezing precipitation profile (warm nose) — "
                    "descending does not exit icing"
                ),
                per_model_ft=per_model_ft,
            )
        return None

    worst_case = min(valid_alts)

    feasible = True
    reason = f"Descend below {worst_case:.0f}ft to exit icing conditions"
    if terrain_elevation_ft is not None and (
        worst_case < terrain_elevation_ft + _TERRAIN_CLEARANCE_FT
    ):
        feasible = False
        reason += f" — below terrain clearance (terrain ~{terrain_elevation_ft:.0f}ft)"
    if fzra_models:
        # A freezing-rain model's escape is None (descending stays in FZRA), and
        # that None must NOT be silently dropped from the aggregate so min() of
        # the *other* models offers a descent (#391 — safety-relevant). Any model
        # showing freezing rain means descent is not a safe universal escape:
        # keep the meteorological altitude but mark it infeasible, mirroring the
        # terrain guard.
        feasible = False
        reason += (
            f" (no descent escape for {', '.join(fzra_models)}: "
            "freezing precipitation profile)"
        )

    return AltitudeAdvisory(
        advisory_type="descend_below_icing",
        altitude_ft=worst_case,
        feasible=feasible,
        reason=reason,
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
    # Hazardous zones only — see the sibling descend-below advisory. A risk-NONE
    # zone would otherwise set a climb-above-icing altitude for absent icing.
    has_icing = any(
        any(z.is_hazardous for z in sa.icing_zones) for sa in soundings.values()
    )
    if not has_icing:
        return None

    per_model_ft: dict[str, float | None] = {}

    for model_key, analysis in soundings.items():
        icing_zones = [z for z in analysis.icing_zones if z.is_hazardous]
        if not icing_zones:
            per_model_ft[model_key] = None
            continue

        highest_icing_top = max(z.top_ft for z in icing_zones)

        # Highest cloud top that overlaps icing
        highest_cloud_in_icing: float = 0
        for cl in analysis.cloud_layers:
            for zone in icing_zones:
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
            # Determine source label using the shared effective_cape function
            # so the label matches the enrichment logic in __init__.py.
            from weatherbrief.analysis.sounding.convective import effective_cape
            eff_cape = effective_cape(analysis.indices) if analysis.indices else None
            if (
                eff_cape is not None
                and eff_cape > 500
                and analysis.indices
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
