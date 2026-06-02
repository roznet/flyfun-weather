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
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from weatherbrief.frontal.case import Case
from weatherbrief.frontal.gates import FrontGateConfig
from weatherbrief.frontal.sources import (
    CaseFieldSource,
    HewsonFieldSource,
    HewsonGrids,
)

logger = logging.getLogger(__name__)


def _as_source(
    src: HewsonFieldSource | Case,
    terrain_mask: np.ndarray | None = None,
) -> HewsonFieldSource:
    """Adapt a bare :class:`Case` to a :class:`CaseFieldSource`.

    The detection functions all take a :class:`HewsonFieldSource`, but a great
    deal of existing code (the ``route-hewson`` / ``route-fronts`` CLI, Phase A
    tests, calibration scripts) calls them with a raw ``Case``. Auto-wrapping
    keeps those call sites working while the production pipeline passes a
    :class:`~weatherbrief.frontal.sources.SnapshotFieldSource`. When a source is
    passed directly it already owns its terrain mask, so the ``terrain_mask``
    argument is ignored in that case.
    """
    if isinstance(src, HewsonFieldSource):
        return src
    return CaseFieldSource(src, terrain_mask=terrain_mask)


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


def sample_hewson_at_route(
    source: HewsonFieldSource | Case,
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
    source : a :class:`HewsonFieldSource` (snapshot or case-backed) or a bare
        :class:`Case` (auto-wrapped in a ``CaseFieldSource``).
    model : model key present in the source (e.g. "era5", "ecmwf").
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
    source = _as_source(source, terrain_mask)
    wps = [(float(la), float(lo)) for la, lo in waypoints]
    hour_list = _normalize_hours(hours, len(wps))

    if model not in source.models:
        raise ValueError(
            f"model {model!r} not in source (available: {source.models})"
        )

    avail_hours = source.available_hours(model)
    if not avail_hours:
        raise ValueError(f"source has no available hours for model {model!r}")

    # Unique integer hours we need to fetch grids for. For a fractional target
    # hour h, we need the two bounding available hours (both must be present for
    # the sample to be valid).
    needed: set[int] = set()
    for h in hour_list:
        if h is None:
            continue
        lo, hi = _bracket(avail_hours, h)
        if lo is not None:
            needed.add(lo)
        if hi is not None:
            needed.add(hi)

    grids_by_hour: dict[int, HewsonGrids] = {}
    for h in sorted(needed):
        grids = source.grids_at_hour(model, h, level_hPa)
        if grids is None:
            raise ValueError(f"grids missing for model={model!r} hour={h}")
        grids_by_hour[h] = grids

    lat_axis, lon_axis = source.lat, source.lon
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
            sample = _sample_grids(grids_by_hour[lo], lat_axis, lon_axis,
                                   wp_lat, wp_lon)
        else:
            w = (h - lo) / (hi - lo)
            s_lo = _sample_grids(grids_by_hour[lo], lat_axis, lon_axis,
                                 wp_lat, wp_lon)
            s_hi = _sample_grids(grids_by_hour[hi], lat_axis, lon_axis,
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


def _sample_grids(
    grids: HewsonGrids,
    lat_axis: np.ndarray,
    lon_axis: np.ndarray,
    wp_lat: float,
    wp_lon: float,
) -> dict[str, float]:
    """Bilinear sample every diagnostic field at one waypoint."""
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
    # Relevance enrichment (attached post-detection by the pipeline stage; the
    # bare detector leaves these None). See tasks/fronts.py:_colocate / _persistence.
    co_location: str | None = None      # "dry" | "partly" | "wet" | "convective" — weather on the boundary
    weather_top_ft: float | None = None # vertical extent of that weather (cloud/convective top), ft
    persistence: float | None = None    # fraction [0,1] of ±window frames the gated gradient holds


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


# ---------------------------------------------------------------------------
# Candidate / decision split
#
# The on-track locator is factored into three stages so the same expensive
# sampling can be re-scored against many gate configs with zero re-sampling
# (cheap calibration sweeps), and so every candidate records *why* it was
# accepted or rejected and by what margin:
#
#   1. sample once          (sample_hewson_at_route on a densified route)
#   2. generate candidates  (every TFP zero-crossing, carrying raw fields)
#   3. apply a gate config  (FrontDecision per candidate: accepted / rejected_by)
#
# accepted decisions → FrontCrossing (merged within merge_km).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrontCandidate:
    """An *unfiltered* TFP zero-crossing along the route.

    Carries the raw fields interpolated to the zero point plus the θe jump
    measured across the generation window — everything a gate config needs to
    accept/reject without touching the samples again. Distances are along-route
    km from the first waypoint.
    """
    lat: float
    lon: float
    distance_km: float
    gradient: float        # |∇θe|  K/100km
    neg_laplacian: float   # −∇²θe  K/(100km)²  (sharpness, diagnostic only)
    advection: float       # −V·∇θe  K/h
    tfp_before: float
    tfp_after: float
    delta_theta_e: float   # θe(after) − θe(before) across the window, K
    airmass_window_km: float  # window the Δθe above was measured across


@dataclass(frozen=True)
class FrontDecision:
    """The verdict of one :class:`FrontGateConfig` on one :class:`FrontCandidate`.

    ``rejected_by`` is the *first* acceptance gate the candidate failed
    (``"gradient"``, ``"anomaly"``, ``"terrain"`` or ``"delta_theta_e"``), or
    ``None`` when accepted.
    ``margins`` gives each gate's signed slack (value − threshold; ≥ 0 means
    the gate passed) so a calibrator can read "rejected: Δθe 4.2 K vs 5.0 gate"
    straight off the trace. ``kind`` / ``intensity`` are always populated (they
    are labels, computed regardless of acceptance).
    """
    candidate: FrontCandidate
    accepted: bool
    rejected_by: str | None
    margins: dict[str, float]
    kind: str              # "cold" | "warm" | "quasi-stationary"
    intensity: str         # "significant" | "classical" | "sharp"

    def to_crossing(self) -> FrontCrossing:
        """Project an accepted decision onto the public :class:`FrontCrossing`."""
        c = self.candidate
        return FrontCrossing(
            lat=c.lat, lon=c.lon, distance_km=c.distance_km,
            gradient=c.gradient, neg_laplacian=c.neg_laplacian,
            advection=c.advection, tfp_before=c.tfp_before,
            tfp_after=c.tfp_after, delta_theta_e=c.delta_theta_e,
            kind=self.kind, intensity=self.intensity,
        )


def generate_front_candidates(
    samples: Sequence[dict],
    *,
    airmass_window_km: float = _DEFAULT_AIRMASS_WINDOW_KM,
) -> list[FrontCandidate]:
    """Every TFP zero-crossing in an ordered route series, **ungated**.

    ``samples`` is one dict per dense route point carrying at least ``lat, lon,
    distance_km, theta_e, gradient, tfp, neg_laplacian, advection`` — the output
    of :func:`sample_hewson_at_route` on a densified route, augmented with
    ``distance_km``. Source-agnostic: a case-backed or snapshot-backed sampler
    feeds it identically.

    The θe jump is measured across ±``airmass_window_km`` here (a single ~15 km
    step understates the air-mass contrast a pilot flies through), so a sweep
    that only varies the threshold gates can reuse one candidate set; a sweep
    that varies the *window* must regenerate.
    """
    out: list[FrontCandidate] = []
    for a, b in zip(samples[:-1], samples[1:]):
        ta, tb = a["tfp"], b["tfp"]
        if not (np.isfinite(ta) and np.isfinite(tb)):
            continue
        if ta == 0.0 and tb == 0.0:
            continue
        if ta * tb >= 0.0:  # no sign change → no zero-crossing on this step
            continue

        denom = ta - tb
        frac = ta / denom if denom != 0.0 else 0.5

        def _interp(key: str) -> float:
            va, vb = a[key], b[key]
            return float(va + frac * (vb - va))

        grad = _interp("gradient")
        if not np.isfinite(grad):
            continue  # can't evaluate any gate without a finite gradient
        dist = _interp("distance_km")
        out.append(
            FrontCandidate(
                lat=_interp("lat"),
                lon=_interp("lon"),
                distance_km=dist,
                gradient=grad,
                neg_laplacian=_interp("neg_laplacian"),
                advection=_interp("advection"),
                tfp_before=float(ta),
                tfp_after=float(tb),
                delta_theta_e=_airmass_delta(samples, dist, airmass_window_km),
                airmass_window_km=airmass_window_km,
            )
        )
    return out


def _classify_kind(advection: float, advection_min: float) -> str:
    if advection > advection_min:
        return "warm"
    if advection < -advection_min:
        return "cold"
    return "quasi-stationary"


def _on_high_terrain(
    terrain_float: np.ndarray,
    lat_axis: np.ndarray,
    lon_axis: np.ndarray,
    lat: float,
    lon: float,
) -> bool:
    """True if (lat, lon) sits on a masked (high-terrain) cell — orographic θe,
    not a synoptic front. Mirrors the 2-D map gate (:func:`_gate_vertex`).

    ``terrain_float`` is the boolean mask pre-cast to float (1.0 valid / 0.0
    masked); the cast is hoisted to the caller so it happens once per gate run,
    not once per candidate."""
    valid = bilinear_sample(terrain_float, lat_axis, lon_axis, lat, lon)
    return bool(np.isfinite(valid) and valid < 0.5)


def apply_gate_config(
    candidates: Sequence[FrontCandidate],
    config: FrontGateConfig,
    *,
    background: np.ndarray | None = None,
    terrain_mask: np.ndarray | None = None,
    lat_axis: np.ndarray | None = None,
    lon_axis: np.ndarray | None = None,
) -> list[FrontDecision]:
    """Score each candidate against ``config`` → one :class:`FrontDecision` each.

    Acceptance is the AND of: the magnitude gate (|∇θe| ≥ ``gradient_min`` — a
    significant air-mass boundary); the gradient-anomaly gate (|∇θe| above the
    time-mean ``background`` by ≥ ``anomaly_min`` — rejects *persistent*
    orographic / sea-land gradients); the high-terrain gate (the cell is not
    masked out); and the air-mass-jump gate (|Δθe| ≥ ``delta_theta_e_min`` — a
    genuine change of air mass, not a gradient col). The first failing gate is
    recorded in ``rejected_by``. Classification (``advection_min``) and intensity
    (from |∇θe|) are labels only and reject nothing.

    The anomaly and terrain gates only fire when ``background`` / ``terrain_mask``
    (and the grid ``lat_axis`` / ``lon_axis`` to sample them) are supplied — the
    same machinery the 2-D map and off-track paths use (:func:`_gate_vertex`,
    :func:`extract_gated_fronts`). Without them this is the legacy magnitude-only
    behaviour, so existing callers/tests are unaffected. Wiring them in lets the
    on-track locator reject the orographic θe gradients the other surfaces
    already reject (e.g. a shallow 925 hPa "boundary" pinned to a mountain range).

    The −∇²θe "sharpness" gate was deliberately *not* reinstated: −∇²θe ≈ 0 at
    the TFP zero by construction, so gating on it there suppresses real fronts
    (verified on the 2026-05-31 Channel front). It is still reported per
    candidate as a diagnostic.
    """
    can_sample = lat_axis is not None and lon_axis is not None
    if (background is not None or terrain_mask is not None) and not can_sample:
        # The anomaly/terrain gates need the grid axes to sample background /
        # terrain at the candidate. Without them they'd silently no-op, which
        # reads as "gate passed" — warn rather than quietly skip.
        logger.warning(
            "apply_gate_config: background/terrain_mask supplied without "
            "lat_axis/lon_axis — anomaly/terrain gates skipped."
        )
    # Cast the boolean terrain mask to float ONCE (bilinear_sample needs float),
    # not once per candidate — the copy is ~150 KB on the European grid.
    terrain_float = (
        terrain_mask.astype(np.float64)
        if (terrain_mask is not None and can_sample) else None
    )
    decisions: list[FrontDecision] = []
    for c in candidates:
        delta = c.delta_theta_e
        anomaly = float("nan")
        if config.use_anomaly_filter and background is not None and can_sample:
            bg = bilinear_sample(background, lat_axis, lon_axis, c.lat, c.lon)
            if np.isfinite(bg):
                anomaly = c.gradient - bg
        margins = {
            "gradient": c.gradient - config.gradient_min,
            "delta_theta_e": (
                abs(delta) - config.delta_theta_e_min
                if np.isfinite(delta) else float("nan")
            ),
            "anomaly": (
                anomaly - config.anomaly_min if np.isfinite(anomaly) else float("nan")
            ),
        }
        rejected_by: str | None = None
        # Terrain is checked before anomaly: it's the harder, categorical rule
        # (even a transient synoptic front over high terrain is excluded), so a
        # cell that is both on terrain AND low-anomaly should read "terrain" in
        # the calibration trace, not "anomaly".
        if c.gradient < config.gradient_min:
            rejected_by = "gradient"
        elif (
            terrain_float is not None
            and _on_high_terrain(terrain_float, lat_axis, lon_axis, c.lat, c.lon)
        ):
            rejected_by = "terrain"
        elif np.isfinite(anomaly) and anomaly < config.anomaly_min:
            rejected_by = "anomaly"
        elif not np.isfinite(delta) or abs(delta) < config.delta_theta_e_min:
            rejected_by = "delta_theta_e"
        decisions.append(
            FrontDecision(
                candidate=c,
                accepted=rejected_by is None,
                rejected_by=rejected_by,
                margins=margins,
                kind=_classify_kind(c.advection, config.advection_min),
                intensity=_intensity_label(c.gradient),
            )
        )
    return decisions


def decisions_to_crossings(
    decisions: Sequence[FrontDecision],
    merge_km: float,
) -> list[FrontCrossing]:
    """Accepted decisions → merged :class:`FrontCrossing` list.

    ``merge_km`` is required (pass ``config.merge_km``) — defaulting it silently
    decoupled the merge distance from the active gate recipe, a footgun for new
    callers (PR #198 review).
    """
    accepted = [d.to_crossing() for d in decisions if d.accepted]
    return _merge_crossings(accepted, merge_km)


def detect_front_crossings(
    samples: Sequence[dict],
    *,
    gradient_min: float = _DEFAULT_GRADIENT_MIN,
    delta_theta_e_min: float = _DEFAULT_DELTA_THETA_E_MIN,
    advection_min: float = _DEFAULT_ADVECTION_MIN,
    merge_km: float = _DEFAULT_MERGE_KM,
    airmass_window_km: float = _DEFAULT_AIRMASS_WINDOW_KM,
    config: FrontGateConfig | None = None,
    background: np.ndarray | None = None,
    terrain_mask: np.ndarray | None = None,
    lat_axis: np.ndarray | None = None,
    lon_axis: np.ndarray | None = None,
) -> list[FrontCrossing]:
    """Locate accepted front crossings in a densely-sampled route series.

    Thin convenience wrapper over the candidate/decision split:
    :func:`generate_front_candidates` → :func:`apply_gate_config` →
    :func:`decisions_to_crossings`. Pass a :class:`FrontGateConfig` to gate with
    a named recipe, or rely on the individual keyword gates (kept for backward
    compatibility with existing callers/tests). When both are given the explicit
    ``config`` wins.

    ``background`` / ``terrain_mask`` (+ ``lat_axis`` / ``lon_axis``) enable the
    anomaly and high-terrain gates — see :func:`apply_gate_config`. Omit them for
    the legacy magnitude-only gating.
    """
    if config is None:
        config = FrontGateConfig(
            gradient_min=gradient_min,
            delta_theta_e_min=delta_theta_e_min,
            advection_min=advection_min,
            merge_km=merge_km,
            airmass_window_km=airmass_window_km,
        )
    candidates = generate_front_candidates(
        samples, airmass_window_km=config.airmass_window_km,
    )
    decisions = apply_gate_config(
        candidates, config,
        background=background, terrain_mask=terrain_mask,
        lat_axis=lat_axis, lon_axis=lon_axis,
    )
    return decisions_to_crossings(decisions, config.merge_km)


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


def evaluate_route_fronts(
    source: HewsonFieldSource | Case,
    model: str,
    waypoints: Sequence[tuple[float, float]],
    hour: float,
    config: FrontGateConfig,
    *,
    terrain_mask: np.ndarray | None = None,
) -> list[FrontDecision]:
    """Densify → sample → generate candidates → score against ``config``.

    Returns the full per-candidate decision trace (accepted *and* rejected),
    using ``config``'s level / step / window. Callers wanting just the accepted
    crossings pass the result through :func:`decisions_to_crossings`; calibration
    sweeps keep the trace and re-score the same candidate set with
    :func:`apply_gate_config`.
    """
    dense = densify_route(waypoints, step_km=config.step_km)
    samples = sample_hewson_at_route(
        source, model,
        [(la, lo) for la, lo, _ in dense],
        hours=hour,
        level_hPa=config.level_hPa,
        terrain_mask=terrain_mask,
    )
    for s, (_, _, dist_km) in zip(samples, dense):
        s["distance_km"] = dist_km
    candidates = generate_front_candidates(
        samples, airmass_window_km=config.airmass_window_km,
    )
    return apply_gate_config(candidates, config)


def find_route_fronts(
    source: HewsonFieldSource | Case,
    model: str,
    waypoints: Sequence[tuple[float, float]],
    hour: float,
    *,
    level_hPa: int | None = None,
    step_km: float = _DEFAULT_STEP_KM,
    terrain_mask: np.ndarray | None = None,
    config: FrontGateConfig | None = None,
    **detect_kwargs,
) -> list[FrontCrossing]:
    """End-to-end: densify a route, sample Hewson fields, detect front crossings.

    Convenience orchestrator over :func:`densify_route`,
    :func:`sample_hewson_at_route`, and the candidate/decision split. Accepts a
    :class:`FrontGateConfig` (preferred) or the legacy individual keyword gates
    (forwarded to :func:`detect_front_crossings` for backward compatibility).
    """
    if config is not None:
        return decisions_to_crossings(
            evaluate_route_fronts(
                source, model, waypoints, hour, config,
                terrain_mask=terrain_mask,
            ),
            config.merge_km,
        )
    dense = densify_route(waypoints, step_km=step_km)
    samples = sample_hewson_at_route(
        source, model,
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
    delta_theta_e: float   # signed θe jump (warm − cold) across ±window along ∇θe, K
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
    """Full per-route frontal picture for one model at one valid hour.

    ``decisions`` is the on-track candidate/decision trace (accepted *and*
    rejected) and ``config`` is the gate recipe that produced this analysis —
    both stamped in for reproducibility and so a calibrator can read why each
    candidate passed or failed.
    """
    model: str
    hour: float
    crossings: list[FrontCrossing]      # fronts the track crosses
    nearest: FrontProximity | None      # nearest front (may be on- or off-track)
    level_hPa: int = 850
    config: FrontGateConfig = field(default_factory=FrontGateConfig)
    decisions: list[FrontDecision] = field(default_factory=list)


def _hewson_grids_at(
    source: HewsonFieldSource | Case,
    model: str,
    hour: float,
    *,
    level_hPa: int | None = None,
    terrain_mask: np.ndarray | None = None,
) -> dict | None:
    """Hewson diagnostic grids (gradient, tfp, dT_dx, dT_dy, theta_e) at a
    fractional hour, linearly interpolated between bounding available hours.
    Returns None when the hour is outside the source range.
    """
    source = _as_source(source, terrain_mask)
    avail = source.available_hours(model)
    lo, hi = _bracket(avail, hour)
    if lo is None or hi is None:
        return None

    def _diag(h: int) -> dict | None:
        g = source.grids_at_hour(model, h, level_hPa)
        if g is None:
            return None
        return {
            "gradient": g.gradient, "tfp": g.tfp,
            "dT_dx": g.dT_dx, "dT_dy": g.dT_dy, "theta_e": g.theta_e,
        }

    if lo == hi:
        return _diag(lo)
    g0, g1 = _diag(lo), _diag(hi)
    if g0 is None or g1 is None:
        return None
    w = (hour - lo) / (hi - lo)
    return {k: (1.0 - w) * g0[k] + w * g1[k] for k in g0}


def grids_at_fractional_hour(
    source: HewsonFieldSource | Case,
    model: str,
    hour: float,
    *,
    level_hPa: int | None = None,
    terrain_mask: np.ndarray | None = None,
) -> HewsonGrids | None:
    """Full :class:`HewsonGrids` at a fractional hour, linearly interpolated.

    Brackets ``hour`` between the two bounding available integer hours and
    blends every field. Returns ``None`` when the hour falls outside the
    source's range. Used by the 2-D contour extractor / calibration map, which
    want all diagnostics (advection for classification) at an arbitrary time.
    """
    source = _as_source(source, terrain_mask)
    avail = source.available_hours(model)
    lo, hi = _bracket(avail, hour)
    if lo is None or hi is None:
        return None
    g_lo = source.grids_at_hour(model, lo, level_hPa)
    if lo == hi:
        return g_lo
    g_hi = source.grids_at_hour(model, hi, level_hPa)
    if g_lo is None or g_hi is None:
        return None
    w = (hour - lo) / (hi - lo)
    fields = ("theta_e", "gradient", "tfp", "neg_laplacian",
              "advection", "tendency", "dT_dx", "dT_dy")
    return HewsonGrids(**{
        f: (1.0 - w) * getattr(g_lo, f) + w * getattr(g_hi, f)
        for f in fields
    })


def compute_background_gradient(
    source: HewsonFieldSource | Case,
    model: str,
    *,
    level_hPa: int | None = None,
    terrain_mask: np.ndarray | None = None,
    hour_stride: int = 6,
) -> np.ndarray:
    """Time-mean |∇θe| over the source's forecast hours — the persistent
    (orographic / sea-land) background used to anomaly-filter route fronts.

    Delegates to :meth:`HewsonFieldSource.background_gradient`; kept as a
    module-level function for the existing callers (and so a bare ``Case`` is
    auto-wrapped). The stride is in *hours*, not array indices, so it behaves
    the same for hourly and 3-hourly sources.
    """
    source = _as_source(source, terrain_mask)
    return source.background_gradient(
        model, level_hPa, hour_stride=hour_stride,
    )


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
    source: HewsonFieldSource | Case,
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
    source = _as_source(source, terrain_mask)
    if terrain_mask is None:
        terrain_mask = source.terrain_mask
    grids = _hewson_grids_at(
        source, model, hour, level_hPa=level_hPa, terrain_mask=terrain_mask,
    )
    if grids is None:
        return []
    grad, tfp = grids["gradient"], grids["tfp"]
    dtdx, dtdy, the = grids["dT_dx"], grids["dT_dy"], grids["theta_e"]
    lat, lon = source.lat, source.lon

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
    source: HewsonFieldSource | Case,
    model: str,
    waypoints: Sequence[tuple[float, float]],
    hour: float,
    *,
    config: FrontGateConfig | None = None,
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

    Pass a :class:`FrontGateConfig` as ``config`` to source every gate / geometry
    parameter from one recipe (preferred — avoids unpacking ~9 fields at the call
    site). When ``config`` is given it overrides the individual keyword gates.

    ``background`` (when pre-supplied) MUST be the time-mean |∇θe| at the *same*
    ``level_hPa`` as this scan — it is the level's own persistent baseline. In a
    multi-level loop, compute one background per level (or leave it ``None`` to
    let this function compute the matching one). The array carries no level tag,
    so a mismatched background silently distorts the anomaly filter.
    """
    if config is not None:
        level_hPa = config.level_hPa
        proximity_km = config.proximity_km
        step_km = config.step_km
        gradient_min = config.gradient_min
        delta_theta_e_min = config.delta_theta_e_min
        anomaly_min = config.anomaly_min
        airmass_window_km = config.airmass_window_km
        use_anomaly_filter = config.use_anomaly_filter
        approach_dh = config.approach_dh
    source = _as_source(source, terrain_mask)
    if terrain_mask is None:
        terrain_mask = source.terrain_mask
    dense = densify_route(waypoints, step_km=step_km)
    if len(dense) < 1:
        return None
    bbox = _route_bbox(dense, proximity_km)
    if use_anomaly_filter and background is None:
        background = compute_background_gradient(
            source, model, level_hPa=level_hPa, terrain_mask=terrain_mask,
        )

    def _cells(h: float) -> list[GatedFrontPoint]:
        return extract_gated_fronts(
            source, model, h, bbox=bbox, level_hPa=level_hPa,
            terrain_mask=terrain_mask, background=background,
            gradient_min=gradient_min, delta_theta_e_min=delta_theta_e_min,
            anomaly_min=anomaly_min, airmass_window_km=airmass_window_km,
        )

    near = _nearest_front(dense, _cells(hour))
    if near is None:
        return None
    dist, cell = near

    trend, rate = "unknown", None
    if approach_dh is not None:
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
    source: HewsonFieldSource | Case,
    model: str,
    waypoints: Sequence[tuple[float, float]],
    hour: float,
    *,
    config: FrontGateConfig | None = None,
    terrain_mask: np.ndarray | None = None,
    # Legacy per-gate overrides — used only when ``config`` is None, for the
    # handful of callers/tests that still pass individual thresholds. Prefer
    # building a FrontGateConfig. Each defaults to None meaning "not supplied"
    # (the config default applies); to *disable* the approach look-ahead pass a
    # full ``config=FrontGateConfig(approach_dh=None)`` rather than the legacy arg.
    level_hPa: int | None = None,
    step_km: float | None = None,
    proximity_km: float | None = None,
    approach_dh: float | None = None,
    gradient_min: float | None = None,
    delta_theta_e_min: float | None = None,
    advection_min: float | None = None,
    merge_km: float | None = None,
    airmass_window_km: float | None = None,
    anomaly_min: float | None = None,
    use_anomaly_filter: bool | None = None,
) -> RouteFrontAnalysis:
    """End-to-end per-route frontal analysis for one model at one valid hour.

    Source-agnostic and config-driven: takes a :class:`HewsonFieldSource`
    (production passes a snapshot source; a bare :class:`Case` is auto-wrapped)
    and a :class:`FrontGateConfig` *recipe*. Combines the on-track candidate/
    decision locator with the off-track proximity scan into one structured
    result — "what fronts does this leg cross, and what front is it about to run
    into?" — and stamps the active config + full candidate trace into the
    returned :class:`RouteFrontAnalysis` for reproducibility and calibration.

    The legacy individual-gate keyword arguments are honoured only when
    ``config`` is omitted (they build an ad-hoc config); ``config`` always wins.
    """
    if config is None:
        config = _build_config_from_legacy_kwargs(
            level_hPa=level_hPa, step_km=step_km, proximity_km=proximity_km,
            approach_dh=approach_dh, gradient_min=gradient_min,
            delta_theta_e_min=delta_theta_e_min, advection_min=advection_min,
            merge_km=merge_km, airmass_window_km=airmass_window_km,
            anomaly_min=anomaly_min, use_anomaly_filter=use_anomaly_filter,
        )

    source = _as_source(source, terrain_mask)
    decisions = evaluate_route_fronts(
        source, model, waypoints, hour, config, terrain_mask=terrain_mask,
    )
    crossings = decisions_to_crossings(decisions, config.merge_km)
    nearest = find_nearby_fronts(
        source, model, waypoints, hour,
        config=config, terrain_mask=terrain_mask,
    )
    return RouteFrontAnalysis(
        model=model, hour=float(hour), crossings=crossings, nearest=nearest,
        level_hPa=config.level_hPa, config=config, decisions=decisions,
    )


def _build_config_from_legacy_kwargs(**kw) -> FrontGateConfig:
    """Build a :class:`FrontGateConfig` from the legacy per-gate keyword args,
    dropping any left at ``None`` (not supplied) so the config defaults apply.
    """
    overrides = {name: value for name, value in kw.items() if value is not None}
    base = FrontGateConfig()
    return base.with_overrides(**overrides) if overrides else base
