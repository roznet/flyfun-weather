"""Multi-model divergence scoring."""

from __future__ import annotations

import math

from weatherbrief.models import AgreementLevel, ModelDivergence

# Thresholds from spec: (good, poor) — spread below good = good, above poor = poor
DIVERGENCE_THRESHOLDS: dict[str, tuple[float, float]] = {
    "temperature_c": (2.0, 5.0),
    "wind_speed_kt": (5.0, 15.0),
    "wind_direction_deg": (20.0, 60.0),
    "precipitation_mm": (1.0, 5.0),
    "cloud_cover_pct": (15.0, 40.0),
    "freezing_level_m": (200.0, 600.0),
    # Sounding-derived metrics
    "freezing_level_ft": (500.0, 1500.0),
    "cape_surface_jkg": (200.0, 500.0),
    "lcl_altitude_ft": (500.0, 1500.0),
    "k_index": (5.0, 15.0),
    "total_totals": (3.0, 8.0),
    "precipitable_water_mm": (5.0, 15.0),
    "lifted_index": (2.0, 5.0),
    "bulk_shear_0_6km_kt": (5.0, 15.0),
    "max_omega_pa_s": (1.0, 5.0),
}

# Variables that wrap around 360 degrees
CIRCULAR_VARIABLES = {"wind_direction_deg"}

# Default thresholds for variables not explicitly listed
DEFAULT_THRESHOLD = (5.0, 15.0)


def _circular_spread(values: list[float]) -> tuple[float, float]:
    """Compute mean and spread for circular (angular) data in degrees.

    Returns (circular_mean_deg, max_angular_spread_deg).
    """
    rads = [math.radians(v) for v in values]
    sin_sum = sum(math.sin(r) for r in rads)
    cos_sum = sum(math.cos(r) for r in rads)
    mean_rad = math.atan2(sin_sum, cos_sum)
    mean_deg = math.degrees(mean_rad) % 360

    # Spread: largest angular difference between any pair
    spread = 0.0
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            diff = abs(values[i] - values[j])
            diff = min(diff, 360 - diff)
            spread = max(spread, diff)

    return mean_deg, spread


def compare_models(
    variable: str, model_values: dict[str, float]
) -> ModelDivergence:
    """Compare a variable across models and score agreement.

    model_values: e.g. {'gfs': 12.3, 'ecmwf': 11.8, 'icon': 12.1}
    """
    values = list(model_values.values())

    if variable in CIRCULAR_VARIABLES:
        mean, spread = _circular_spread(values)
    else:
        mean = sum(values) / len(values)
        spread = max(values) - min(values)

    good_thresh, poor_thresh = DIVERGENCE_THRESHOLDS.get(
        variable, DEFAULT_THRESHOLD
    )

    if spread <= good_thresh:
        agreement = AgreementLevel.GOOD
    elif spread <= poor_thresh:
        agreement = AgreementLevel.MODERATE
    else:
        agreement = AgreementLevel.POOR

    return ModelDivergence(
        variable=variable,
        model_values=model_values,
        mean=round(mean, 2),
        spread=round(spread, 2),
        agreement=agreement,
    )
