"""Sample Hewson diagnostic fields at route waypoints.

The precompute layer (Phase B) does not exist yet — diagnostics are
computed on demand from the Case's raw θe / u / v. That is fine: a
European 0.25° grid evaluates in well under a second and a typical
route hits only a handful of forecast hours.

Three interpolation axes:
    spatial   — bilinear on the Case's lat/lon grid
    temporal  — linear between bounding available hours
    (vertical — deferred; 850 hPa only until Phase B brings 925/700)

Output shape matches the design doc §Phase A:
    {lat, lon, hour, theta_e, gradient, tfp, neg_laplacian,
     advection, tendency}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from weatherbrief.frontal.case import Case
from weatherbrief.frontal.detect import compute_hewson_diagnostics

logger = logging.getLogger(__name__)


# Diagnostic field keys exposed per waypoint. `theta_e` is the raw
# air-mass label (§2.1); the rest are the Hewson derivatives.
_FIELD_KEYS = (
    "theta_e",
    "gradient",
    "tfp",
    "neg_laplacian",
    "advection",
    "tendency",
)


@dataclass(frozen=True)
class _HourGrids:
    """Per-hour diagnostic grids + raw theta_e, keyed under _FIELD_KEYS.

    Tendency is filled in separately because it needs adjacent hours.
    """
    theta_e: np.ndarray
    gradient: np.ndarray
    tfp: np.ndarray
    neg_laplacian: np.ndarray
    advection: np.ndarray
    tendency: np.ndarray  # NaN-filled if neither neighbour is available


def sample_hewson_at_route(
    case: Case,
    model: str,
    waypoints: Sequence[tuple[float, float]],
    hours: Sequence[float] | float | None = None,
    *,
    terrain_mask: np.ndarray | None = None,
) -> list[dict]:
    """Return per-waypoint Hewson diagnostics.

    Parameters
    ----------
    case : loaded Case (see `load_case`).
    model : model key present in the case (e.g. "era5", "ecmwf").
    waypoints : iterable of (lat_deg, lon_deg) pairs.
    hours : forecast hour(s) at which to sample. One of:
        - None  → sample all waypoints at hour 0
        - float → same hour for every waypoint
        - sequence of floats → per-waypoint hour (must match len(waypoints))
        Fractional hours are linearly interpolated between the two bounding
        available hours.
    terrain_mask : optional boolean (True=valid) matching the case grid.

    Returns
    -------
    list[dict], one entry per waypoint, with keys:
        lat, lon, hour, theta_e, gradient, tfp, neg_laplacian,
        advection, tendency
    Values are NaN when a waypoint sits outside the grid or the
    requested hour is outside the available range.
    """
    wps = [(float(la), float(lo)) for la, lo in waypoints]
    hour_list = _normalize_hours(hours, len(wps))

    if model not in case.models:
        raise ValueError(
            f"model {model!r} not in case (available: {case.models})"
        )

    avail_hours = case.available_hours(model)
    if not avail_hours:
        raise ValueError(f"case has no available hours for model {model!r}")

    # Unique integer hours we need to compute diagnostics for. For a
    # fractional target hour h, we need ⌊h⌋ and ⌈h⌉ (both must be in
    # avail_hours for the sample to be valid).
    needed: set[int] = set()
    for h in hour_list:
        if h is None:
            continue
        lo, hi = _bracket(avail_hours, h)
        if lo is not None:
            needed.add(lo)
        if hi is not None:
            needed.add(hi)

    grids_by_hour: dict[int, _HourGrids] = {
        h: _compute_hour_grids(case, model, h, avail_hours, terrain_mask)
        for h in sorted(needed)
    }

    results: list[dict] = []
    for (wp_lat, wp_lon), h in zip(wps, hour_list):
        entry: dict = {"lat": wp_lat, "lon": wp_lon, "hour": h}
        if h is None:
            for k in _FIELD_KEYS:
                entry[k] = float("nan")
            results.append(entry)
            continue

        lo, hi = _bracket(avail_hours, h)
        if lo is None or hi is None:
            for k in _FIELD_KEYS:
                entry[k] = float("nan")
            results.append(entry)
            continue

        if lo == hi:
            sample = _sample_grids(grids_by_hour[lo], case.lat, case.lon,
                                   wp_lat, wp_lon)
        else:
            w = (h - lo) / (hi - lo)
            s_lo = _sample_grids(grids_by_hour[lo], case.lat, case.lon,
                                 wp_lat, wp_lon)
            s_hi = _sample_grids(grids_by_hour[hi], case.lat, case.lon,
                                 wp_lat, wp_lon)
            sample = {k: (1.0 - w) * s_lo[k] + w * s_hi[k] for k in _FIELD_KEYS}

        entry.update(sample)
        results.append(entry)

    return results


# ---------------------------------------------------------------------------
# Internals


def _normalize_hours(
    hours: Sequence[float] | float | None,
    n_waypoints: int,
) -> list[float | None]:
    if hours is None:
        return [0.0] * n_waypoints
    if isinstance(hours, (int, float)):
        return [float(hours)] * n_waypoints
    out = [float(h) if h is not None else None for h in hours]
    if len(out) != n_waypoints:
        raise ValueError(
            f"hours has length {len(out)} but there are {n_waypoints} waypoints"
        )
    return out


def _bracket(
    avail_hours: list[int], target: float
) -> tuple[int | None, int | None]:
    """Return (lo, hi) from avail_hours bracketing `target`.

    If target is outside the range, returns (None, None). If target
    coincides with an available hour, lo == hi.
    """
    if not avail_hours:
        return None, None
    lo_candidates = [h for h in avail_hours if h <= target]
    hi_candidates = [h for h in avail_hours if h >= target]
    if not lo_candidates or not hi_candidates:
        return None, None
    return max(lo_candidates), min(hi_candidates)


def _compute_hour_grids(
    case: Case,
    model: str,
    hour: int,
    avail_hours: list[int],
    terrain_mask: np.ndarray | None,
) -> _HourGrids:
    """Compute diagnostic grids + tendency for one integer hour."""
    fields = case.fields(model, hour)
    if fields is None:
        raise ValueError(f"fields missing for model={model!r} hour={hour}")

    diag = compute_hewson_diagnostics(
        fields["theta_e"], case.lat, case.lon,
        u=fields["u850"], v=fields["v850"],
        terrain_mask=terrain_mask,
    )

    return _HourGrids(
        theta_e=fields["theta_e"],
        gradient=diag["gradient"],
        tfp=diag["tfp"],
        neg_laplacian=diag["neg_laplacian"],
        advection=diag["advection"],
        tendency=_tendency_grid(case, model, hour, avail_hours),
    )


def _tendency_grid(
    case: Case,
    model: str,
    hour: int,
    avail_hours: list[int],
) -> np.ndarray:
    """∂θe/∂t at `hour`, using centered/forward/backward diff.

    Mirrors the fallback logic in `_cmd_plot_hewson`: prefer centered
    difference, fall back to one-sided at the ends of the range. Returns
    a NaN-filled grid when neither neighbour is available.
    """
    prev_h = max((h for h in avail_hours if h < hour), default=None)
    next_h = min((h for h in avail_hours if h > hour), default=None)

    if prev_h is not None and next_h is not None:
        f_prev = case.fields(model, prev_h)["theta_e"]
        f_next = case.fields(model, next_h)["theta_e"]
        return (f_next - f_prev) / (next_h - prev_h)
    if next_h is not None:
        f_here = case.fields(model, hour)["theta_e"]
        f_next = case.fields(model, next_h)["theta_e"]
        return (f_next - f_here) / (next_h - hour)
    if prev_h is not None:
        f_prev = case.fields(model, prev_h)["theta_e"]
        f_here = case.fields(model, hour)["theta_e"]
        return (f_here - f_prev) / (hour - prev_h)

    # Single hour in the case — no tendency possible
    shape = case.fields(model, hour)["theta_e"].shape
    return np.full(shape, np.nan, dtype=np.float64)


def _sample_grids(
    grids: _HourGrids,
    lat_axis: np.ndarray,
    lon_axis: np.ndarray,
    wp_lat: float,
    wp_lon: float,
) -> dict[str, float]:
    """Bilinear sample every field at one waypoint."""
    return {
        k: bilinear_sample(getattr(grids, k), lat_axis, lon_axis, wp_lat, wp_lon)
        for k in _FIELD_KEYS
    }


def bilinear_sample(
    grid: np.ndarray,
    lat_axis: np.ndarray,
    lon_axis: np.ndarray,
    wp_lat: float,
    wp_lon: float,
) -> float:
    """Bilinear interpolation on an ascending (lat, lon) grid.

    Returns NaN when (wp_lat, wp_lon) falls outside the axes. Expects
    lat_axis and lon_axis to be monotonically ascending (the convention
    used throughout `frontal.grid`). No wrap-around on longitude —
    European grids do not cross the antimeridian.
    """
    if not (lat_axis[0] <= wp_lat <= lat_axis[-1]):
        return float("nan")
    if not (lon_axis[0] <= wp_lon <= lon_axis[-1]):
        return float("nan")

    i = int(np.searchsorted(lat_axis, wp_lat, side="right")) - 1
    i = min(max(i, 0), len(lat_axis) - 2)
    j = int(np.searchsorted(lon_axis, wp_lon, side="right")) - 1
    j = min(max(j, 0), len(lon_axis) - 2)

    fy = (wp_lat - lat_axis[i]) / (lat_axis[i + 1] - lat_axis[i])
    fx = (wp_lon - lon_axis[j]) / (lon_axis[j + 1] - lon_axis[j])

    g00 = grid[i, j]
    g01 = grid[i, j + 1]
    g10 = grid[i + 1, j]
    g11 = grid[i + 1, j + 1]

    return float(
        (1.0 - fy) * ((1.0 - fx) * g00 + fx * g01)
        + fy * ((1.0 - fx) * g10 + fx * g11)
    )
