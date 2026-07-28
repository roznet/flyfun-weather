"""Front-detection pipeline stage (issue #195, deliverable D).

Produces the per-briefing ``route_fronts.json`` artifact: for each model that
has a precompute snapshot, locate the fronts along the route — on-track
crossings (sampled at each waypoint's ETA) and the nearest off-track front — at
the level nearest cruise, **storing all three levels** so Part 2's cross-section
bands have data.

Two surfaces, one core (mirrors ``run_advisories`` / ``run_advisories_from_pack``):

* :func:`run_fronts` — called inline by the pipeline when the experimental
  ``auto_front_detection`` preference is on. Takes the in-memory route-point
  analyses (which carry per-waypoint ETAs).
* :func:`run_fronts_from_pack` — recompute twin that reads ``route_analyses.json``
  from a pack, so toggling the preference re-runs cheaply with **zero re-fetch**
  (the expensive grid already lives in the precompute snapshot).

Reads the snapshot via :class:`SnapshotFieldSource` — milliseconds, no network.
Degrades gracefully: models without a snapshot (ukmo / meteofrance / best_match,
or a model whose precompute hasn't run) simply get no front data.

Known limitations carried from the design doc: 850 hPa θe doesn't see low IMC /
fog (§10a.2 — this is free-atmosphere weather); output is qualitative (§8); an
init mismatch between the briefing forecast and the latest snapshot is
acceptable for the smooth advective fields.
"""

from __future__ import annotations

import dataclasses
import logging
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np

from weatherbrief.analysis.sounding.convective import convection_realized
from weatherbrief.frontal.gates import FrontGateConfig
from weatherbrief.frontal.grid import terrain_mask_for_level
from weatherbrief.frontal.route_sampling import (
    RouteFrontAnalysis,
    apply_gate_config,
    decisions_to_crossings,
    densify_route,
    find_nearby_fronts,
    generate_front_candidates,
    haversine_km,
    sample_hewson_at_route,
)
from weatherbrief.frontal.sources import SnapshotFieldSource
from weatherbrief.hewson.precompute import (
    DEFAULT_LEVELS,
    latest_snapshot,
    resolve_output_dir,
    snapshot_for_window,
)
from weatherbrief.models import (
    FrontChainModel,
    FrontChainNodeModel,
    FrontCrossingModel,
    FrontDecisionModel,
    FrontProximityModel,
    RouteFrontAnalysisModel,
    RouteFrontsManifest,
)

logger = logging.getLogger(__name__)


# Only these models have precompute snapshots (design §6.1). Other advisory
# models degrade gracefully (no front data).
FRONT_MODELS: frozenset[str] = frozenset({"ecmwf", "gfs", "icon"})

# Pressure level → representative MSL altitude (design §4.2 / §6.2).
_LEVEL_FT: dict[int, int] = {925: 2_500, 850: 5_000, 700: 10_000}


def nearest_cruise_level(cruise_altitude_ft: int, available: Sequence[int]) -> int:
    """Pick the stored level whose representative altitude is nearest cruise."""
    avail = [L for L in available if L in _LEVEL_FT] or list(available)
    return min(avail, key=lambda L: abs(_LEVEL_FT.get(L, 5_000) - cruise_altitude_ft))


# ---------------------------------------------------------------------------
# Waypoint extraction
# ---------------------------------------------------------------------------


def _waypoints_with_eta(
    analyses: Sequence,
) -> tuple[list[tuple[float, float]], list[datetime]] | None:
    """Pull ordered ``(lat, lon)`` + ETA datetimes from route-point analyses.

    Requires ≥2 points with finite coords and a resolved ``interpolated_time``.
    Returns ``None`` when the route is too short / lacks ETAs (front detection
    is skipped — nothing to locate along).
    """
    pts: list[tuple[float, float]] = []
    etas: list[datetime] = []
    for a in analyses:
        eta = getattr(a, "interpolated_time", None)
        if eta is None or a.lat is None or a.lon is None:
            continue
        if eta.tzinfo is None:
            eta = eta.replace(tzinfo=timezone.utc)
        pts.append((float(a.lat), float(a.lon)))
        etas.append(eta)
    if len(pts) < 2:
        return None
    return pts, etas


def _cumulative_km(waypoints: Sequence[tuple[float, float]]) -> list[float]:
    cum = [0.0]
    for (la0, lo0), (la1, lo1) in zip(waypoints[:-1], waypoints[1:]):
        cum.append(cum[-1] + haversine_km(la0, lo0, la1, lo1))
    return cum


# ---------------------------------------------------------------------------
# Detection for one (model, level)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Relevance enrichment — co-location (is the boundary "wet"?) + persistence
# (does the gate hold over time, or flicker?). These turn a bare θe zero-crossing
# into something gradeable: a dry, flickering crossing (orographic artifact, e.g.
# the 2026-05-31 Alpine case) is demoted; a wet/convective, persistent one (the
# Dijon cold front the same day) is kept. The θe boundary's *level* is not the
# weather's *level* — weather_top_ft is the cloud/convective top a pilot meets,
# so an overflown front with towering convection still reads as relevant.
# ---------------------------------------------------------------------------

_SIGNIFICANT_COVERAGE = {"bkn", "ovc"}
_PARTLY_COVERAGE = {"few", "sct"}
_CONVECTIVE_RISK = {"moderate", "high", "extreme"}
_PERSIST_OFFSETS_H = (-6, -3, 0, 3, 6)


def _attr(obj: object, name: str, default: object = None) -> object:
    """getattr/dict-get that tolerates pydantic objects or raw dicts (or None)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _enum_val(x: object) -> object:
    """Unwrap an enum to its ``.value`` (passthrough for plain strings/None)."""
    return getattr(x, "value", x)


def _colocate(analyses, model: str, dist_km: float, level_hpa: int):
    """Weather co-located with a crossing → (category, weather_top_ft).

    category ∈ {"dry","partly","wet","convective"}; ``(None, None)`` when the
    per-model column for this point/model isn't available (degrade gracefully).
    """
    if not analyses:
        return None, None
    nm = dist_km / 1.852
    a = min(analyses, key=lambda x: abs((_attr(x, "distance_from_origin_nm", 0.0) or 0.0) - nm))
    sounding = _attr(a, "sounding", None) or {}
    s = sounding.get(model) if hasattr(sounding, "get") else None
    if s is None:
        return None, None
    layers = _attr(s, "cloud_layers", None) or []
    conv = _attr(s, "convective", None)
    indices = _attr(s, "indices", None)
    risk = _enum_val(_attr(conv, "risk_level", None)) if conv is not None else None

    def _spans(cl, level: int) -> bool:
        bp, tp = _attr(cl, "base_pressure_hpa", None), _attr(cl, "top_pressure_hpa", None)
        return bool(bp and tp and tp <= level <= bp)

    def _covers(level: int, coverages: set[str]) -> bool:
        """True if a cloud layer of one of ``coverages`` physically spans ``level``."""
        return any(_spans(cl, level) and _enum_val(_attr(cl, "coverage", None)) in coverages for cl in layers)

    # Cloud top must come from layers spanning the frontal level, not the whole
    # column — else unrelated high cirrus inflates weather_top_ft and false-AMBERs
    # a low wet/partly front (the category is already level-gated via _covers).
    cloud_top = max((_attr(cl, "top_ft", None) or 0.0 for cl in layers if _spans(cl, level_hpa)), default=0.0) or None

    precip_obj = _attr(s, "precipitation", None)
    # surface_intensity is a column-wide surface reading, NOT level-gated like the
    # cloud checks below. So a real boundary at the level + any surface precip →
    # "wet" even if the precipitating cloud doesn't span the level (then there is
    # no spanning layer, so weather_top_ft is None → _grade_crossing treats it as
    # reaching cruise). This is a deliberate conservative bias: surface precip at a
    # detected boundary is weather worth flagging, and erring to AMBER never hides
    # a real front. See test_drizzle_below_front_is_wet.
    precip = precip_obj is not None and _enum_val(_attr(precip_obj, "surface_intensity", "none")) != "none"

    # Convective only when convection is *realized* (#216): a CIN-capped potential
    # risk is not weather on the boundary, and its top_ft is the parcel EL — not a
    # tower a pilot meets. Realized convection keeps the EL as its tower-depth
    # proxy (the overflown-tower Dijon case); a potential-only risk falls through
    # to the cloud-coverage category and the EL is never used as a top. The
    # realized/potential logic + thresholds live in the convective module (shared
    # with the route convective tier); we own only the data extraction here,
    # including the DD→NWP lifted-index fallback (LI is a binary gate signal, not a
    # tier input, so the cross of the §4 DD/NWP boundary is acceptable).
    li = _attr(conv, "lifted_index", None)
    if li is None:
        li = _attr(indices, "nwp_lifted_index", None)
    realized = convection_realized(
        method=_attr(conv, "method", "thermo"),
        cin_jkg=_attr(conv, "cin_jkg", None),
        ml_cape_jkg=_attr(indices, "cape_mixed_layer_jkg", None),
        lifted_index=li,
    )
    if risk in _CONVECTIVE_RISK and realized:
        category = "convective"
        conv_top = _attr(conv, "top_ft", None) or _attr(conv, "el_altitude_ft", None)
        tops = [v for v in (cloud_top, conv_top) if v]
        weather_top_ft = float(max(tops)) if tops else None
    else:
        # Cloud must span the frontal level to count — a high cirrus deck unrelated
        # to a low front is neither "wet" nor "partly" (it would otherwise
        # false-AMBER). weather_top reflects the spanning cloud only; the parcel EL
        # is never a realized top here.
        weather_top_ft = cloud_top
        if _covers(level_hpa, _SIGNIFICANT_COVERAGE) or precip:
            category = "wet"
        elif _covers(level_hpa, _PARTLY_COVERAGE):
            category = "partly"
        else:
            category = "dry"
    return category, weather_top_ft


# Vertical-linking budget — how far apart (along-route km) two crossings on
# adjacent levels may sit and still be the *same* sloping front. A front tilts
# back over the cold air with height, so the upper-level crossing is displaced;
# the budget bounds that displacement from textbook frontal slopes:
#   925→850 (~760 m gap): cold ~1:50–1:100 → ~40–75 km; warm ~1:150 → ~110 km
#   850→700 (~1500 m gap): cold ~75–150 km; warm ~225 km
# These are wider than the 60 km ``merge_km`` (which collapses duplicates *at one
# level*) — the old coherence cluster reused merge_km and so under-linked warm
# fronts. Warm fronts get a slope multiplier (shallower slope, larger displacement).
_LINK_BUDGET_KM: dict[tuple[int, int], float] = {
    (925, 850): 100.0,
    (850, 700): 170.0,
}
_WARM_LINK_FACTOR = 1.4   # warm fronts slope shallower → allow more displacement
_UPRIGHT_KM = 30.0        # |head−base| below this reads as a near-vertical front


def _kinds_compatible(a: str, b: str) -> bool:
    """True if two crossings could belong to one front by type.

    cold↔cold and warm↔warm link; ``quasi-stationary`` is an advection-only label
    (weak cross-front wind) and wildcards — it links to either. cold↔warm never
    links: two genuinely different boundaries that happen to be vertically near.
    """
    if a == b:
        return True
    return "quasi-stationary" in (a, b)


def _link_budget_km(lower: int, upper: int, kind_a: str, kind_b: str) -> float:
    """Max along-route displacement for a link between adjacent levels."""
    base = _LINK_BUDGET_KM.get((lower, upper))
    if base is None:
        # Non-adjacent / unknown pair: scale the 925→850 budget by the level gap.
        base = _LINK_BUDGET_KM[(925, 850)] * abs(lower - upper) / 75.0
    if "warm" in (kind_a, kind_b):
        base *= _WARM_LINK_FACTOR
    return base


def _consensus_kind(kinds: Sequence[str]) -> str:
    """Front kind for a chain: majority of the decisive (non-quasi) labels, else
    quasi-stationary. Ties fall to the first decisive label (bottom-most node)."""
    decisive = [k for k in kinds if k != "quasi-stationary"]
    if not decisive:
        return "quasi-stationary"
    return Counter(decisive).most_common(1)[0][0]


def _chain_tilt(base_dist: float, head_dist: float, base_delta_theta_e: float) -> str:
    """Geometry label for the rendered slant.

    The cold side is where θe is lower. ``delta_theta_e`` is θe(downroute) −
    θe(uproute), so cold air is downroute (larger distance) when it is negative.
    A front sloping back over the cold air with height (the norm) therefore has
    its upper/head node displaced toward the cold side. ``coldward`` = that norm,
    ``warmward`` = the anomalous opposite, ``upright`` = near-vertical.
    """
    disp = head_dist - base_dist
    if abs(disp) < _UPRIGHT_KM:
        return "upright"
    # Cold air is downroute (larger distance) when Δθe < 0, so the cold direction
    # in distance is +1 then, −1 otherwise.
    cold_dir = 1.0 if base_delta_theta_e < 0 else -1.0
    return "coldward" if (disp * cold_dir) > 0 else "warmward"


def _link_front_chains(
    analyses: list[RouteFrontAnalysisModel],
) -> list[FrontChainModel]:
    """Link crossings across 925/850/700 into sloping front chains; also stamp
    ``vertical_levels`` on every crossing (chain depth) for the marker/advisory.

    A genuine front slopes through several pressure levels, so the same air-mass
    boundary shows up at ~the same along-route distance on each level, displaced
    toward the cold air with height. We grow chains bottom→top: starting from the
    lowest level's crossings, each level up attaches to the best open chain whose
    head is one level below it, subject to physical gates —

      * **kind-compatible** (:func:`_kinds_compatible`) — cold↔cold / warm↔warm,
        quasi wildcards; a cold front never links to a warm front nearby;
      * **same Δθe sign** — both flying into colder air or both into warmer;
        it is the same boundary only if the air-mass contrast points the same way;
      * **within the slope budget** (:func:`_link_budget_km`) — bounded by
        textbook frontal slope, wider than ``merge_km`` and wider for warm fronts.

    Among candidates that pass, the one whose displacement is *coldward* (the
    physical norm) and smallest wins — a soft directional prior, not a hard gate,
    because the 3-level data is coarse and occlusions genuinely tilt the other way.

    Crossings that attach to nothing become single-level chains (``n_levels == 1``)
    — shallow / suspect, but kept so the count and the marker still reflect them.
    Replaces the old distance-only coherence cluster, which ignored kind / Δθe sign
    and reused the too-tight ``merge_km`` (under-linking warm fronts).
    """
    by_level: dict[int, list[FrontCrossingModel]] = {}
    for a in analyses:
        if a.crossings:
            by_level[a.level_hPa] = sorted(a.crossings, key=lambda c: c.distance_km)
    if not by_level:
        return []

    # Bottom→top: 925, 850, 700 (descending hPa = ascending altitude).
    levels = sorted(by_level, reverse=True)

    # A chain is the list of (level, crossing) nodes accumulated so far; its head
    # is the last (highest) node. ``open_chains`` may still grow upward.
    chains: list[list[tuple[int, FrontCrossingModel]]] = []
    open_chains: list[list[tuple[int, FrontCrossingModel]]] = []

    for li, level in enumerate(levels):
        crossings = by_level[level]
        if li == 0:
            for c in crossings:
                ch = [(level, c)]
                chains.append(ch)
                open_chains.append(ch)
            continue

        used_chains: set[int] = set()
        still_open: list[list[tuple[int, FrontCrossingModel]]] = []
        for c in crossings:
            best_idx: int | None = None
            best_score = float("inf")
            for idx, ch in enumerate(open_chains):
                if idx in used_chains:
                    continue
                h_level, head = ch[-1]
                if not _kinds_compatible(head.kind, c.kind):
                    continue
                # Same air-mass-contrast direction. A crossing with exactly
                # Δθe == 0 carries no direction, so don't let np.sign(0)==0
                # spuriously reject it against any signed neighbour — treat zero
                # as compatible and rely on the other gates.
                if (
                    head.delta_theta_e != 0.0 and c.delta_theta_e != 0.0
                    and np.sign(head.delta_theta_e) != np.sign(c.delta_theta_e)
                ):
                    continue
                disp = c.distance_km - head.distance_km
                budget = _link_budget_km(h_level, level, head.kind, c.kind)
                if abs(disp) > budget:
                    continue
                # Soft coldward prior: penalise anti-slope links, then prefer the
                # nearest. Cold air is downroute (larger distance) when Δθe < 0, so
                # the cold direction in distance is +1 then, −1 otherwise; a
                # coldward link (the physical norm) has disp in that direction.
                cold_dir = 1.0 if head.delta_theta_e < 0 else -1.0
                anti = disp * cold_dir < 0
                score = abs(disp) + (budget if anti else 0.0)
                if score < best_score:
                    best_score, best_idx = score, idx
            if best_idx is not None:
                open_chains[best_idx].append((level, c))
                used_chains.add(best_idx)
                still_open.append(open_chains[best_idx])
            else:
                ch = [(level, c)]
                chains.append(ch)
                still_open.append(ch)
        open_chains = still_open

    # Stamp vertical_levels (chain depth) back onto every crossing, then project.
    out: list[FrontChainModel] = []
    for ch in chains:
        n_levels = len({lvl for lvl, _ in ch})
        for _, c in ch:
            c.vertical_levels = n_levels
        ordered = sorted(ch, key=lambda lc: -lc[0])  # 925→850→700 (bottom→top)
        base_lvl, base_c = ordered[0]
        head_lvl, head_c = ordered[-1]
        out.append(FrontChainModel(
            nodes=[
                FrontChainNodeModel(
                    level_hPa=lvl, distance_km=c.distance_km, kind=c.kind,
                    intensity=c.intensity, gradient=c.gradient,
                    delta_theta_e=c.delta_theta_e, co_location=c.co_location,
                )
                for lvl, c in ordered
            ],
            kind=_consensus_kind([c.kind for _, c in ordered]),
            n_levels=n_levels,
            tilt=_chain_tilt(base_c.distance_km, head_c.distance_km,
                             base_c.delta_theta_e),
        ))
    return out


def _persistence(source, model, lat, lon, level_hpa, eta_hour, gradient_min):
    """Fraction of ±window frames where the gated gradient holds at the cell.

    A real front persists; an orographic/grid artifact flickers. ``None`` when
    no frames are samplable (e.g. single-timestep historical snapshot)."""
    avail = set(source.available_hours(model))
    if not avail:
        return None
    stride = getattr(source, "stride_hours", None) or 3
    li = int(np.argmin(np.abs(source.lat - lat)))
    lj = int(np.argmin(np.abs(source.lon - lon)))
    # Dedupe snapped hours: with stride > 3 h several offsets round to the same
    # frame (banker's rounding), which would triple-count that frame in tot.
    hours = sorted({int(round((eta_hour + dh) / stride) * stride) for dh in _PERSIST_OFFSETS_H} & avail)
    hits = tot = 0
    for h in hours:
        g = source.gradient_at_hour(model, h, level_hpa)
        if g is None:
            continue
        tot += 1
        v = g[li, lj]
        if np.isfinite(v) and v >= gradient_min:
            hits += 1
    return (hits / tot) if tot else None


def _analyze_one(
    source: SnapshotFieldSource,
    model: str,
    waypoints: list[tuple[float, float]],
    eta_hours: list[float],
    mid_hour: float,
    config: FrontGateConfig,
    analyses: list | None = None,
) -> RouteFrontAnalysis:
    """Detect fronts for one model/level.

    On-track crossings sample each densified route point at its **own ETA**
    (time advances along the route, so a moving front is crossed where/when the
    aircraft actually meets it — design §6.3 temporal axis). The off-track
    proximity scan needs one coherent grid, so it runs at the route-midpoint ETA.
    """
    dense = densify_route(waypoints, step_km=config.step_km)
    wp_cum = _cumulative_km(waypoints)
    dense_cum = [d[2] for d in dense]
    dense_hours = np.interp(dense_cum, wp_cum, eta_hours).tolist()

    samples = sample_hewson_at_route(
        source, model,
        [(la, lo) for la, lo, _ in dense],
        hours=dense_hours,
        level_hPa=config.level_hPa,
    )
    for s, (_, _, dist_km) in zip(samples, dense):
        s["distance_km"] = dist_km

    candidates = generate_front_candidates(
        samples, airmass_window_km=config.airmass_window_km,
    )
    # Reject orographic θe gradients on-track the same way the map / off-track
    # paths do: a persistent gradient pinned to terrain has a high time-mean
    # background (low anomaly) and/or sits on a masked cell. Needs ≥2 frames for
    # a meaningful background — with one frame the mean equals the instant and
    # every front would self-cancel, so skip the anomaly gate then (graceful).
    background = None
    if config.use_anomaly_filter and len(source.available_hours(model)) >= 2:
        background = source.background_gradient(model, config.level_hPa)
    decisions = apply_gate_config(
        candidates, config,
        background=background,
        terrain_mask=source.terrain_mask,
        lat_axis=source.lat,
        lon_axis=source.lon,
    )
    crossings = decisions_to_crossings(decisions, config.merge_km)

    # Attach relevance enrichment: each crossing is sampled at its own ETA
    # (consistent with the per-point time-march above).
    if crossings:
        enriched = []
        for c in crossings:
            eta_h = float(np.interp(c.distance_km, dense_cum, dense_hours))
            co_loc, weather_top = _colocate(analyses, model, c.distance_km, config.level_hPa)
            pers = _persistence(
                source, model, c.lat, c.lon, config.level_hPa, eta_h, config.gradient_min,
            )
            enriched.append(dataclasses.replace(
                c, co_location=co_loc, weather_top_ft=weather_top, persistence=pers,
            ))
        crossings = enriched

    nearest = find_nearby_fronts(source, model, waypoints, mid_hour, config=config)
    return RouteFrontAnalysis(
        model=model, hour=float(mid_hour), crossings=crossings, nearest=nearest,
        level_hPa=config.level_hPa, config=config, decisions=decisions,
    )


# ---------------------------------------------------------------------------
# Dataclass → Pydantic projection
# ---------------------------------------------------------------------------


def _finite_or_none(v: float | None) -> float | None:
    """NaN/±Inf → ``None``; finite values unchanged.

    The compute layer signals "not computable" with NaN (see ``_airmass_delta``
    and the margins in ``apply_gate_config``). Pydantic writes NaN as JSON
    ``null``, so anything non-finite must be projected onto an Optional field or
    the artifact cannot be re-validated on load — which previously aborted the
    whole advisory stage for the pack.
    """
    if v is None or not math.isfinite(v):
        return None
    return float(v)


def _to_analysis_model(a: RouteFrontAnalysis) -> RouteFrontAnalysisModel:
    return RouteFrontAnalysisModel(
        model=a.model,
        level_hPa=a.level_hPa,
        hour=a.hour,
        crossings=[
            FrontCrossingModel(
                lat=c.lat, lon=c.lon, distance_km=c.distance_km,
                gradient=c.gradient, neg_laplacian=c.neg_laplacian,
                advection=c.advection, tfp_before=c.tfp_before,
                tfp_after=c.tfp_after, delta_theta_e=c.delta_theta_e,
                kind=c.kind, intensity=c.intensity,
                co_location=c.co_location, weather_top_ft=c.weather_top_ft,
                persistence=c.persistence,
            )
            for c in a.crossings
        ],
        nearest=(
            FrontProximityModel(
                distance_km=a.nearest.distance_km, lat=a.nearest.lat,
                lon=a.nearest.lon, gradient=a.nearest.gradient,
                delta_theta_e=a.nearest.delta_theta_e, on_track=a.nearest.on_track,
                trend=a.nearest.trend, closing_km_per_h=a.nearest.closing_km_per_h,
            )
            if a.nearest is not None else None
        ),
        decisions=[
            FrontDecisionModel(
                lat=d.candidate.lat, lon=d.candidate.lon,
                distance_km=d.candidate.distance_km, gradient=d.candidate.gradient,
                delta_theta_e=_finite_or_none(d.candidate.delta_theta_e),
                advection=d.candidate.advection,
                accepted=d.accepted, rejected_by=d.rejected_by,
                kind=d.kind, intensity=d.intensity,
                margins={k: _finite_or_none(v) for k, v in d.margins.items()},
            )
            for d in a.decisions
        ],
    )


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def compute_route_fronts(
    waypoints: list[tuple[float, float]],
    etas: list[datetime],
    *,
    route_name: str,
    cruise_altitude_ft: int,
    advisory_models: Sequence[str] | None,
    gate_preset: str = "default",
    output_dir: Path | None = None,
    now: datetime | None = None,
    route_point_analyses: list | None = None,
) -> RouteFrontsManifest:
    """Build the :class:`RouteFrontsManifest` (no I/O). Shared by both surfaces.

    ``route_point_analyses`` (the per-model sounding column along the route) is
    optional; when present, crossings are enriched with cloud/convection
    co-location so the advisory and cross-section can gate on weather, not just
    the θe gradient.
    """
    from weatherbrief.frontal.gates import get_preset

    now = now or datetime.now(timezone.utc)
    models = [m for m in (advisory_models or list(FRONT_MODELS)) if m in FRONT_MODELS]
    # Stable order: ecmwf, gfs, icon.
    models = [m for m in ("ecmwf", "gfs", "icon") if m in models]

    # Elevation grid enables level-aware masking (a 925 hPa surface sits far below
    # 850/700 hPa); absent on pre-#216 caches → fall back to the flat mask.
    terrain_mask, terrain_elevation = _load_terrain_cache(output_dir)

    per_model: dict[str, list[RouteFrontAnalysisModel]] = {}
    front_chains: dict[str, list[FrontChainModel]] = {}
    per_model_primary: dict[str, int] = {}
    snapshot_inits: dict[str, str] = {}
    missing: list[str] = []
    fell_back: list[str] = []
    levels_seen: list[int] = []
    primary_level = 850
    base_config = get_preset(gate_preset)

    # The flight's valid-time window — pick each model's snapshot to bracket it
    # rather than always the newest init, so past/far-future flights sample at
    # real (non-negative, in-horizon) forecast hours (#201).
    win_start = min(etas) if etas else now
    win_end = max(etas) if etas else now

    for model in models:
        snap_path = snapshot_for_window(model, win_start, win_end, output_dir)
        if snap_path is None:
            # No retained snapshot brackets the window — fall back to the latest
            # init (timing may be off for past/far-future flights; noted below).
            snap_path = latest_snapshot(model, output_dir)
            if snap_path is not None:
                fell_back.append(model)
        if snap_path is None:
            missing.append(model)
            continue
        try:
            source = SnapshotFieldSource(snap_path, model_name=model)
        except Exception:
            logger.warning("Front detection: unreadable snapshot %s", snap_path,
                           exc_info=True)
            missing.append(model)
            continue
        # Terrain masking, only when the cache matches this snapshot's grid — a
        # stale mask/elevation (e.g. from a resolution change) would otherwise
        # crash fill_terrain() on a shape mismatch. Mismatch → no masking.
        grid_shape = (source.lat.size, source.lon.size)
        # Prefer level-aware masking (#216): with a matching elevation grid, a
        # per-level mask is derived inside the loop below. The flat mask is only
        # the fallback when no elevation is available (pre-#216 cache).
        model_elevation = (
            terrain_elevation
            if (terrain_elevation is not None and terrain_elevation.shape == grid_shape)
            else None
        )
        if model_elevation is None:
            if terrain_mask is not None and terrain_mask.shape == grid_shape:
                source.terrain_mask = terrain_mask
            elif terrain_mask is not None:
                logger.warning(
                    "Front detection: terrain mask %s mismatches %s grid %s — "
                    "skipping terrain masking",
                    terrain_mask.shape, model, grid_shape,
                )
        snapshot_inits[model] = _iso_z(source.init_time_unix)

        # ETAs → snapshot-relative forecast hours.
        eta_hours = [(e.timestamp() - source.init_time_unix) / 3600.0 for e in etas]
        wp_cum = _cumulative_km(waypoints)
        mid_hour = float(np.interp(wp_cum[-1] / 2.0, wp_cum, eta_hours))

        levels = [L for L in source.available_levels(model) if L in DEFAULT_LEVELS]
        if not levels:
            missing.append(model)
            continue
        # Accumulate across models — a partial snapshot could expose a subset,
        # and manifest.levels must describe everything actually in per_model.
        levels_seen = sorted(set(levels_seen) | set(levels))
        # Per-model nearest-cruise level — a partial snapshot may expose a
        # different level set per model, so the advisory must grade each model's
        # crossings against *its own* primary, not one manifest-wide value (#203).
        model_primary = nearest_cruise_level(cruise_altitude_ft, levels)

        analyses: list[RouteFrontAnalysisModel] = []
        for level in levels:
            cfg = base_config.with_overrides(level_hPa=level)
            # Mask terrain against *this level's* standard-atmosphere height, so a
            # 925 hPa surface masks far more terrain than 700 hPa — fixing both the
            # low-level Alpine artefact and the high-level over-masking (#216).
            if model_elevation is not None:
                source.terrain_mask = terrain_mask_for_level(model_elevation, level)
            try:
                result = _analyze_one(
                    source, model, waypoints, eta_hours, mid_hour, cfg,
                    analyses=route_point_analyses,
                )
            except Exception:
                logger.warning(
                    "Front detection failed for %s @ %d hPa", model, level,
                    exc_info=True,
                )
                continue
            analyses.append(_to_analysis_model(result))
        # Vertical linking: associate crossings across 925/850/700 into sloping
        # front chains (kind / Δθe-sign / slope-budget gated) and stamp each
        # crossing with its chain depth (vertical_levels) so the advisory /
        # cross-section can down-weight single-level (shallow) detections. The
        # chains drive the cross-section's slanted front lines. Per-model —
        # vertical slope is a within-model property.
        chains = _link_front_chains(analyses)
        if analyses:
            per_model[model] = analyses
            per_model_primary[model] = model_primary
            if chains:
                front_chains[model] = chains

    # Representative manifest-wide primary (display / back-compat): the modal
    # per-model value, falling back to the legacy default when nothing detected.
    if per_model_primary:
        primary_level = Counter(per_model_primary.values()).most_common(1)[0][0]

    notes: list[str] = []
    if missing:
        notes.append(
            "No precompute snapshot for: " + ", ".join(missing)
            + " — front data unavailable for those models."
        )
    if fell_back:
        notes.append(
            "No snapshot brackets the flight window for: " + ", ".join(fell_back)
            + " — used the latest available init (timing may be approximate for "
            "past or far-future flights)."
        )
    notes.append(
        "Free-atmosphere fronts only; 850 hPa θe does not see low IMC / fog. "
        "Positions/timing are qualitative (±50–100 km, ±1–2 h)."
    )

    return RouteFrontsManifest(
        schema_version=1,
        route_name=route_name,
        generated_at=now,
        primary_level_hPa=primary_level,
        per_model_primary_hPa=per_model_primary,
        levels=levels_seen,
        gate_config=base_config.with_overrides(level_hPa=primary_level).to_dict(),
        models=list(per_model.keys()),
        per_model=per_model,
        front_chains=front_chains,
        models_without_snapshot=missing,
        snapshot_inits=snapshot_inits,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Public surfaces
# ---------------------------------------------------------------------------


def run_fronts(
    route_point_analyses: Sequence,
    *,
    route_name: str,
    cruise_altitude_ft: int,
    advisory_models: Sequence[str] | None,
    gate_preset: str = "default",
    pack_dir: Path | None = None,
    output_dir: Path | None = None,
    out_name: str = "route_fronts.json",
) -> RouteFrontsManifest | None:
    """Detect fronts from in-memory route-point analyses; write the artifact.

    Returns ``None`` (and writes nothing) when the route lacks ≥2 ETA-stamped
    waypoints. ``output_dir`` overrides the snapshot root (defaults to
    ``${DATA_DIR}/hewson``); ``pack_dir`` is where the artifact lands.
    ``out_name`` defaults to ``route_fronts.json``; the alt-departure re-run
    passes ``route_fronts_alt.json`` so the alt grade reads fronts sampled at
    the alt ETAs rather than the primary briefing's.
    """
    extracted = _waypoints_with_eta(route_point_analyses)
    if extracted is None:
        logger.info("Front detection: route lacks ETA-stamped waypoints — skipping")
        return None
    waypoints, etas = extracted

    manifest = compute_route_fronts(
        waypoints, etas,
        route_name=route_name, cruise_altitude_ft=cruise_altitude_ft,
        advisory_models=advisory_models, gate_preset=gate_preset,
        output_dir=output_dir, route_point_analyses=route_point_analyses,
    )
    if pack_dir is not None:
        from weatherbrief.tasks.artifacts import save_front_artifacts

        save_front_artifacts(pack_dir, manifest, filename=out_name)
    logger.info(
        "Front detection: %d model(s) with snapshots, %d without; primary level %d hPa",
        len(manifest.models), len(manifest.models_without_snapshot),
        manifest.primary_level_hPa,
    )
    return manifest


def run_fronts_from_pack(
    pack_dir: Path,
    *,
    advisory_models: Sequence[str] | None = None,
    cruise_altitude_ft: int | None = None,
    gate_preset: str = "default",
    output_dir: Path | None = None,
) -> RouteFrontsManifest | None:
    """Recompute fronts from a pack's persisted ``route_analyses.json``.

    Lets the experimental preference be toggled on for an existing briefing
    without re-fetching — the snapshot already holds the grid. Returns ``None``
    if the route analyses are missing or lack ETA-stamped waypoints.
    """
    from weatherbrief.tasks.artifacts import load_route_analyses

    try:
        manifest_in = load_route_analyses(pack_dir)
    except FileNotFoundError:
        logger.info("Front detection (from pack): no route_analyses.json in %s", pack_dir)
        return None

    extracted = _waypoints_with_eta(manifest_in.analyses)
    if extracted is None:
        return None
    waypoints, etas = extracted

    cruise = (
        cruise_altitude_ft
        if cruise_altitude_ft is not None
        else manifest_in.cruise_altitude_ft
    )
    models = advisory_models if advisory_models is not None else manifest_in.models

    manifest = compute_route_fronts(
        waypoints, etas,
        route_name=manifest_in.route_name, cruise_altitude_ft=cruise,
        advisory_models=models, gate_preset=gate_preset, output_dir=output_dir,
        route_point_analyses=manifest_in.analyses,
    )
    from weatherbrief.tasks.artifacts import save_front_artifacts

    save_front_artifacts(pack_dir, manifest)
    return manifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_z(unix_seconds: int) -> str:
    dt = datetime.fromtimestamp(unix_seconds, tz=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _load_terrain_cache(
    output_dir: Path | None,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Load the precompute's cached terrain ``(mask, elevation)`` in a single open.

    One ``np.load`` avoids doubled I/O and closes a TOCTOU window where a
    concurrent precompute could swap the file between two reads, yielding a mask
    and elevation from different cache versions. ``elevation`` is ``None`` on a
    pre-#216 cache (callers fall back to the flat mask); ``(None, None)`` when the
    cache is absent or unreadable."""
    path = resolve_output_dir(output_dir) / "terrain_mask.npz"
    if not path.exists():
        return None, None
    try:
        with np.load(path) as npz:
            mask = npz["mask"]
            elevation = npz["elevation"] if "elevation" in npz.files else None
            return mask, elevation
    except Exception:
        logger.warning("Front detection: unreadable terrain cache %s", path)
        return None, None
