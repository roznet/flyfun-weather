"""ISA atmosphere + cruise-speed helpers.

Backend mirror of ``web/ts/utils/atmo.ts`` — keep the two in sync. Both the
new-flight duration estimate (frontend) and the winds-aloft / headwind advisory
(backend) need to (a) pick which cruise speed to use and (b) convert that cruise
IAS to TAS at altitude, so the logic lives in one place per side.
"""

from __future__ import annotations

import math

# ISA troposphere constants
_TROPOPAUSE_M = 11000.0  # ~36,089 ft — lapse-rate formula valid below this
_ISA_LAPSE_K_PER_M = 0.0065
_ISA_SEA_LEVEL_TEMP_K = 288.15
_FT_PER_M = 3.28084


def ias_to_tas_isa(ias_kt: float, alt_ft: float) -> float:
    """Indicated airspeed (kt) → true airspeed (kt) under the ISA standard atmosphere.

    ``TAS = IAS / √(ρ/ρ₀)``, where ``ρ/ρ₀ = (1 − L·altM/T0)^4.2561`` in the ISA
    troposphere. Altitude is clamped to the tropopause (~36,089 ft): above it
    density is isothermal, so the lapse formula would over-predict TAS. The
    clamp is conservative (slightly understates TAS above FL360) but well within
    planning tolerance. Mirror of ``iasToTasISA`` in ``atmo.ts``.
    """
    alt_m = min(max(0.0, alt_ft) / _FT_PER_M, _TROPOPAUSE_M)
    density_ratio = (1.0 - _ISA_LAPSE_K_PER_M * alt_m / _ISA_SEA_LEVEL_TEMP_K) ** 4.2561
    return ias_kt / math.sqrt(density_ratio)


def resolve_cruise_speed_ias(
    aircraft_speed_kt: float | None,
    profile_speed_kt: float | None,
) -> float | None:
    """Resolve the cruise IAS (kt) to use, applying the app's precedence: the
    selected aircraft's cruise speed first, then the flight-default profile's
    speed. Both inputs are cruise IAS. Nullish or non-positive candidates
    (unset / 0 / negative) are skipped, so a speedless aircraft falls back to
    the profile. Returns ``None`` if neither yields a usable value. Mirror of
    ``resolveCruiseSpeedIAS`` in ``atmo.ts``.
    """
    for candidate in (aircraft_speed_kt, profile_speed_kt):
        if candidate is not None and candidate > 0:
            return float(candidate)
    return None
