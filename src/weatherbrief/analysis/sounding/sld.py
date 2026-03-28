"""Supercooled Large Droplet (SLD) detection from atmospheric structure.

Two physically-grounded formation mechanisms:

1. **Warm-nose freezing rain** — rain forms above a warm layer (Tw > 0 C)
   aloft, melts, then falls into a subfreezing surface layer where it
   becomes freezing rain.  Freezing rain drops are 0.5-5 mm, far exceeding
   the Appendix C icing envelope (< 50 um).

2. **Collision-coalescence in deep warm-top cloud** — in clouds deeper than
   ~3000 ft with tops warmer than -12 C, ice nucleation is inefficient so
   the Bergeron process cannot deplete supercooled liquid.  Droplets grow
   large via collision-coalescence (the warm-rain process), producing
   freezing drizzle (100-500 um) within the supercooled portion of the
   cloud.

Both mechanisms are model-agnostic: they rely on the temperature/wet-bulb
profile and cloud layer geometry, not on CLMR/ICMR fields.
"""

from __future__ import annotations

from weatherbrief.analysis.sounding.icing_common import MIN_ZONE_HALF_THICKNESS_FT
from weatherbrief.models import (
    DerivedLevel,
    EnhancedCloudLayer,
    IcingRisk,
    PrecipitationAssessment,
    SldZone,
)

# ── Mechanism 1: Warm-nose freezing rain ────────────────────────────

# Warm-nose depth thresholds for severity grading.
# A deeper warm nose melts more precipitation → heavier freezing rain.
_WARM_NOSE_DEPTH_SEVERE_FT = 3000.0


def _warm_nose_sld_zones(
    levels: list[DerivedLevel],
    precipitation: PrecipitationAssessment,
) -> list[SldZone]:
    """Detect SLD from warm-nose freezing rain.

    When the precipitation module has detected a warm nose with freezing
    rain risk, the subfreezing layer below the warm nose is an SLD zone —
    freezing rain drops are large by definition.
    """
    if not precipitation.freezing_rain_risk:
        return []
    if precipitation.warm_nose_base_ft is None:
        return []

    warm_nose_base = precipitation.warm_nose_base_ft
    warm_nose_top = precipitation.warm_nose_top_ft or warm_nose_base

    # The SLD zone is the cold layer below the warm nose where FZRA falls.
    # Find the surface altitude from the lowest level.
    surface_ft: float | None = None
    for lv in levels:
        if lv.altitude_ft is not None:
            surface_ft = lv.altitude_ft
            break

    if surface_ft is None:
        return []

    base_ft = surface_ft
    top_ft = warm_nose_base

    if top_ft - base_ft < 2 * MIN_ZONE_HALF_THICKNESS_FT:
        mid = (top_ft + base_ft) / 2
        base_ft = mid - MIN_ZONE_HALF_THICKNESS_FT
        top_ft = mid + MIN_ZONE_HALF_THICKNESS_FT

    # Severity: deeper warm nose → heavier melting → more intense FZRA
    warm_nose_depth = warm_nose_top - warm_nose_base
    risk = (
        IcingRisk.SEVERE
        if warm_nose_depth >= _WARM_NOSE_DEPTH_SEVERE_FT
        else IcingRisk.MODERATE
    )

    # Mean temperature of levels in the cold layer
    cold_temps = [
        lv.temperature_c
        for lv in levels
        if lv.temperature_c is not None
        and lv.altitude_ft is not None
        and lv.altitude_ft < warm_nose_base
        and lv.temperature_c < 0
    ]
    mean_temp = round(sum(cold_temps) / len(cold_temps), 1) if cold_temps else None

    return [
        SldZone(
            base_ft=base_ft,
            top_ft=top_ft,
            risk=risk,
            mechanism="warm_nose",
            mean_temperature_c=mean_temp,
        )
    ]


# ── Mechanism 2: Collision-coalescence in deep warm-top cloud ───────

# Minimum cloud depth for collision-coalescence growth (ft).
_MIN_CLOUD_DEPTH_FT = 3000.0
_DEEP_CLOUD_DEPTH_FT = 5000.0

# Cloud-top temperature ceiling: above (warmer than) this, ice nucleation
# is too slow for the Bergeron process to deplete supercooled liquid.
_MAX_CLOUD_TOP_TEMP_C = -12.0

# Optional CLMR corroboration threshold (g/kg).  When GFS microphysics
# data is available, elevated CLMR strengthens confidence.
_CLMR_CORROBORATION_G_KG = 0.05


def _cloud_top_temperature(
    cloud: EnhancedCloudLayer,
    levels: list[DerivedLevel],
) -> float | None:
    """Estimate temperature at the cloud top from the sounding profile."""
    if cloud.mean_temperature_c is not None:
        # mean_temperature_c is available on some cloud layers — but we need
        # the top temperature specifically.  Interpolate from levels.
        pass

    # Find the two levels bracketing the cloud top altitude
    best: DerivedLevel | None = None
    best_dist = float("inf")
    for lv in levels:
        if lv.altitude_ft is not None and lv.temperature_c is not None:
            dist = abs(lv.altitude_ft - cloud.top_ft)
            if dist < best_dist:
                best_dist = dist
                best = lv

    return best.temperature_c if best is not None else None


def _has_clmr_corroboration(
    levels: list[DerivedLevel],
    base_ft: float,
    top_ft: float,
) -> bool:
    """Check if any level in the altitude range has elevated CLMR (GFS only)."""
    for lv in levels:
        if lv.altitude_ft is None or lv.cloud_liquid_water_g_kg is None:
            continue
        if base_ft <= lv.altitude_ft <= top_ft:
            if lv.cloud_liquid_water_g_kg >= _CLMR_CORROBORATION_G_KG:
                return True
    return False


def _coalescence_sld_zones(
    levels: list[DerivedLevel],
    cloud_layers: list[EnhancedCloudLayer],
    freezing_level_ft: float | None,
) -> list[SldZone]:
    """Detect SLD from collision-coalescence in deep warm-top clouds.

    For each cloud layer:
    1. Cloud must be deep (> 3000 ft thickness)
    2. Cloud top must be warmer than -12 C (inefficient ice nucleation)
    3. Cloud must overlap the subfreezing range (some portion < 0 C)

    The SLD zone is the subfreezing portion of the cloud where large
    supercooled droplets exist.
    """
    if freezing_level_ft is None:
        return []

    zones: list[SldZone] = []

    for cloud in cloud_layers:
        depth = cloud.top_ft - cloud.base_ft
        if depth < _MIN_CLOUD_DEPTH_FT:
            continue

        # Cloud top must be warmer than -12 C
        top_temp = _cloud_top_temperature(cloud, levels)
        if top_temp is None or top_temp < _MAX_CLOUD_TOP_TEMP_C:
            continue

        # Cloud must have a subfreezing portion (overlaps with T < 0 C)
        # The subfreezing portion starts at the freezing level (or cloud base
        # if cloud base is already above the freezing level)
        sld_base = max(cloud.base_ft, freezing_level_ft)
        sld_top = cloud.top_ft

        if sld_top <= sld_base:
            # Cloud is entirely below freezing level — no supercooled portion
            continue

        # The supercooled portion must itself have subfreezing temperatures
        # (i.e. cloud top is above freezing level but still warm enough for
        # inefficient glaciation)
        if top_temp is not None and top_temp >= 0:
            # Cloud top is above 0 C — this cloud may produce rain but not
            # supercooled large drops within the cloud itself
            continue

        # Expand thin zones
        if sld_top - sld_base < 2 * MIN_ZONE_HALF_THICKNESS_FT:
            mid = (sld_top + sld_base) / 2
            sld_base = mid - MIN_ZONE_HALF_THICKNESS_FT
            sld_top = mid + MIN_ZONE_HALF_THICKNESS_FT

        # Risk grading: deeper cloud or CLMR corroboration → higher risk
        has_clmr = _has_clmr_corroboration(levels, sld_base, sld_top)
        if depth >= _DEEP_CLOUD_DEPTH_FT or has_clmr:
            risk = IcingRisk.MODERATE
        else:
            risk = IcingRisk.LIGHT

        # Mean temperature through the SLD portion
        temps = [
            lv.temperature_c
            for lv in levels
            if lv.temperature_c is not None
            and lv.altitude_ft is not None
            and sld_base <= lv.altitude_ft <= sld_top
        ]
        mean_temp = round(sum(temps) / len(temps), 1) if temps else None

        zones.append(
            SldZone(
                base_ft=sld_base,
                top_ft=sld_top,
                risk=risk,
                mechanism="coalescence",
                mean_temperature_c=mean_temp,
            )
        )

    return zones


# ── Public API ──────────────────────────────────────────────────────


def assess_sld_zones(
    levels: list[DerivedLevel],
    cloud_layers: list[EnhancedCloudLayer] | None = None,
    freezing_level_ft: float | None = None,
    precipitation: PrecipitationAssessment | None = None,
) -> list[SldZone]:
    """Detect SLD zones from atmospheric structure.

    Combines two independent physical mechanisms:
    1. Warm-nose freezing rain (from precipitation assessment)
    2. Collision-coalescence in deep warm-top clouds

    Returns empty list when required inputs are unavailable.
    """
    zones: list[SldZone] = []

    # Mechanism 1: warm-nose freezing rain
    if precipitation is not None:
        zones.extend(_warm_nose_sld_zones(levels, precipitation))

    # Mechanism 2: collision-coalescence — disabled for now.
    # Without droplet size data, deep warm-top clouds cannot be reliably
    # distinguished from ordinary icing clouds.  The coalescence code is
    # retained for future use when GRIB microphysics or satellite data
    # can provide stronger corroboration.
    # if cloud_layers is not None and freezing_level_ft is not None:
    #     zones.extend(
    #         _coalescence_sld_zones(levels, cloud_layers, freezing_level_ft)
    #     )

    # Sort by base altitude
    zones.sort(key=lambda z: z.base_ft)
    return zones
