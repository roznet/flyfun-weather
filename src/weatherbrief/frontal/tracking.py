"""Zone timeseries construction and frontal clearance timing.

Builds per-zone, per-hour frontal presence timeseries from pre-reshaped
fields. Clearance timing functions scan these timeseries to find when
fronts clear each zone, and compute inter-model timing spread.
"""

from __future__ import annotations

import logging

import numpy as np

from weatherbrief.frontal.detect import (
    classify_front_type,
    compute_frontal_zones_dual,
)
from weatherbrief.frontal.zones import ZONES, find_fronts_in_regions

logger = logging.getLogger(__name__)


def build_zone_timeseries(
    model_forecasts: dict[int, dict],
    lat: np.ndarray,
    lon: np.ndarray,
    hours: range | list[int],
    terrain_mask: np.ndarray | None = None,
    t_gradient_threshold: float = 2.0,
    te_gradient_threshold: float = 4.0,
) -> dict[str, list[dict]]:
    """Build per-zone, per-hour timeseries of frontal presence for one model.

    Parameters
    ----------
    model_forecasts : {hour_index: fields_dict} where fields_dict has
        T850, Td850, theta_e, u850, v850 as (n_lat, n_lon) arrays.
        Produced by reshape_to_fields() in grid.py.
    lat, lon : 1D coordinate arrays.
    hours : forecast hours to process.
    terrain_mask : boolean mask (True=valid).

    Returns
    -------
    {zone_name: [{hour, present, type, intensity, orientation}, ...]}
    """
    timeseries: dict[str, list[dict]] = {zone: [] for zone in ZONES}

    for h in hours:
        if h not in model_forecasts:
            continue
        fields = model_forecasts[h]

        zones_result = compute_frontal_zones_dual(
            fields["T850"],
            fields["theta_e"],
            lat,
            lon,
            terrain_mask=terrain_mask,
            t_gradient_threshold=t_gradient_threshold,
            te_gradient_threshold=te_gradient_threshold,
        )
        front_type_grid = classify_front_type(
            zones_result["dT_dx"],
            zones_result["dT_dy"],
            fields["u850"],
            fields["v850"],
            zones_result["frontal_mask"],
            detected_by=zones_result.get("detected_by"),
        )
        region_results = find_fronts_in_regions(
            zones_result["frontal_mask"],
            front_type_grid,
            zones_result["gradient"],
            zones_result["front_orientation"],
            lat,
            lon,
            terrain_mask=terrain_mask,
        )
        for zone_name, result in region_results.items():
            timeseries[zone_name].append(
                {
                    "hour": h,
                    "present": result["present"],
                    "type": result.get("type"),
                    "intensity": result.get("intensity"),
                    "orientation": result.get("orientation"),
                }
            )

    return timeseries


# ---------------------------------------------------------------------------
# Clearance timing — derived at query time, not stored
# ---------------------------------------------------------------------------


def find_frontal_clearance_time(
    zone_timeseries: dict[str, list[dict]],
    region_name: str,
    max_horizon: int = 72,
    min_clear_hours: int = 3,
) -> int | None:
    """Find the earliest hour at which a front clears the zone.

    Requires min_clear_hours consecutive clear hours before declaring
    clearance. Returns the first of those clear hours, or None if
    the front persists through max_horizon.
    """
    entries = zone_timeseries.get(region_name, [])
    consecutive_clear = 0
    clearance_start = None

    for entry in entries:
        if entry["hour"] > max_horizon:
            break
        if not entry["present"]:
            if consecutive_clear == 0:
                clearance_start = entry["hour"]
            consecutive_clear += 1
            if consecutive_clear >= min_clear_hours:
                return clearance_start
        else:
            consecutive_clear = 0
            clearance_start = None

    return None


def find_clearance_times_all_models(
    all_timeseries: dict[str, dict[str, list[dict]]],
    region_name: str,
    max_horizon: int = 72,
    min_clear_hours: int = 3,
) -> dict[str, int | None]:
    """For each model, find the clearance time from pre-computed timeseries.

    all_timeseries: {model_name: zone_timeseries_dict}
    """
    return {
        model: find_frontal_clearance_time(
            ts, region_name, max_horizon, min_clear_hours,
        )
        for model, ts in all_timeseries.items()
    }


def compute_timing_spread(clearance_times: dict[str, int | None]) -> dict:
    """Compute inter-model timing spread in hours."""
    valid_times = [t for t in clearance_times.values() if t is not None]

    if len(valid_times) < 2:
        return {
            "spread_hours": None,
            "agreement": False,
            "note": "insufficient_data",
        }

    spread = max(valid_times) - min(valid_times)

    return {
        "spread_hours": int(spread),
        "min_clearance": min(valid_times),
        "max_clearance": max(valid_times),
        "by_model": clearance_times,
        "agreement": spread <= 6,
    }
