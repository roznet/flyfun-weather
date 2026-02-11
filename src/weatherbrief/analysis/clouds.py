"""Cloud layer estimation from relative humidity profiles."""

from __future__ import annotations

from weatherbrief.models import CloudLayer, PressureLevelData

IN_CLOUD_THRESHOLD = 80  # percent RH


def estimate_cloud_layers(levels: list[PressureLevelData]) -> list[CloudLayer]:
    """Estimate cloud layers from pressure level RH profiles.

    Cloud likely where RH >= 80%. Base = lowest such level, top = highest.
    Levels should be ordered from surface (high pressure) to altitude (low pressure).
    """
    cloud_layers: list[CloudLayer] = []
    in_cloud = False
    base_ft: float | None = None
    base_pressure: int | None = None

    for level in levels:
        rh = level.relative_humidity_pct
        alt_ft = level.geopotential_height_m * 3.28084 if level.geopotential_height_m else None

        if rh is None or alt_ft is None:
            continue

        if rh >= IN_CLOUD_THRESHOLD and not in_cloud:
            in_cloud = True
            base_ft = alt_ft
            base_pressure = level.pressure_hpa
        elif rh < IN_CLOUD_THRESHOLD and in_cloud:
            in_cloud = False
            cloud_layers.append(
                CloudLayer(
                    base_ft=round(base_ft, 0),
                    top_ft=round(alt_ft, 0),
                    base_pressure_hpa=base_pressure,
                    top_pressure_hpa=level.pressure_hpa,
                )
            )

    if in_cloud and base_ft is not None:
        cloud_layers.append(
            CloudLayer(
                base_ft=round(base_ft, 0),
                top_ft=None,
                base_pressure_hpa=base_pressure,
                note="estimated, top unknown",
            )
        )

    return cloud_layers
