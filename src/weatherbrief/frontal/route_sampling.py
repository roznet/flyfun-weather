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
from weatherbrief.frontal.detect import (
    compute_frontal_zones,
    compute_hewson_diagnostics,
)

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


# §2.2 / §2.3 acceptance gates. A front crossing must clear BOTH the magnitude
# gate (|∇θe| — a significant air-mass boundary) and the air-mass-jump gate
# (|Δθe| across a ±window — a genuine change of air mass, not a col/plateau).
# The advection gate only governs cold/warm classification, not acceptance.
#
# Why Δθe and not −∇²θe (the original sharpness gate): TFP = −∇|∇θe|·∇̂θe is
# *zero at the gradient maximum* — the front axis — and −∇²θe is also ≈0 there
# by construction (the θe step is near-linear through its inflection). So
# gating −∇²θe ≥ k at the TFP zero-crossing rejects essentially every real
# front (verified on the 2026-05-31 Channel cold front: −∇²θe ≈ 0 at the zero
# on all three models). The gradient gate already rejects weak cols; the θe
# jump is the robust air-mass discriminator. −∇²θe is still reported on the
# FrontCrossing as a diagnostic. See designs note + memory project_hewson_route_locator_gates.
_DEFAULT_GRADIENT_MIN = 6.0        # K / 100 km  (>4 significant, >8 classical)
_DEFAULT_DELTA_THETA_E_MIN = 5.0   # K  (|θe jump| across ±window — air-mass contrast)
_DEFAULT_ADVECTION_MIN = 0.5       # K / h  (below this: quasi-stationary)
_DEFAULT_MERGE_KM = 60.0           # collapse multiple zero-crossings on one front
_DEFAULT_AIRMASS_WINDOW_KM = 75.0  # ± window to measure the θe jump across a front
_DEFAULT_STEP_KM = 15.0            # ~2 samples per 0.25° cell (~28 km)


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
    delta_theta_e_min: float = _DEFAULT_DELTA_THETA_E_MIN,
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
    zero point clears the gradient (magnitude) gate AND whose θe jump across
    ±``airmass_window_km`` clears the air-mass-jump gate. This is the Hewson
    locator restricted to the route: the gradient gate rejects weak/dry
    boundaries, the Δθe gate rejects gradient *cols* (which have a strong local
    gradient but no net change of air mass). Adjacent surviving crossings within
    ``merge_km`` are collapsed to the strongest (one physical front can straddle
    several dense steps).

    Note the air-mass-jump gate replaced an earlier −∇²θe "sharpness" gate: −∇²θe
    is ≈0 at the TFP zero by construction, so gating on it there suppressed real
    fronts (see the module-level threshold comment). −∇²θe is still reported on
    each :class:`FrontCrossing` for diagnostics.
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
        if not np.isfinite(grad) or grad < gradient_min:
            continue

        dist = _interp("distance_km")
        delta_theta_e = _airmass_delta(samples, dist, airmass_window_km)
        if not np.isfinite(delta_theta_e) or abs(delta_theta_e) < delta_theta_e_min:
            continue

        adv = _interp("advection")
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
                neg_laplacian=_interp("neg_laplacian"),
                advection=adv,
                tfp_before=float(ta),
                tfp_after=float(tb),
                delta_theta_e=delta_theta_e,
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


# ---------------------------------------------------------------------------
# Off-track proximity: fronts the route does NOT cross but passes close to.
#
# A pure on-track locator (detect_front_crossings) answers "does a front cross
# my track?". For route-SIGMET it is not enough: a sharp front can sit a few
# tens of km off the route — beside it, or just beyond the destination the route
# stops short of — and still be the dominant hazard, especially if it is moving
# onto the track. find_nearby_fronts extracts the gated front *axis* on the grid
# near the route and reports the single nearest front + whether it is closing.
#
# Orographic / sea-land θe gradients are persistent, not transient, so they are
# rejected by an anomaly filter (gradient minus the time-mean "background"
# gradient), mirroring tracking.apply_anomaly_filter. Without it the scan
# false-alarms on e.g. the Alpine foothills (verified on the 2026-05-31 Leg 1).
# ---------------------------------------------------------------------------


_DEFAULT_PROXIMITY_KM = 120.0      # lateral/ahead search radius around the route
_DEFAULT_ANOMALY_MIN = 2.0         # K/100km gradient anomaly above background
_DEFAULT_APPROACH_DH = 2.0         # h ahead used to judge closing vs receding
_CLOSING_RATE_KM = 8.0             # |Δdistance| over the window to call it moving


@dataclass(frozen=True)
class GatedFrontPoint:
    """One grid cell sitting on a gated front axis (a TFP zero-line)."""
    lat: float
    lon: float
    gradient: float        # |∇θe|  K/100km
    delta_theta_e: float   # |θe jump| across ±window along the gradient, K
    anomaly: float         # gradient − background, K/100km (NaN if no background)


@dataclass(frozen=True)
class FrontProximity:
    """Nearest gated front to a route, with its approach sense.

    ``distance_km`` is the great-circle distance from the closest route point to
    the front axis. ``on_track`` is True when that distance is within the
    densification step (i.e. the front is effectively crossed and also shows up
    in :func:`find_route_fronts`). ``trend`` is one of ``closing`` / ``receding``
    / ``steady`` / ``unknown``; ``closing_km_per_h`` is signed (negative = the
    front is getting closer) or None when no approach test was run.
    """
    distance_km: float
    lat: float
    lon: float
    gradient: float
    delta_theta_e: float
    on_track: bool
    trend: str
    closing_km_per_h: float | None


@dataclass(frozen=True)
class RouteFrontAnalysis:
    """Full per-route frontal picture for one model at one valid hour."""
    model: str
    hour: float
    crossings: list[FrontCrossing]      # fronts the track crosses
    nearest: FrontProximity | None      # nearest front (may be on- or off-track)


def _hewson_grids_at(
    case: Case,
    model: str,
    hour: float,
    *,
    level_hPa: int | None = None,
    terrain_mask: np.ndarray | None = None,
) -> dict | None:
    """Hewson diagnostic grids (gradient, tfp, dT_dx, dT_dy, theta_e) at a
    fractional hour, linearly interpolated between bounding available hours.
    Returns None when the hour is outside the case range.
    """
    avail = case.available_hours(model)
    lo, hi = _bracket(avail, hour)
    if lo is None or hi is None:
        return None

    def _diag(h: int) -> dict:
        f = case.fields(model, h, level_hPa)
        d = compute_hewson_diagnostics(
            f["theta_e"], case.lat, case.lon,
            u=f["u850"], v=f["v850"], terrain_mask=terrain_mask,
        )
        return {
            "gradient": d["gradient"], "tfp": d["tfp"],
            "dT_dx": d["dT_dx"], "dT_dy": d["dT_dy"], "theta_e": f["theta_e"],
        }

    if lo == hi:
        return _diag(lo)
    w = (hour - lo) / (hi - lo)
    g0, g1 = _diag(lo), _diag(hi)
    return {k: (1.0 - w) * g0[k] + w * g1[k] for k in g0}


def compute_background_gradient(
    case: Case,
    model: str,
    *,
    level_hPa: int | None = None,
    terrain_mask: np.ndarray | None = None,
    hour_stride: int = 6,
) -> np.ndarray:
    """Time-mean |∇θe| over the case's forecast hours — the persistent
    (orographic / sea-land) background used to anomaly-filter route fronts.

    Sampled every ``hour_stride`` hours to bound cost; the mean is dominated by
    persistent features either way (a front passing through for a few hours
    barely moves a multi-day mean).
    """
    hours = case.available_hours(model)
    sampled = hours[::hour_stride] or hours
    acc: np.ndarray | None = None
    n = 0
    for h in sampled:
        f = case.fields(model, h, level_hPa)
        if f is None:
            continue
        g = compute_frontal_zones(
            f["theta_e"], case.lat, case.lon, terrain_mask=terrain_mask,
        )["gradient"]
        acc = g if acc is None else acc + g
        n += 1
    if acc is None or n == 0:
        return np.zeros((len(case.lat), len(case.lon)))
    return acc / n


def _route_bbox(
    dense: Sequence[tuple[float, float, float]], margin_km: float,
) -> tuple[float, float, float, float]:
    """(lat_min, lat_max, lon_min, lon_max) around a route + a km margin."""
    las = [p[0] for p in dense]
    los = [p[1] for p in dense]
    mlat = margin_km / 111.0
    mlon = margin_km / (111.0 * float(np.cos(np.radians(np.mean(las)))))
    return (min(las) - mlat, max(las) + mlat, min(los) - mlon, max(los) + mlon)


def extract_gated_fronts(
    case: Case,
    model: str,
    hour: float,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    level_hPa: int | None = None,
    terrain_mask: np.ndarray | None = None,
    background: np.ndarray | None = None,
    gradient_min: float = _DEFAULT_GRADIENT_MIN,
    delta_theta_e_min: float = _DEFAULT_DELTA_THETA_E_MIN,
    anomaly_min: float = _DEFAULT_ANOMALY_MIN,
    airmass_window_km: float = _DEFAULT_AIRMASS_WINDOW_KM,
) -> list[GatedFrontPoint]:
    """Grid cells on a gated front axis (TFP zero-line) within ``bbox``.

    A cell qualifies when (1) TFP changes sign to its east or north neighbour —
    the front axis passes through the cell, (2) |∇θe| ≥ ``gradient_min``,
    (3) when ``background`` is given, the gradient anomaly (gradient − background)
    ≥ ``anomaly_min`` — this rejects persistent orographic / sea-land gradients,
    and (4) the θe jump across ±``airmass_window_km`` along the gradient ≥
    ``delta_theta_e_min``. Same gate philosophy as :func:`detect_front_crossings`,
    applied to the 2-D field instead of the 1-D route series.
    """
    grids = _hewson_grids_at(
        case, model, hour, level_hPa=level_hPa, terrain_mask=terrain_mask,
    )
    if grids is None:
        return []
    grad, tfp = grids["gradient"], grids["tfp"]
    dtdx, dtdy, the = grids["dT_dx"], grids["dT_dy"], grids["theta_e"]
    lat, lon = case.lat, case.lon

    if bbox is None:
        ila = range(len(lat))
        jlo = range(len(lon))
    else:
        la0, la1, lo0, lo1 = bbox
        ila = np.where((lat >= la0) & (lat <= la1))[0]
        jlo = np.where((lon >= lo0) & (lon <= lo1))[0]

    gmag = np.sqrt(dtdx ** 2 + dtdy ** 2)
    gmag = np.where(gmag > 1e-9, gmag, 1e-9)
    ux, uy = dtdx / gmag, dtdy / gmag   # unit gradient (cold→warm), dimensionless

    out: list[GatedFrontPoint] = []
    ny, nx = tfp.shape
    for i in ila:
        for j in jlo:
            t = tfp[i, j]
            if not np.isfinite(t):
                continue
            sign_change = (
                (j + 1 < nx and np.isfinite(tfp[i, j + 1]) and t * tfp[i, j + 1] < 0)
                or (i + 1 < ny and np.isfinite(tfp[i + 1, j]) and t * tfp[i + 1, j] < 0)
            )
            if not sign_change:
                continue
            if terrain_mask is not None and not terrain_mask[i, j]:
                continue  # high-terrain cell — orographic θe, not a synoptic front
            g = float(grad[i, j])
            if not np.isfinite(g) or g < gradient_min:
                continue
            anomaly = float("nan")
            if background is not None:
                anomaly = g - float(background[i, j])
                if anomaly < anomaly_min:
                    continue
            la, lo = float(lat[i]), float(lon[j])
            dlat = (uy[i, j] * airmass_window_km) / 111.0
            dlon = (ux[i, j] * airmass_window_km) / (
                111.0 * float(np.cos(np.radians(la)))
            )
            warm = bilinear_sample(the, lat, lon, la + dlat, lo + dlon)
            cold = bilinear_sample(the, lat, lon, la - dlat, lo - dlon)
            dthe = warm - cold
            if not np.isfinite(dthe) or abs(dthe) < delta_theta_e_min:
                continue
            out.append(GatedFrontPoint(
                lat=la, lon=lo, gradient=g, delta_theta_e=float(dthe),
                anomaly=anomaly,
            ))
    return out


def _nearest_front(
    dense: Sequence[tuple[float, float, float]],
    cells: Sequence[GatedFrontPoint],
) -> tuple[float, GatedFrontPoint] | None:
    """(min great-circle distance, the front cell) from a route to gated cells."""
    best: tuple[float, GatedFrontPoint] | None = None
    for c in cells:
        d = min(haversine_km(la, lo, c.lat, c.lon) for la, lo, _ in dense)
        if best is None or d < best[0]:
            best = (d, c)
    return best


def find_nearby_fronts(
    case: Case,
    model: str,
    waypoints: Sequence[tuple[float, float]],
    hour: float,
    *,
    level_hPa: int | None = None,
    terrain_mask: np.ndarray | None = None,
    proximity_km: float = _DEFAULT_PROXIMITY_KM,
    step_km: float = _DEFAULT_STEP_KM,
    gradient_min: float = _DEFAULT_GRADIENT_MIN,
    delta_theta_e_min: float = _DEFAULT_DELTA_THETA_E_MIN,
    anomaly_min: float = _DEFAULT_ANOMALY_MIN,
    airmass_window_km: float = _DEFAULT_AIRMASS_WINDOW_KM,
    use_anomaly_filter: bool = True,
    approach_dh: float | None = _DEFAULT_APPROACH_DH,
    on_track_km: float | None = None,
    background: np.ndarray | None = None,
) -> FrontProximity | None:
    """Nearest gated front to a route, with a closing/receding verdict.

    Walks the densified route, extracts gated front cells in a ``proximity_km``
    bbox, and returns the single closest front. When ``approach_dh`` is set the
    same scan is repeated ``approach_dh`` hours later and the change in nearest
    distance classifies the front as closing / receding / steady. Returns None
    when no gated front exists within the bbox. Pass a precomputed
    ``background`` to avoid recomputing it across calls.
    """
    dense = densify_route(waypoints, step_km=step_km)
    if len(dense) < 1:
        return None
    bbox = _route_bbox(dense, proximity_km)
    if use_anomaly_filter and background is None:
        background = compute_background_gradient(
            case, model, level_hPa=level_hPa, terrain_mask=terrain_mask,
        )

    def _cells(h: float) -> list[GatedFrontPoint]:
        return extract_gated_fronts(
            case, model, h, bbox=bbox, level_hPa=level_hPa,
            terrain_mask=terrain_mask, background=background,
            gradient_min=gradient_min, delta_theta_e_min=delta_theta_e_min,
            anomaly_min=anomaly_min, airmass_window_km=airmass_window_km,
        )

    near = _nearest_front(dense, _cells(hour))
    if near is None:
        return None
    dist, cell = near

    trend, rate = "unknown", None
    if approach_dh:
        # Track the SAME front forward: find the cell at hour+dh closest to this
        # front's current position (not the nearest-to-route, which could be a
        # different front), then measure how its route distance changed. Comparing
        # nearest-to-route at two times conflates distinct fronts when several are
        # near and gives a meaningless rate.
        later_cells = _cells(hour + approach_dh)
        same = min(
            later_cells,
            key=lambda c: haversine_km(cell.lat, cell.lon, c.lat, c.lon),
            default=None,
        )
        # Only trust the association if the front didn't jump implausibly far.
        if same is not None and haversine_km(cell.lat, cell.lon, same.lat, same.lon) <= proximity_km:
            dist_later = min(haversine_km(la, lo, same.lat, same.lon) for la, lo, _ in dense)
            rate = (dist_later - dist) / approach_dh
            delta = dist_later - dist
            trend = (
                "closing" if delta < -_CLOSING_RATE_KM
                else "receding" if delta > _CLOSING_RATE_KM
                else "steady"
            )

    threshold = on_track_km if on_track_km is not None else step_km
    return FrontProximity(
        distance_km=dist, lat=cell.lat, lon=cell.lon, gradient=cell.gradient,
        delta_theta_e=cell.delta_theta_e, on_track=dist <= threshold,
        trend=trend, closing_km_per_h=rate,
    )


def analyze_route_fronts(
    case: Case,
    model: str,
    waypoints: Sequence[tuple[float, float]],
    hour: float,
    *,
    level_hPa: int | None = None,
    terrain_mask: np.ndarray | None = None,
    step_km: float = _DEFAULT_STEP_KM,
    proximity_km: float = _DEFAULT_PROXIMITY_KM,
    approach_dh: float | None = _DEFAULT_APPROACH_DH,
    gradient_min: float = _DEFAULT_GRADIENT_MIN,
    delta_theta_e_min: float = _DEFAULT_DELTA_THETA_E_MIN,
    advection_min: float = _DEFAULT_ADVECTION_MIN,
    merge_km: float = _DEFAULT_MERGE_KM,
    airmass_window_km: float = _DEFAULT_AIRMASS_WINDOW_KM,
    anomaly_min: float = _DEFAULT_ANOMALY_MIN,
    use_anomaly_filter: bool = True,
) -> RouteFrontAnalysis:
    """End-to-end per-route frontal analysis for one model at one valid hour.

    Combines the on-track locator (:func:`find_route_fronts`) with the off-track
    proximity scan (:func:`find_nearby_fronts`) into one structured result — the
    "what fronts does this leg cross, and what front is it about to run into?"
    answer that feeds route-SIGMET advisories (#168).
    """
    crossings = find_route_fronts(
        case, model, waypoints, hour,
        level_hPa=level_hPa, step_km=step_km, terrain_mask=terrain_mask,
        gradient_min=gradient_min, delta_theta_e_min=delta_theta_e_min,
        advection_min=advection_min, merge_km=merge_km,
        airmass_window_km=airmass_window_km,
    )
    nearest = find_nearby_fronts(
        case, model, waypoints, hour,
        level_hPa=level_hPa, terrain_mask=terrain_mask,
        proximity_km=proximity_km, step_km=step_km, gradient_min=gradient_min,
        delta_theta_e_min=delta_theta_e_min, anomaly_min=anomaly_min,
        airmass_window_km=airmass_window_km, use_anomaly_filter=use_anomaly_filter,
        approach_dh=approach_dh,
    )
    return RouteFrontAnalysis(
        model=model, hour=float(hour), crossings=crossings, nearest=nearest,
    )
