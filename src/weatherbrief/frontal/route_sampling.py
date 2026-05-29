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
    level_hPa: int | None = None,
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
    level_hPa : pressure level to sample (925/850/700). Defaults to the
        case's 850 hPa field, or its first available level. 925 hPa is the
        sharper signal for GA-altitude fronts (see the May-4 calibration).
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
        h: _compute_hour_grids(case, model, h, avail_hours, terrain_mask, level_hPa)
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
    level_hPa: int | None = None,
) -> _HourGrids:
    """Compute diagnostic grids + tendency for one integer hour."""
    fields = case.fields(model, hour, level_hPa)
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
        tendency=_tendency_grid(case, model, hour, avail_hours, level_hPa),
    )


def _tendency_grid(
    case: Case,
    model: str,
    hour: int,
    avail_hours: list[int],
    level_hPa: int | None = None,
) -> np.ndarray:
    """∂θe/∂t at `hour`, using centered/forward/backward diff.

    Mirrors the fallback logic in `_cmd_plot_hewson`: prefer centered
    difference, fall back to one-sided at the ends of the range. Returns
    a NaN-filled grid when neither neighbour is available.
    """
    prev_h = max((h for h in avail_hours if h < hour), default=None)
    next_h = min((h for h in avail_hours if h > hour), default=None)

    if prev_h is not None and next_h is not None:
        f_prev = case.fields(model, prev_h, level_hPa)["theta_e"]
        f_next = case.fields(model, next_h, level_hPa)["theta_e"]
        return (f_next - f_prev) / (next_h - prev_h)
    if next_h is not None:
        f_here = case.fields(model, hour, level_hPa)["theta_e"]
        f_next = case.fields(model, next_h, level_hPa)["theta_e"]
        return (f_next - f_here) / (next_h - hour)
    if prev_h is not None:
        f_prev = case.fields(model, prev_h, level_hPa)["theta_e"]
        f_here = case.fields(model, hour, level_hPa)["theta_e"]
        return (f_here - f_prev) / (hour - prev_h)

    # Single hour in the case — no tendency possible
    shape = case.fields(model, hour, level_hPa)["theta_e"].shape
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


# ---------------------------------------------------------------------------
# Route densification + front-crossing detection
#
# The raw per-waypoint sampler above answers "what are the field values at my
# turning points?". To *locate a front relative to the route* we instead need
# to walk the route at fine spacing and find where TFP changes sign — the
# Hewson front locator — gated so we keep real fronts and reject the ubiquitous
# weak/col zero-crossings (see designs/future/hewson-fields-aviation-advisories.md
# §2 and the May-4 calibration: a TFP sign flip with a strong, sharp gradient is
# the discriminator, not gradient magnitude alone).
# ---------------------------------------------------------------------------


# §2.2 / §2.3 thresholds. A front crossing must clear BOTH the magnitude gate
# (|∇θe| — a significant air-mass boundary) and the sharpness gate (−∇²θe — a
# concentrated gradient maximum, not a broad plateau or a col). The advection
# gate only governs cold/warm classification, not acceptance.
_DEFAULT_GRADIENT_MIN = 6.0       # K / 100 km  (>4 significant, >8 classical)
_DEFAULT_NEG_LAPLACIAN_MIN = 1.0  # K / (100 km)²  (concentrated, not a plateau)
_DEFAULT_ADVECTION_MIN = 0.5      # K / h  (below this: quasi-stationary)
_DEFAULT_MERGE_KM = 60.0          # collapse multiple zero-crossings on one front
_DEFAULT_AIRMASS_WINDOW_KM = 75.0  # ± window to measure the θe jump across a front
_DEFAULT_STEP_KM = 15.0           # ~2 samples per 0.25° cell (~28 km)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points (degrees)."""
    r = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlam = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2.0) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlam / 2.0) ** 2
    return float(2.0 * r * np.arcsin(np.sqrt(a)))


def densify_route(
    waypoints: Sequence[tuple[float, float]],
    step_km: float = _DEFAULT_STEP_KM,
) -> list[tuple[float, float, float]]:
    """Insert evenly-spaced points along each leg of a route.

    Returns ``[(lat, lon, cumulative_km), ...]`` starting at the first
    waypoint. Each leg is split into ``ceil(leg_km / step_km)`` segments and
    linearly interpolated in lat/lon — fine for European GA legs where
    great-circle vs rhumb divergence is sub-grid. Cumulative distance uses the
    true great-circle leg length so the along-route axis stays physical.

    A ``step_km`` of ~15 km gives ~2 samples per 0.25° grid cell, enough to
    localise a TFP zero-crossing without aliasing the ~28 km grid.
    """
    wps = [(float(la), float(lo)) for la, lo in waypoints]
    if len(wps) < 2:
        return [(la, lo, 0.0) for la, lo in wps]
    if step_km <= 0:
        raise ValueError(f"step_km must be positive, got {step_km}")

    out: list[tuple[float, float, float]] = [(wps[0][0], wps[0][1], 0.0)]
    cum = 0.0
    for (la0, lo0), (la1, lo1) in zip(wps[:-1], wps[1:]):
        leg_km = haversine_km(la0, lo0, la1, lo1)
        n = max(1, int(np.ceil(leg_km / step_km)))
        for k in range(1, n + 1):
            frac = k / n
            la = la0 + frac * (la1 - la0)
            lo = lo0 + frac * (lo1 - lo0)
            out.append((la, lo, cum + frac * leg_km))
        cum += leg_km
    return out


@dataclass(frozen=True)
class FrontCrossing:
    """One detected front crossing along a route.

    Distances are along-route km from the first waypoint. ``kind`` is from the
    advection sense at the crossing; ``delta_theta_e`` is the θe change across
    a ±window measured in the direction of flight (negative = flying into
    colder air, the classic post-cold-front transition).
    """
    lat: float
    lon: float
    distance_km: float
    gradient: float        # |∇θe|  K/100km
    neg_laplacian: float   # −∇²θe  K/(100km)²  (sharpness)
    advection: float       # −V·∇θe  K/h  (+ warm adv, − cold adv)
    tfp_before: float
    tfp_after: float
    delta_theta_e: float   # θe(after) − θe(before) across the window, K
    kind: str              # "cold" | "warm" | "quasi-stationary"
    intensity: str         # "significant" | "classical" | "sharp"


def _intensity_label(gradient: float) -> str:
    """Map |∇θe| (K/100km) to the §2.2 aviation band name."""
    if gradient >= 12.0:
        return "sharp"        # SIGMET-worthy
    if gradient >= 8.0:
        return "classical"    # stratus/nimbostratus, precip, shear
    return "significant"      # wind shift, some cloud, possible precip


def _airmass_delta(
    samples: Sequence[dict],
    crossing_km: float,
    window_km: float,
) -> float:
    """θe(downroute) − θe(uproute) measured ±``window_km`` around a crossing.

    A single dense step (~15 km) understates the air-mass contrast of a front;
    sampling a wider window captures the actual jump a pilot flies through.
    Falls back to the route ends when the window overruns them.
    """
    dists = [s["distance_km"] for s in samples]
    before_idx = min(
        range(len(samples)), key=lambda i: abs(dists[i] - (crossing_km - window_km))
    )
    after_idx = min(
        range(len(samples)), key=lambda i: abs(dists[i] - (crossing_km + window_km))
    )
    theta_before = samples[before_idx]["theta_e"]
    theta_after = samples[after_idx]["theta_e"]
    if not (np.isfinite(theta_before) and np.isfinite(theta_after)):
        return float("nan")
    return float(theta_after - theta_before)


def detect_front_crossings(
    samples: Sequence[dict],
    *,
    gradient_min: float = _DEFAULT_GRADIENT_MIN,
    neg_laplacian_min: float = _DEFAULT_NEG_LAPLACIAN_MIN,
    advection_min: float = _DEFAULT_ADVECTION_MIN,
    merge_km: float = _DEFAULT_MERGE_KM,
    airmass_window_km: float = _DEFAULT_AIRMASS_WINDOW_KM,
) -> list[FrontCrossing]:
    """Locate front crossings in an ordered, densely-sampled route series.

    ``samples`` is an ordered list of dicts (one per dense route point), each
    carrying at least ``lat, lon, distance_km, theta_e, gradient, tfp,
    neg_laplacian, advection`` — the output shape of
    :func:`sample_hewson_at_route` on a densified route, augmented with
    ``distance_km``. Source-agnostic: a calibration ``Case`` or a live
    precompute NPZ can both feed it.

    A crossing is a TFP sign change between adjacent samples whose interpolated
    zero point clears the gradient (magnitude) and −∇²θe (sharpness) gates. This
    is the Hewson locator restricted to the route: the sharpness gate rejects
    the gradient *cols* that produce half of all TFP zero-crossings, and the
    magnitude gate rejects weak/dry boundaries. Adjacent surviving crossings
    within ``merge_km`` are collapsed to the strongest (one physical front can
    straddle several dense steps).
    """
    raw: list[FrontCrossing] = []
    for a, b in zip(samples[:-1], samples[1:]):
        ta, tb = a["tfp"], b["tfp"]
        if not (np.isfinite(ta) and np.isfinite(tb)):
            continue
        if ta == 0.0 and tb == 0.0:
            continue
        if ta * tb >= 0.0:  # no sign change → no zero-crossing on this step
            continue

        # Linear interpolation fraction to the TFP zero between a and b.
        denom = ta - tb
        frac = ta / denom if denom != 0.0 else 0.5

        def _interp(key: str) -> float:
            va, vb = a[key], b[key]
            return float(va + frac * (vb - va))

        grad = _interp("gradient")
        neg_lap = _interp("neg_laplacian")
        if not (np.isfinite(grad) and np.isfinite(neg_lap)):
            continue
        if grad < gradient_min or neg_lap < neg_laplacian_min:
            continue

        adv = _interp("advection")
        dist = _interp("distance_km")
        if adv > advection_min:
            kind = "warm"
        elif adv < -advection_min:
            kind = "cold"
        else:
            kind = "quasi-stationary"

        raw.append(
            FrontCrossing(
                lat=_interp("lat"),
                lon=_interp("lon"),
                distance_km=dist,
                gradient=grad,
                neg_laplacian=neg_lap,
                advection=adv,
                tfp_before=float(ta),
                tfp_after=float(tb),
                delta_theta_e=_airmass_delta(samples, dist, airmass_window_km),
                kind=kind,
                intensity=_intensity_label(grad),
            )
        )

    return _merge_crossings(raw, merge_km)


def _merge_crossings(
    crossings: list[FrontCrossing], merge_km: float
) -> list[FrontCrossing]:
    """Collapse crossings within ``merge_km`` along-route, keeping the strongest.

    One physical front a few cells thick can trip the TFP sign test on several
    consecutive dense steps; without merging we'd report it as a cluster of
    near-identical fronts.
    """
    if not crossings or merge_km <= 0:
        return crossings
    ordered = sorted(crossings, key=lambda c: c.distance_km)
    merged: list[FrontCrossing] = [ordered[0]]
    for c in ordered[1:]:
        last = merged[-1]
        if c.distance_km - last.distance_km <= merge_km:
            if c.gradient > last.gradient:
                merged[-1] = c  # keep the sharper of the cluster
        else:
            merged.append(c)
    return merged


def find_route_fronts(
    case: Case,
    model: str,
    waypoints: Sequence[tuple[float, float]],
    hour: float,
    *,
    level_hPa: int | None = None,
    step_km: float = _DEFAULT_STEP_KM,
    terrain_mask: np.ndarray | None = None,
    **detect_kwargs,
) -> list[FrontCrossing]:
    """End-to-end: densify a route, sample Hewson fields, detect front crossings.

    Convenience orchestrator over :func:`densify_route`,
    :func:`sample_hewson_at_route`, and :func:`detect_front_crossings`. Extra
    keyword args are forwarded to the detector (gradient_min, etc.).
    """
    dense = densify_route(waypoints, step_km=step_km)
    samples = sample_hewson_at_route(
        case, model,
        [(la, lo) for la, lo, _ in dense],
        hours=hour,
        level_hPa=level_hPa,
        terrain_mask=terrain_mask,
    )
    for s, (_, _, dist_km) in zip(samples, dense):
        s["distance_km"] = dist_km
    return detect_front_crossings(samples, **detect_kwargs)


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
