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

A third detector, :func:`assess_sld_aloft_zones` (#411 step 4, §84), reads the
model's own rain water instead: a band of rain water colder than about -2 °C
with no surface freezing-rain signal under it. Those zones are **unconfirmed**
(no surface report can check them) and are not part of ``sld_zones``: the
aircraft icing card computes them on demand, graded AMBER at most.
"""

from __future__ import annotations

import logging
import math
import os

from weatherbrief.analysis.sounding.icing_common import (
    SUPERCOOLED_RAIN_MIN_QR_KG_KG,
)
from weatherbrief.analysis.sounding.thin_layers import widen_thin_layer
from weatherbrief.models import (
    DerivedLevel,
    EnhancedCloudLayer,
    IcingRisk,
    PrecipitationAssessment,
    SldZone,
)

logger = logging.getLogger(__name__)

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

    base_ft, top_ft = widen_thin_layer(base_ft, top_ft)

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
        sld_base, sld_top = widen_thin_layer(sld_base, sld_top)

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


# ── Mechanism 3: rain water aloft (unconfirmed) ─────────────────────

# §84 (#411 step 4). Rain water at or below 0 °C covers 8 to 12% of the GFS
# European grid on a quiet day, mostly showers just above the freezing level,
# so the band is tightened before it is graded: colder than this ...
SLD_ALOFT_MAX_TEMP_C = -2.0
# ... and at least this deep (bottom level to top level of the run). One
# pressure level on its own has no depth and never makes a band.
SLD_ALOFT_MIN_DEPTH_FT = 1000.0
# Provisional values: the owner picks the final ones from the amber-volume
# replay (decision 1 of 2026-11-15). The replay sweeps them through these
# variables without a code change; production leaves them unset.
_ALOFT_TEMP_ENV = "WB_SLD_ALOFT_MAX_TEMP_C"
_ALOFT_DEPTH_ENV = "WB_SLD_ALOFT_MIN_DEPTH_FT"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        # nan would make every comparison False: the bands would never fire.
        logger.warning("%s=%r is not a finite number: using %s", name, raw, default)
        return default
    return value


def sld_aloft_thresholds() -> tuple[float, float]:
    """(max temperature °C, min depth ft) of an unconfirmed SLD band aloft."""
    return (
        _env_float(_ALOFT_TEMP_ENV, SLD_ALOFT_MAX_TEMP_C),
        _env_float(_ALOFT_DEPTH_ENV, SLD_ALOFT_MIN_DEPTH_FT),
    )


def _is_aloft_rain_level(lv: DerivedLevel, max_temp_c: float) -> bool:
    return (
        lv.altitude_ft is not None
        and lv.temperature_c is not None
        and lv.rain_water_g_kg is not None
        and lv.rain_water_g_kg >= SUPERCOOLED_RAIN_MIN_QR_KG_KG * 1000.0
        and lv.temperature_c <= max_temp_c
    )


def assess_sld_aloft_zones(
    levels: list[DerivedLevel],
    confirmed: list[SldZone] | None = None,
    max_temp_c: float | None = None,
    min_depth_ft: float | None = None,
) -> list[SldZone]:
    """Unconfirmed SLD bands aloft: rain water colder than ``max_temp_c``.

    A band is a run of consecutive levels (in altitude order) that each carry
    rain water at or above the supercooled-rain floor (§24) at or below
    ``max_temp_c``, spanning at least ``min_depth_ft``. A band that overlaps or
    touches a confirmed (surface-reaching) zone is dropped: that air is already
    graded as confirmed SLD, and a band sharing its edge is the same layer
    continuing, not a second one (owner decision 2026-11-22). Models without
    rain water (ECMWF, ICON-EU) yield none, which means "not detected", never
    "no SLD".

    Risk is MODERATE: the icing card grades these AMBER at most whatever the
    risk says, and the cross-section layer shows them like the other zones.
    """
    if max_temp_c is None or min_depth_ft is None:
        env_temp, env_depth = sld_aloft_thresholds()
        max_temp_c = env_temp if max_temp_c is None else max_temp_c
        min_depth_ft = env_depth if min_depth_ft is None else min_depth_ft

    ordered = sorted(
        (lv for lv in levels if lv.altitude_ft is not None),
        key=lambda lv: lv.altitude_ft,
    )
    runs: list[list[DerivedLevel]] = []
    current: list[DerivedLevel] = []
    for lv in ordered:
        if _is_aloft_rain_level(lv, max_temp_c):
            current.append(lv)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)

    zones: list[SldZone] = []
    for run in runs:
        base_ft = run[0].altitude_ft
        top_ft = run[-1].altitude_ft
        if top_ft - base_ft < min_depth_ft:
            continue
        if any(c.base_ft <= top_ft and c.top_ft >= base_ft for c in confirmed or []):
            continue
        temps = [lv.temperature_c for lv in run]
        zones.append(SldZone(
            base_ft=base_ft,
            top_ft=top_ft,
            base_pressure_hpa=run[0].pressure_hpa,
            top_pressure_hpa=run[-1].pressure_hpa,
            risk=IcingRisk.MODERATE,
            mechanism="rain_aloft",
            mean_temperature_c=round(sum(temps) / len(temps), 1),
        ))
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
