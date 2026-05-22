"""Display-unit conversion and formatting, keyed by a user's region preference.

Canonical storage is always SI/aviation-canonical (visibility in meters, pressure
in hPa, temperature in Celsius, altitude in feet, wind in knots, distance in nm).
These helpers convert + format to the region the user prefers, at the display edge,
so we never derive a precise unit from an already-rounded one.

Only visibility is wired into display surfaces today. ``format_qnh`` and
``format_temperature`` exist so QNH (hPa vs inHg) and temperature (C vs F) plug in
at their call sites without new plumbing once those fields are surfaced.
"""

from __future__ import annotations

from typing import Literal

UnitsRegion = Literal["europe", "us"]
# Stored user preference: a concrete region or "auto" (resolve per flight).
UnitsPreference = Literal["auto", "europe", "us"]
DEFAULT_REGION: UnitsRegion = "europe"
DEFAULT_PREFERENCE: UnitsPreference = "auto"


def normalize_preference(pref: str | None) -> UnitsPreference:
    """Coerce a stored value to a known preference (default auto)."""
    return pref if pref in ("auto", "europe", "us") else "auto"


def resolve_units_region(pref: str | None, detected: str | None) -> UnitsRegion:
    """Resolve a stored preference + a detected flight region to a concrete region.

    A forced "europe"/"us" preference wins. "auto" (and anything unknown) uses
    the detected flight region, falling back to europe when it can't be told.
    """
    if pref == "us" or pref == "europe":
        return pref
    return "us" if detected == "us" else "europe"

# Conversion constants (single source of truth — was duplicated as _M_PER_SM).
M_PER_SM = 1609.34
HPA_PER_INHG = 33.8639  # 1 inHg == 33.8639 hPa


def normalize_region(region: str | None) -> UnitsRegion:
    """Coerce an arbitrary stored value to a known region (default europe)."""
    return "us" if region == "us" else "europe"


def format_visibility(meters: float | None, region: str | None = DEFAULT_REGION) -> str | None:
    """Format a visibility in meters for display.

    europe -> meters below 5 km, km up to a >10 km cap.
    us     -> statute miles, capped at >10 SM.
    """
    if meters is None:
        return None
    if normalize_region(region) == "us":
        sm = meters / M_PER_SM
        if sm >= 10:
            return ">10 SM"
        return f"{sm:.1f} SM"
    # europe
    if meters >= 10000:
        return ">10 km"
    if meters >= 5000:
        return f"{meters / 1000:.0f} km"
    return f"{round(meters)} m"


def format_qnh(hpa: float | None, region: str | None = DEFAULT_REGION) -> str | None:
    """Format an altimeter setting / QNH in hPa for display.

    europe -> integer hPa.  us -> inHg to 2 decimals.
    """
    if hpa is None:
        return None
    if normalize_region(region) == "us":
        return f"{hpa / HPA_PER_INHG:.2f} inHg"
    return f"{round(hpa)} hPa"


def format_temperature(
    celsius: float | None,
    region: str | None = DEFAULT_REGION,
    decimals: int = 0,
) -> str | None:
    """Format a temperature in Celsius for display.

    europe -> degrees C.  us -> degrees F.  (US METARs are still C; F is a US
    pilot "thinking" preference, bundled with the us region.)
    """
    if celsius is None:
        return None
    if normalize_region(region) == "us":
        return f"{celsius * 9 / 5 + 32:.{decimals}f}°F"
    return f"{celsius:.{decimals}f}°C"
