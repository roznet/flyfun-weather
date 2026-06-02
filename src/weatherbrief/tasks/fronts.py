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
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np

from weatherbrief.frontal.gates import FrontGateConfig
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
)
from weatherbrief.models import (
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


def _attr(obj, name, default=None):
    """getattr/dict-get that tolerates pydantic objects or raw dicts (or None)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _enum_val(x):
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
    cloud_top = max((_attr(cl, "top_ft", None) or 0.0 for cl in layers if _spans(cl, level_hpa)), default=0.0)
    conv_top = None
    if conv is not None:
        conv_top = _attr(conv, "top_ft", None) or _attr(conv, "el_altitude_ft", None)
    tops = [v for v in (cloud_top or None, conv_top) if v]
    weather_top_ft = float(max(tops)) if tops else None

    precip_obj = _attr(s, "precipitation", None)
    precip = precip_obj is not None and _enum_val(_attr(precip_obj, "surface_intensity", "none")) != "none"

    # Cloud must span the frontal level to count — a high cirrus deck unrelated
    # to a low front is neither "wet" nor "partly" (it would otherwise false-AMBER).
    if risk in _CONVECTIVE_RISK:
        category = "convective"
    elif _covers(level_hpa, _SIGNIFICANT_COVERAGE) or precip:
        category = "wet"
    elif _covers(level_hpa, _PARTLY_COVERAGE):
        category = "partly"
    else:
        category = "dry"
    return category, weather_top_ft


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
    analyses=None,
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
    decisions = apply_gate_config(candidates, config)
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
                delta_theta_e=d.candidate.delta_theta_e,
                advection=d.candidate.advection,
                accepted=d.accepted, rejected_by=d.rejected_by,
                kind=d.kind, intensity=d.intensity, margins=d.margins,
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
    route_point_analyses=None,
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

    terrain_mask = _load_cached_terrain_mask(output_dir)

    per_model: dict[str, list[RouteFrontAnalysisModel]] = {}
    snapshot_inits: dict[str, str] = {}
    missing: list[str] = []
    levels_seen: list[int] = []
    primary_level = 850
    base_config = get_preset(gate_preset)

    for model in models:
        snap_path = latest_snapshot(model, output_dir)
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
        # Only apply the cached terrain mask if it matches this snapshot's grid
        # — a stale mask (e.g. from a resolution change) would otherwise crash
        # fill_terrain() on a shape mismatch. Mismatch → no masking (graceful).
        if (
            terrain_mask is not None
            and terrain_mask.shape == (source.lat.size, source.lon.size)
        ):
            source.terrain_mask = terrain_mask
        elif terrain_mask is not None:
            logger.warning(
                "Front detection: terrain mask %s mismatches %s grid %s — "
                "skipping terrain masking",
                terrain_mask.shape, model, (source.lat.size, source.lon.size),
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
        primary_level = nearest_cruise_level(cruise_altitude_ft, levels)

        analyses: list[RouteFrontAnalysisModel] = []
        for level in levels:
            cfg = base_config.with_overrides(level_hPa=level)
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
        if analyses:
            per_model[model] = analyses

    notes: list[str] = []
    if missing:
        notes.append(
            "No precompute snapshot for: " + ", ".join(missing)
            + " — front data unavailable for those models."
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
        levels=levels_seen,
        gate_config=base_config.with_overrides(level_hPa=primary_level).to_dict(),
        models=list(per_model.keys()),
        per_model=per_model,
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


def _load_cached_terrain_mask(output_dir: Path | None) -> np.ndarray | None:
    """Load the precompute's cached terrain mask, or ``None`` if absent."""
    path = resolve_output_dir(output_dir) / "terrain_mask.npz"
    if not path.exists():
        return None
    try:
        with np.load(path) as npz:
            return npz["mask"]
    except Exception:
        logger.warning("Front detection: unreadable terrain mask %s", path)
        return None
