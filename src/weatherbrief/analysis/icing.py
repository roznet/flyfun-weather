"""Icing band detection from pressure level data."""

from __future__ import annotations

from weatherbrief.models import IcingBand, IcingRisk, PressureLevelData


def assess_icing_at_level(level: PressureLevelData) -> IcingBand:
    """Assess icing risk at a single pressure level."""
    temp = level.temperature_c
    rh = level.relative_humidity_pct
    altitude_ft = level.geopotential_height_m * 3.28084 if level.geopotential_height_m else None

    # Default: no risk
    risk = IcingRisk.NONE

    if temp is not None and rh is not None:
        if 0 >= temp >= -20 and rh >= 60:
            # In the icing temperature band with moisture
            if -10 <= temp <= 0 and rh > 80:
                risk = IcingRisk.SEVERE if rh > 90 else IcingRisk.MODERATE
            elif -20 <= temp < -10 and rh > 80:
                risk = IcingRisk.MODERATE if rh > 90 else IcingRisk.LIGHT
            else:
                risk = IcingRisk.LIGHT

    return IcingBand(
        pressure_hpa=level.pressure_hpa,
        altitude_ft=round(altitude_ft, 0) if altitude_ft else None,
        temperature_c=temp,
        relative_humidity_pct=rh,
        risk=risk,
    )


def assess_icing_profile(levels: list[PressureLevelData]) -> list[IcingBand]:
    """Assess icing across all pressure levels."""
    return [assess_icing_at_level(level) for level in levels]
