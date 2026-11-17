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


def resolve_cruise_speed_ias(aircraft_speed_kt: float | None) -> float | None:
    """Resolve the cruise IAS (kt) to use: the flight's aircraft's cruise speed.

    Speed belongs to the aircraft alone (#777) — profiles no longer carry one,
    and a flight with no aircraft resolves to the owner's default aircraft
    before it gets here. Nullish or non-positive values (unset / 0 /
    negative) yield ``None``, and callers keep their generic default. Mirror
    of ``resolveCruiseSpeedIAS`` in ``atmo.ts``.
    """
    if aircraft_speed_kt is not None and aircraft_speed_kt > 0:
        return float(aircraft_speed_kt)
    return None


# --- Compressible speeds (#1005 flight path) ---------------------------------
#
# Server-only: the jet preset of the climb/descent flight path needs Mach, which
# the density-ratio ``ias_to_tas_isa`` above cannot express. Nothing on the web
# side mirrors these (the path is computed on the server; #1005 decision 3).

_ISA_SEA_LEVEL_SOUND_KT = 661.4788  # speed of sound at 288.15 K
_TROPOPAUSE_FT = _TROPOPAUSE_M * _FT_PER_M
_ISA_TROPOPAUSE_TEMP_K = _ISA_SEA_LEVEL_TEMP_K - _ISA_LAPSE_K_PER_M * _TROPOPAUSE_M  # 216.65


def isa_temperature_k(alt_ft: float) -> float:
    """ISA temperature (K) at a pressure altitude; isothermal above the tropopause."""
    alt_m = min(max(0.0, alt_ft) / _FT_PER_M, _TROPOPAUSE_M)
    return _ISA_SEA_LEVEL_TEMP_K - _ISA_LAPSE_K_PER_M * alt_m


def isa_pressure_ratio(alt_ft: float) -> float:
    """ISA static pressure ratio p/p0 at a pressure altitude (troposphere + lower stratosphere)."""
    alt_ft = max(0.0, alt_ft)
    if alt_ft <= _TROPOPAUSE_FT:
        return (isa_temperature_k(alt_ft) / _ISA_SEA_LEVEL_TEMP_K) ** 5.25588
    p_trop = (_ISA_TROPOPAUSE_TEMP_K / _ISA_SEA_LEVEL_TEMP_K) ** 5.25588
    return p_trop * math.exp(-(alt_ft - _TROPOPAUSE_FT) / 20806.0)


def speed_of_sound_kt(alt_ft: float) -> float:
    """ISA speed of sound (kt) at a pressure altitude."""
    return _ISA_SEA_LEVEL_SOUND_KT * math.sqrt(isa_temperature_k(alt_ft) / _ISA_SEA_LEVEL_TEMP_K)


def cas_to_mach_isa(cas_kt: float, alt_ft: float) -> float:
    """Calibrated airspeed (kt) → Mach under ISA, compressible (subsonic)."""
    a0 = _ISA_SEA_LEVEL_SOUND_KT
    qc_over_p0 = (1.0 + 0.2 * (cas_kt / a0) ** 2) ** 3.5 - 1.0
    qc_over_p = qc_over_p0 / isa_pressure_ratio(alt_ft)
    return math.sqrt(5.0 * ((qc_over_p + 1.0) ** (2.0 / 7.0) - 1.0))


def mach_to_tas_isa(mach: float, alt_ft: float) -> float:
    """Mach → true airspeed (kt) under ISA."""
    return mach * speed_of_sound_kt(alt_ft)


def cas_to_tas_isa(cas_kt: float, alt_ft: float) -> float:
    """Calibrated airspeed (kt) → true airspeed (kt) under ISA, compressible."""
    return mach_to_tas_isa(cas_to_mach_isa(cas_kt, alt_ft), alt_ft)
