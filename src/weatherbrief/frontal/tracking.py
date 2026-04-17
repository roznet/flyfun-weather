"""Zone timeseries construction and frontal clearance timing.

Builds per-zone, per-hour frontal presence timeseries from pre-reshaped
fields. Uses a two-pass approach: first computes the mean gradient field
across all forecast hours (the "background"), then detects fronts as
transient gradient anomalies above that background.

This automatically filters persistent orographic gradients (Alps, Pyrenees,
Dinaric Alps) and sea-land thermal contrasts without per-zone thresholds.
"""

from __future__ import annotations

import logging

import numpy as np

from weatherbrief.frontal.detect import (
    classify_front_type,
    compute_frontal_zones,
    compute_frontal_zones_dual,
)
from weatherbrief.frontal.zones import ZONES, find_fronts_in_regions

logger = logging.getLogger(__name__)

# Anomaly threshold: gradient must exceed the time-mean by this much (K/100km).
# A front passing through a zone for ~12h out of 72h barely moves the mean,
# so its anomaly is close to its full gradient value. Persistent orographic
# gradients have anomaly ~0 since they ARE the mean.
_ANOMALY_THRESHOLD = 1.0

# Absolute gradient floor: even with anomaly filtering, require the raw
# gradient to exceed this. Prevents weak noise from triggering in zones
# with very low background gradients.
_ABSOLUTE_FLOOR = 2.0


def _compute_mean_gradient(
    model_forecasts: dict[int, dict],
    lat: np.ndarray,
    lon: np.ndarray,
    hours: range | list[int],
    terrain_mask: np.ndarray | None = None,
    field_name: str = "T850",
) -> np.ndarray:
    """Compute time-mean gradient field for a given variable across all hours.

    This is the "background" gradient — persistent features like orographic
    gradients and sea-land contrasts. Used as a baseline for anomaly detection.

    field_name : key in the fields dict ("T850" or "theta_e").
    """
    gradient_sum = np.zeros((len(lat), len(lon)))
    count = 0

    for h in hours:
        if h not in model_forecasts:
            continue
        fields = model_forecasts[h]
        result = compute_frontal_zones(
            fields[field_name], lat, lon, terrain_mask=terrain_mask,
        )
        gradient_sum += result["gradient"]
        count += 1

    if count == 0:
        return gradient_sum

    mean_gradient = gradient_sum / count
    logger.info(
        "Background %s gradient: mean=%.2f, max=%.2f K/100km (%d hours)",
        field_name, mean_gradient.mean(), mean_gradient.max(), count,
    )
    return mean_gradient


def apply_anomaly_filter(
    zones_result: dict,
    mean_t_gradient: np.ndarray,
    mean_te_gradient: np.ndarray,
    anomaly_threshold: float = _ANOMALY_THRESHOLD,
    absolute_floor: float = _ABSOLUTE_FLOOR,
    te_anomaly_threshold: float | None = None,
    te_absolute_floor: float | None = None,
) -> np.ndarray:
    """Per-channel anomaly filtering for dual-detected frontal points.

    Each point is checked against the background of the channel that detected
    it: T-detected points against the T background, θe-detected points against
    the θe background. Points detected by both channels pass if either
    channel's anomaly check passes.

    θe gradients are naturally ~2× larger than T850 gradients (moisture
    amplifies thermal contrasts). The θe thresholds default to 2× the T
    thresholds to maintain equivalent filtering stringency.

    Returns a boolean mask of points that survive filtering.
    """
    if te_anomaly_threshold is None:
        te_anomaly_threshold = anomaly_threshold * 2.0
    if te_absolute_floor is None:
        te_absolute_floor = absolute_floor * 2.0

    detected_by = zones_result["detected_by"]

    t_anomaly = zones_result["gradient"] - mean_t_gradient
    t_anomaly_ok = (
        (t_anomaly > anomaly_threshold)
        & (zones_result["gradient"] > absolute_floor)
    )

    te_anomaly = zones_result["te_gradient"] - mean_te_gradient
    te_anomaly_ok = (
        (te_anomaly > te_anomaly_threshold)
        & (zones_result["te_gradient"] > te_absolute_floor)
    )

    anomaly_mask = np.zeros_like(zones_result["frontal_mask"])
    has_t = (detected_by & 1) > 0
    has_te = (detected_by & 2) > 0
    anomaly_mask[has_t] |= t_anomaly_ok[has_t]
    anomaly_mask[has_te] |= te_anomaly_ok[has_te]

    return zones_result["frontal_mask"] & anomaly_mask


# Persistence filter: maximum consecutive hours a zone can be flagged
# before detections are suppressed. Real fronts pass through a zone in
# ~6-12h. Persistent detections are typically orographic (Alps, Pyrenees)
# or sea-land contrast artifacts.
_MAX_PERSISTENCE = 72


def build_zone_timeseries(
    model_forecasts: dict[int, dict],
    lat: np.ndarray,
    lon: np.ndarray,
    hours: range | list[int],
    terrain_mask: np.ndarray | None = None,
    t_gradient_threshold: float = 2.0,
    te_gradient_threshold: float = 4.0,
    anomaly_threshold: float = _ANOMALY_THRESHOLD,
    absolute_floor: float = _ABSOLUTE_FLOOR,
    max_persistence: int = _MAX_PERSISTENCE,
    tfp_dilation: int = 2,
) -> dict[str, list[dict]]:
    """Build per-zone, per-hour timeseries of frontal presence for one model.

    Two-pass approach:
    1. Compute mean gradient for both T850 and θe across all hours (background).
    2. For each hour, detect fronts where the gradient exceeds BOTH
       the absolute floor AND the background + anomaly threshold,
       checked per-channel (T points against T background, θe against θe).

    Per-channel anomaly filtering ensures θe-detected points (warm/moisture
    fronts) are compared against the θe background — not penalised by the
    T850 background which may be low for the same region. This follows the
    Hewson (1998) approach where θw/θe is the primary detection field.

    Parameters
    ----------
    model_forecasts : {hour_index: fields_dict} where fields_dict has
        T850, Td850, theta_e, u850, v850 as (n_lat, n_lon) arrays.
    lat, lon : 1D coordinate arrays.
    hours : forecast hours to process.
    terrain_mask : boolean mask (True=valid).
    anomaly_threshold : gradient must exceed background by this much (K/100km).
    absolute_floor : minimum raw gradient to consider (K/100km).

    Returns
    -------
    {zone_name: [{hour, present, type, intensity, orientation}, ...]}
    """
    # Pass 1: compute background gradient fields for both channels
    mean_t_gradient = _compute_mean_gradient(
        model_forecasts, lat, lon, hours, terrain_mask, field_name="T850",
    )
    mean_te_gradient = _compute_mean_gradient(
        model_forecasts, lat, lon, hours, terrain_mask, field_name="theta_e",
    )

    # Pass 2: detect fronts with per-channel anomaly filtering
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
            tfp_dilation=tfp_dilation,
        )

        filtered_mask = apply_anomaly_filter(
            zones_result, mean_t_gradient, mean_te_gradient,
            anomaly_threshold, absolute_floor,
        )

        front_type_grid = classify_front_type(
            zones_result["dT_dx"],
            zones_result["dT_dy"],
            fields["u850"],
            fields["v850"],
            filtered_mask,
            detected_by=zones_result.get("detected_by"),
        )
        region_results = find_fronts_in_regions(
            filtered_mask,
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


def _apply_persistence_filter(
    timeseries: dict[str, list[dict]],
    max_persistence: int = _MAX_PERSISTENCE,
) -> dict[str, list[dict]]:
    """Suppress zones flagged for too many consecutive hours.

    Scans each zone's timeseries for runs of consecutive "present=True"
    entries. If a run exceeds max_persistence hours, all entries in that
    run are set to present=False.
    """
    for zone_name, entries in timeseries.items():
        # Find runs of consecutive "present" hours
        run_start = None
        for i, entry in enumerate(entries):
            if entry["present"]:
                if run_start is None:
                    run_start = i
            else:
                if run_start is not None:
                    run_len = i - run_start
                    if run_len > max_persistence:
                        for j in range(run_start, i):
                            entries[j]["present"] = False
                    run_start = None
        # Handle run that extends to the end
        if run_start is not None:
            run_len = len(entries) - run_start
            if run_len > max_persistence:
                for j in range(run_start, len(entries)):
                    entries[j]["present"] = False

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
