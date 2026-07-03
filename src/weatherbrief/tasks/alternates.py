"""Weather-based alternate airports (issue #210).

When a destination forecast is marginal, surface the closest airports a pilot
could divert to that *fix the specific problem* (flight category, wind speed,
best-runway crosswind), classify each as reachable **before** or **after** the
destination along the route, and rank them closest-first.

Consistency is the point of this module: each alternate's assessment is
computed by the SAME shared code path as the pan-European forecast map
(``analysis.airport_consensus``) fed from the SAME per-model fetchers the
standalone verification cycle uses (GFS/ICON via Open-Meteo + GRIB ceiling,
ECMWF via local GRIB for visibility). So the same airport reads the same
category / crosswind / consensus in both views.

See ``designs/future/alternates.md`` for the full design and rationale.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from weatherbrief.analysis.airport_consensus import consensus, enrich_wind, snap_to_dict
from weatherbrief.models.alternates import (
    AlternateAirport,
    AlternateAxisPick,
    RouteAlternates,
)
from weatherbrief.models.analysis import RouteConfig
from weatherbrief.tasks.airport_watchlist import WatchlistAirport

logger = logging.getLogger(__name__)

# Candidate-selection geometry defaults (NM).
ALT_CORRIDOR_NM = 40.0  # near-route corridor half-width
ALT_DEST_RADIUS_NM = 50.0  # great-circle radius around the destination
ALT_MAX_CANDIDATES = 30  # absolute cap on fetched candidates (the N×3 lite-sounding cost)
ALT_BATCH_SIZE = 8  # fetch candidates in nearest-first batches of this size (#271 follow-up)
ALT_POSITION_MARGIN_NM = 10.0  # along-track slack before calling an airport "before"
ALT_DEFAULT_MIN_RUNWAY_FT = 2000  # conservative default when no aircraft profile is known

# Flight category severity: higher index = worse conditions.
_CATEGORY_ORDER = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}
# Destination at/worse than MVFR triggers the instrument-approach requirement.
_REQUIRE_APPROACH_FROM = _CATEGORY_ORDER["MVFR"]

# Per-model split mirrors the forecast map exactly (STANDALONE_MODELS).
_MODELS = ["gfs", "icon", "ecmwf"]


def _cat_idx(category: str | None) -> int:
    """Severity index for a flight category (unknown → best/VFR)."""
    if category is None:
        return 0
    return _CATEGORY_ORDER.get(category.upper(), 0)


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in NM via euro_aip's NavPoint (single source of truth)."""
    from euro_aip.models.navpoint import NavPoint

    _, dist_nm = NavPoint(latitude=lat1, longitude=lon1).haversine_distance(
        NavPoint(latitude=lat2, longitude=lon2)
    )
    return dist_nm


def _eta_hour(target_time: datetime, duration_hours: float) -> datetime:
    """Destination ETA rounded to the nearest whole UTC hour (the fetch sample hour)."""
    eta = target_time
    if eta.tzinfo is None:
        eta = eta.replace(tzinfo=timezone.utc)
    eta = eta.astimezone(timezone.utc) + timedelta(hours=max(0.0, duration_hours))
    # Round to nearest hour so we hit an integer sample hour the fetchers emit.
    rounded = (eta + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
    return rounded


def _same_hour(a: datetime, b: datetime) -> bool:
    """True when two aware datetimes fall on the same UTC calendar hour."""
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    a = a.astimezone(timezone.utc)
    b = b.astimezone(timezone.utc)
    return (a.year, a.month, a.day, a.hour) == (b.year, b.month, b.day, b.hour)


def _fetch_eta_snapshots(
    airports: list[WatchlistAirport],
    eta_dt: datetime,
) -> dict[str, dict[str, dict]]:
    """Fetch per-model snapshot dicts for ``airports`` at the ETA hour.

    Returns ``{icao: {model: snapshot_dict}}`` keeping only the snapshot whose
    ``forecast_hour`` matches ``eta_dt``. Per-model split mirrors the forecast
    map: GFS/ICON via Open-Meteo + GRIB ceiling, ECMWF via local GRIB (the only
    source of ECMWF visibility), with an Open-Meteo fallback when no local GRIB
    run is available (degraded — ECMWF visibility absent — but consensus still
    works). Each model's failure is non-fatal.

    The three model passes run **concurrently** (issue #271): each pass is
    independent (it writes only its own ``model`` key per icao), so the stage
    cost collapses toward the *slowest single model* instead of the sum of all
    three. The heavy GRIB decode already runs on the shared ``ProcessPoolExecutor``
    via ``_dispatch_decode`` (throttled by ``GRIB_DECODE_WORKERS``), so the
    threads here only overlap the Open-Meteo network waits and dispatch
    submission — no new GIL contention, no extra peak decode concurrency.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from contextvars import copy_context

    from weatherbrief.fetch.model_status import fetch_model_metadata
    from weatherbrief.tasks.standalone_verification import (
        MODEL_FORECAST_DAYS,
        _enrich_with_grib,
        _fetch_forecasts_for_model,
        _select_ecmwf_grib_run,
        fetch_ecmwf_grib_snapshots,
    )

    sample_hours = [eta_dt.hour]
    by_icao: dict[str, dict[str, dict]] = {}
    # Single up-front metadata call — kept OUTSIDE the parallel region.
    metadata = fetch_model_metadata(_MODELS)

    def _fetch_one_model(model: str) -> dict[str, dict]:
        """Fetch one model's ETA-hour snapshots → ``{icao: snap}``.

        Runs in its own worker thread with its **own** ``requests.Session``
        (``requests.Session`` is not thread-safe, so it must not be shared
        across model passes). Decode priority is inherited from the caller's
        ContextVar (propagated via ``copy_context`` at submit time) — the
        interactive briefing path resolves to INTERACTIVE.
        """
        import requests

        meta = metadata.get(model)
        if meta is None:
            logger.warning("Alternates: no model metadata for %s, skipping", model)
            return {}
        om_init_time = datetime.fromtimestamp(meta.last_init_time, tz=timezone.utc)
        days = MODEL_FORECAST_DAYS.get(model, 4)
        session = requests.Session()

        snaps: list[dict] = []
        if model == "ecmwf":
            run_files = _select_ecmwf_grib_run(om_init_time, days)
            if run_files is not None:
                snaps = fetch_ecmwf_grib_snapshots(
                    run_files, airports, sample_hours, days,
                )
            else:
                logger.info(
                    "Alternates: no local ECMWF GRIB run; falling back to "
                    "Open-Meteo (no ECMWF visibility)"
                )
                snaps, _ = _fetch_forecasts_for_model(
                    model, om_init_time, airports, session, sample_hours,
                )
                _enrich_with_grib(snaps, model, om_init_time, airports, session)
        else:
            snaps, _ = _fetch_forecasts_for_model(
                model, om_init_time, airports, session, sample_hours,
            )
            _enrich_with_grib(snaps, model, om_init_time, airports, session)

        result: dict[str, dict] = {}
        for snap in snaps:
            if _same_hour(snap["forecast_hour"], eta_dt):
                result[snap["icao"]] = snap
        return result

    # Fan out the 3 model passes. ``copy_context().run`` propagates this
    # thread's ContextVars (notably the decode priority set by the interactive
    # briefing) into each worker — a bare ThreadPoolExecutor worker would start
    # from default context and silently drop alternates decode to SCHEDULED.
    with ThreadPoolExecutor(max_workers=len(_MODELS)) as executor:
        futures = {
            executor.submit(copy_context().run, _fetch_one_model, model): model
            for model in _MODELS
        }
        for future in as_completed(futures):
            model = futures[future]
            try:
                model_snaps = future.result()
            except Exception:
                # A single model's failure stays non-fatal (matches the prior
                # per-model behaviour) — consensus still works on the rest.
                logger.warning("Alternates: %s fetch failed", model, exc_info=True)
                continue
            for icao, snap in model_snaps.items():
                by_icao.setdefault(icao, {})[model] = snap

    return by_icao


def _assess(
    icao: str,
    by_icao: dict[str, dict[str, dict]],
    runways: dict,
    aggregation: str = "majority",
) -> tuple[dict[str, dict], dict] | None:
    """Build (per_model, consensus) for one airport via the shared assembly.

    ``aggregation`` ("majority"/"worst") is the user's aggregation preference,
    passed straight to :func:`consensus` so the alternate card and the airport
    arrival card reduce the same per-model data the same way.

    Returns None when no model produced a snapshot for the airport.
    """
    models_raw = by_icao.get(icao)
    if not models_raw:
        return None
    per_model = {m: snap_to_dict(s) for m, s in models_raw.items()}
    rwy_ends = runways.get(icao, [])
    for d in per_model.values():
        enrich_wind(d, rwy_ends)
    return per_model, consensus(per_model, mode=aggregation)


@dataclass(frozen=True)
class _DestContext:
    """Destination-derived values needed to build each candidate's vs-dest deltas.

    Assembled once from the destination assessment so the per-candidate builder
    (which runs once per batch) stays a pure function of the candidate.
    """

    enroute_nm: float  # destination along-track distance (for before/after)
    gc_from_origin_nm: float  # great-circle origin→destination (for detour_early)
    cat_idx: int  # destination flight-category severity index
    wind_kt: float | None
    crosswind_kt: float | None


def _build_alternate(
    c: dict,
    by_icao: dict[str, dict[str, dict]],
    runways: dict,
    origin,
    dest_ctx: _DestContext,
    aggregation: str = "majority",
) -> AlternateAirport | None:
    """Assemble one ``AlternateAirport`` from a candidate's fetched snapshots.

    Returns ``None`` when the candidate has no model snapshot at the ETA hour, or
    when its assessment raises (a single malformed airport must skip only itself,
    not abort the stage). Extracted so the batched fetch loop can build each
    batch's candidates without duplicating this logic.
    """
    ap = c["airport"]
    try:
        assessed = _assess(ap.ident, by_icao, runways, aggregation)
    except Exception:
        logger.debug("Alternates: assessment failed for %s", ap.ident, exc_info=True)
        return None
    if assessed is None:
        return None
    per_model, cons = assessed

    # Suitability: instrument approach + best precision tier (minima proxy).
    has_iap = False
    best_approach_type = None
    try:
        approaches = ap.procedures_query.approaches()
        has_iap = approaches.exists()
        if has_iap:
            best = approaches.most_precise()
            best_approach_type = best.approach_type if best is not None else None
    except Exception:
        logger.debug("Alternates: approach query failed for %s", ap.ident, exc_info=True)

    # Geometry: before/after + detour pair.
    enroute = c.get("enroute_distance_nm")
    if enroute is not None and enroute < dest_ctx.enroute_nm - ALT_POSITION_MARGIN_NM:
        position = "before"
    else:
        position = "after"
    dist_from_dest = c["distance_from_dest_nm"]
    gc_dep_alt = _haversine_nm(origin.lat, origin.lon, ap.latitude_deg, ap.longitude_deg)
    detour_early_nm = round(gc_dep_alt - dest_ctx.gc_from_origin_nm, 1)
    detour_late_nm = round(dist_from_dest, 1)

    # Assessment vs destination, per axis.
    alt_cat = cons["flight_category"]
    alt_idx = _cat_idx(alt_cat)
    alt_wind = cons.get("wind_speed_kt")
    alt_xw = cons.get("crosswind_kt")

    better_category = alt_idx < dest_ctx.cat_idx
    better_wind = (
        alt_wind is not None and dest_ctx.wind_kt is not None and alt_wind < dest_ctx.wind_kt
    )
    better_crosswind = (
        alt_xw is not None and dest_ctx.crosswind_kt is not None and alt_xw < dest_ctx.crosswind_kt
    )
    not_worse_cat = alt_idx <= dest_ctx.cat_idx
    not_worse_wind = alt_wind is None or dest_ctx.wind_kt is None or alt_wind <= dest_ctx.wind_kt
    not_worse_xw = (
        alt_xw is None or dest_ctx.crosswind_kt is None or alt_xw <= dest_ctx.crosswind_kt
    )
    dominates = (
        not_worse_cat
        and not_worse_wind
        and not_worse_xw
        and (better_category or better_wind or better_crosswind)
    )

    return AlternateAirport(
        icao=ap.ident,
        name=ap.name,
        lat=ap.latitude_deg,
        lon=ap.longitude_deg,
        distance_from_dest_nm=round(dist_from_dest, 1),
        enroute_distance_nm=enroute,
        segment_distance_nm=c.get("segment_distance_nm"),
        position=position,
        detour_early_nm=detour_early_nm,
        detour_late_nm=detour_late_nm,
        flight_category=alt_cat,
        wind_speed_kt=cons.get("wind_speed_kt"),
        crosswind_kt=cons.get("crosswind_kt"),
        headwind_kt=cons.get("headwind_kt"),
        best_runway_id=next(
            (d.get("best_runway_id") for d in per_model.values() if d.get("best_runway_id")),
            None,
        ),
        ceiling_ft=cons.get("ceiling_ft"),
        visibility_m=cons.get("visibility_m"),
        agreement=cons.get("agreement", {}),
        per_model=per_model,
        has_instrument_approach=has_iap,
        best_approach_type=best_approach_type,
        longest_runway_ft=ap.longest_runway_length_ft,
        has_hard_runway=bool(ap.has_hard_runway),
        point_of_entry=bool(ap.point_of_entry),
        is_major=ap.type == "large_airport",
        better_category=better_category,
        better_wind=better_wind,
        better_crosswind=better_crosswind,
        dominates_destination=dominates,
    )


def _qualifies_both_regimes(cand: AlternateAirport) -> bool:
    """True when a candidate is a "likely" alternate under BOTH FAA and EASA.

    Computed from the candidate's NWP-consensus ceiling/visibility — the same
    inputs the alternate-requirement post-step falls back to when no TAF covers
    the ETA (TAFs only exist at D-0; the alternates stage runs D-2 inward). Used
    only to decide whether the batched fetch can stop early; the authoritative,
    TAF-aware qualification is still written later by the post-step.
    """
    from weatherbrief.analysis.alternate_requirement import (
        compute_easa_qual,
        compute_faa_qual,
    )
    from weatherbrief.models.alternate_requirement import BandVerdict

    faa = compute_faa_qual(
        cand.ceiling_ft, cand.visibility_m,
        cand.best_approach_type, cand.has_instrument_approach,
    )
    easa = compute_easa_qual(
        cand.ceiling_ft, cand.visibility_m,
        cand.best_approach_type, cand.has_instrument_approach,
    )
    return faa.verdict == BandVerdict.LIKELY and easa.verdict == BandVerdict.LIKELY


def run_alternates(
    route: RouteConfig,
    target_time: datetime,
    airports_db_path: str,
    *,
    corridor_nm: float = ALT_CORRIDOR_NM,
    radius_nm: float = ALT_DEST_RADIUS_NM,
    max_candidates: int = ALT_MAX_CANDIDATES,
    require_hard_runway: bool = True,
    min_runway_ft: int | None = ALT_DEFAULT_MIN_RUNWAY_FT,
    aggregation: str = "majority",
    now: datetime | None = None,
) -> RouteAlternates | None:
    """Compute weather-based divert candidates for a route's destination.

    Args:
        route: Flight route (destination = last waypoint).
        target_time: Flight departure time (aware UTC); ETA = departure + duration.
        airports_db_path: euro_aip SQLite database path.
        corridor_nm: Near-route corridor half-width for candidate geometry.
        radius_nm: Great-circle radius around the destination for laterally-offset fields.
        max_candidates: Cap on fetched candidates (nearest-to-destination first).
        require_hard_runway: Drop airports without a hard runway.
        min_runway_ft: Drop airports whose longest runway is shorter (when known).
        aggregation: Model-consensus mode ("majority"/"worst"), the user's
            advisory aggregation preference. Passed to ``consensus`` so the
            alternate card and the airport arrival card agree on the same
            airport's category / ceiling / crosswind.
        now: Override for "now" (testing); defaults to current UTC time.

    Returns:
        A populated ``RouteAlternates``, or ``None`` if the destination
        assessment could not be computed (caller degrades gracefully).
    """
    from weatherbrief.airports import _load_airport_model, get_runway_ends
    from weatherbrief.analysis.route_geometry import compute_route_distances

    now = now or datetime.now(timezone.utc)
    eta_dt = _eta_hour(target_time, route.flight_duration_hours)

    model = _load_airport_model(airports_db_path)
    route_icaos = [wp.icao for wp in route.waypoints]
    dest = route.destination
    origin = route.origin

    route_distances = compute_route_distances(route)
    dest_enroute_nm = route_distances[-1] if route_distances else 0.0
    gc_dep_dest = _haversine_nm(origin.lat, origin.lon, dest.lat, dest.lon)

    # --- 1. Candidate geometry: near-route corridor ∪ destination radius ---
    candidates: dict[str, dict] = {}
    try:
        near = model.find_airports_near_route(route_icaos, distance_nm=corridor_nm)
    except Exception:
        logger.warning("Alternates: find_airports_near_route failed", exc_info=True)
        near = []
    for item in near:
        ap = item.get("airport")
        if ap is None or ap.ident is None:
            continue
        candidates[ap.ident] = {
            "airport": ap,
            "enroute_distance_nm": item.get("enroute_distance_nm"),
            "segment_distance_nm": item.get("segment_distance_nm"),
        }

    # euro_aip loads the whole airport set into memory (no spatial index), so
    # the radius query is a scan. Cheap bounding-box pre-filter first — skip the
    # haversine for airports that can't possibly be within radius_nm. 1° lat ≈
    # 60 NM; longitude degrees shrink by cos(lat) toward the poles.
    lat_margin = radius_nm / 60.0
    # Use cos at the worst-case (highest-|lat|) edge of the box so the longitude
    # margin is never too tight — the box must not exclude a true in-radius
    # airport; the haversine below still does the exact gating.
    cos_lat = max(0.01, math.cos(math.radians(min(89.0, abs(dest.lat) + lat_margin))))
    lon_margin = radius_nm / (60.0 * cos_lat)
    try:
        all_airports = model.airports.all()
    except Exception:
        all_airports = []
    for ap in all_airports:
        if ap.ident in candidates:
            continue
        if ap.latitude_deg is None or ap.longitude_deg is None:
            continue
        if abs(ap.latitude_deg - dest.lat) > lat_margin:
            continue
        if abs(ap.longitude_deg - dest.lon) > lon_margin:
            continue
        d = _haversine_nm(dest.lat, dest.lon, ap.latitude_deg, ap.longitude_deg)
        if d <= radius_nm:
            candidates[ap.ident] = {
                "airport": ap,
                "enroute_distance_nm": None,
                "segment_distance_nm": None,
                "distance_from_dest_nm": d,  # already computed — reused below
            }

    # --- 2. Drop the destination and departure themselves ---
    candidates.pop(dest.icao, None)
    candidates.pop(origin.icao, None)

    # --- 3. Runway suitability ---
    # NB: large_airport / scheduled_service are NOT dropped here — they are
    # returned and flagged (is_major, below) so the UI can hide them by default
    # while letting the pilot reveal them. Only genuine reachability/safety
    # filters (hard runway, min length, the sub-VFR-no-approach gate) stay
    # server-side.
    filtered: list[dict] = []
    for c in candidates.values():
        ap = c["airport"]
        if require_hard_runway and not ap.has_hard_runway:
            continue
        if (
            min_runway_ft is not None
            and ap.longest_runway_length_ft is not None
            and ap.longest_runway_length_ft < min_runway_ft
        ):
            continue
        if ap.latitude_deg is None or ap.longitude_deg is None:
            continue
        # Radius candidates already carry distance_from_dest_nm; near-route ones
        # don't, so compute it here only when absent.
        if c.get("distance_from_dest_nm") is None:
            c["distance_from_dest_nm"] = _haversine_nm(
                dest.lat, dest.lon, ap.latitude_deg, ap.longitude_deg,
            )
        filtered.append(c)

    # --- 4. Cap to the nearest N by destination distance (log the cap) ---
    filtered.sort(key=lambda c: c["distance_from_dest_nm"])
    capped = filtered[:max_candidates]
    if len(filtered) > max_candidates:
        logger.info(
            "Alternates: capped candidates %d → %d (nearest-to-destination)",
            len(filtered), max_candidates,
        )

    # --- 5. Batched fetch (nearest-to-dest first), stop once a non-major
    #        candidate qualifies under BOTH FAA and EASA ---------------------
    # Fetching every candidate's lite sounding across 3 models is the dominant
    # cost (ALT_MAX_CANDIDATES exists for it). Most marginal-destination cases
    # are resolved by a field very close to the destination, so we fetch in
    # nearest-first batches of ALT_BATCH_SIZE and stop as soon as one *non-major*
    # candidate is a "likely" alternate under FAA *and* EASA. Major fields may
    # appear (and are flagged) but never satisfy the stop condition — the pilot
    # needs a usable non-major divert. When nothing qualifies we exhaust the cap
    # and return the full set, exactly as before.
    dest_wa = WatchlistAirport(icao=dest.icao, lat=dest.lat, lon=dest.lon)
    by_icao: dict[str, dict[str, dict]] = {}
    runways: dict = {}
    alternates: list[AlternateAirport] = []
    dest_ctx: _DestContext | None = None
    dest_category = None
    dest_crosswind = None
    dest_ceiling_ft = None
    dest_visibility_m = None
    require_approach = False
    evaluated_count = 0

    # ``or [0]`` guarantees one iteration even with no candidates, so the
    # destination itself is still fetched + assessed (it gates the whole stage).
    batch_starts = list(range(0, len(capped), ALT_BATCH_SIZE)) or [0]
    for batch_idx, batch_start in enumerate(batch_starts):
        batch = capped[batch_start:batch_start + ALT_BATCH_SIZE]

        # Bundle the destination into the first batch's fetch (one round trip).
        fetch_airports: list[WatchlistAirport] = [dest_wa] if batch_idx == 0 else []
        for c in batch:
            ap = c["airport"]
            fetch_airports.append(
                WatchlistAirport(icao=ap.ident, lat=ap.latitude_deg, lon=ap.longitude_deg)
            )

        by_icao.update(_fetch_eta_snapshots(fetch_airports, eta_dt))
        try:
            runways.update(get_runway_ends([a.icao for a in fetch_airports], airports_db_path))
        except Exception:
            logger.warning("Alternates: runway lookup failed", exc_info=True)

        # Destination assessment (drives axes + the instrument-approach gate).
        if batch_idx == 0:
            dest_assessed = _assess(dest.icao, by_icao, runways, aggregation)
            if dest_assessed is None:
                logger.info("Alternates: no destination snapshot at ETA; skipping stage")
                return None
            _, dest_cons = dest_assessed
            dest_category = dest_cons["flight_category"]
            dest_crosswind = dest_cons.get("crosswind_kt")
            # Destination NWP-consensus ceiling/vis at ETA (reduced under the
            # user's aggregation mode — median-of-winning-pool in majority mode,
            # worst-across-models in worst mode) — the regulatory-trigger NWP
            # fallback (#249) when no dest TAF exists.
            dest_ceiling_ft = dest_cons.get("ceiling_ft")
            dest_visibility_m = dest_cons.get("visibility_m")
            dest_ctx = _DestContext(
                enroute_nm=dest_enroute_nm,
                gc_from_origin_nm=gc_dep_dest,
                cat_idx=_cat_idx(dest_category),
                wind_kt=dest_cons.get("wind_speed_kt"),
                crosswind_kt=dest_crosswind,
            )
            # Informational only: is the destination itself MVFR/IFR/LIFR (i.e.
            # you'd need an instrument approach to get into the *destination*).
            # The candidate approach gate below is per-candidate, not keyed here.
            require_approach = dest_ctx.cat_idx >= _REQUIRE_APPROACH_FROM

        # ``dest_ctx`` is always set here: the first batch either assigns it or
        # returns early above, and every later batch follows that first one.
        assert dest_ctx is not None
        # Build this batch's alternates (a single malformed airport skips only
        # itself, not the stage — _build_alternate returns None for it).
        for c in batch:
            alt = _build_alternate(c, by_icao, runways, origin, dest_ctx, aggregation)
            if alt is not None:
                alternates.append(alt)
        evaluated_count += len(batch)

        # Stop as soon as a non-major candidate qualifies under both regimes.
        if any(not a.is_major and _qualifies_both_regimes(a) for a in alternates):
            logger.info(
                "Alternates: qualifying non-major alternate after %d candidate(s) "
                "of %d capped; skipping the rest",
                evaluated_count, len(capped),
            )
            break

    # Per-candidate instrument-approach gate (always applied, by the
    # candidate's *own* weather — not the destination's). A field that is
    # itself MVFR/IFR/LIFR with no published approach can't be reached in those
    # conditions, so drop it. A field forecast VFR is always kept: you divert
    # there visually, no approach needed. Graceful degradation: if procedure
    # data is absent entirely (no candidate has any approach) yet there are
    # sub-VFR fields the gate *would* drop, going dark on missing reference
    # data is worse than showing them flagged — so relax + flag instead.
    def _needs_approach(a: AlternateAirport) -> bool:
        return _cat_idx(a.flight_category) >= _REQUIRE_APPROACH_FROM

    gated_out = [a for a in alternates if _needs_approach(a) and not a.has_instrument_approach]
    any_approach_data = any(a.has_instrument_approach for a in alternates)
    approach_filter_relaxed = False
    if gated_out and not any_approach_data:
        approach_filter_relaxed = True
        logger.info(
            "Alternates: %d sub-VFR candidate(s) lack an approach but no procedure "
            "data is present (likely absent from the airport DB) — showing unfiltered, flagged",
            len(gated_out),
        )
    else:
        alternates = [
            a for a in alternates
            if a.has_instrument_approach or not _needs_approach(a)
        ]

    # Rank closest-first.
    alternates.sort(key=lambda a: a.distance_from_dest_nm)

    # Nearest-improving alternate per deficient axis.
    def _nearest(pred) -> AlternateAirport | None:
        matching = [a for a in alternates if pred(a)]
        if not matching:
            return None
        return min(matching, key=lambda a: a.distance_from_dest_nm)

    nearest_improving: list[AlternateAxisPick] = []
    for axis, pred in (
        ("category", lambda a: a.better_category),
        ("wind", lambda a: a.better_wind),
        ("crosswind", lambda a: a.better_crosswind),
    ):
        pick = _nearest(pred)
        nearest_improving.append(AlternateAxisPick(
            axis=axis,
            icao=pick.icao if pick else None,
            distance_from_dest_nm=pick.distance_from_dest_nm if pick else None,
            position=pick.position if pick else None,
        ))

    return RouteAlternates(
        destination_icao=dest.icao,
        destination_category=dest_category,
        destination_crosswind_kt=dest_crosswind,
        destination_ceiling_ft=dest_ceiling_ft,
        destination_visibility_m=dest_visibility_m,
        eta=eta_dt,
        corridor_nm=corridor_nm,
        radius_nm=radius_nm,
        require_approach=require_approach,
        approach_filter_relaxed=approach_filter_relaxed,
        candidates_evaluated=evaluated_count,
        alternates=alternates,
        nearest_improving=nearest_improving,
        computed_at=now,
    )
