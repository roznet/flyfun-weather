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
ALT_MAX_CANDIDATES = 30  # cap fetched candidates (the N×3 lite-sounding cost)
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


def _is_scheduled_service(value) -> bool:
    """True when an airport advertises scheduled commercial service."""
    return str(value).strip().lower() == "yes"


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
    """
    import requests

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
    session = requests.Session()
    metadata = fetch_model_metadata(_MODELS)

    for model in _MODELS:
        meta = metadata.get(model)
        if meta is None:
            logger.warning("Alternates: no model metadata for %s, skipping", model)
            continue
        om_init_time = datetime.fromtimestamp(meta.last_init_time, tz=timezone.utc)
        days = MODEL_FORECAST_DAYS.get(model, 4)

        snaps: list[dict] = []
        try:
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
        except Exception:
            logger.warning("Alternates: %s fetch failed", model, exc_info=True)
            continue

        for snap in snaps:
            if _same_hour(snap["forecast_hour"], eta_dt):
                by_icao.setdefault(snap["icao"], {})[model] = snap

    return by_icao


def _assess(
    icao: str,
    by_icao: dict[str, dict[str, dict]],
    runways: dict,
) -> tuple[dict[str, dict], dict] | None:
    """Build (per_model, consensus) for one airport via the shared assembly.

    Returns None when no model produced a snapshot for the airport.
    """
    models_raw = by_icao.get(icao)
    if not models_raw:
        return None
    per_model = {m: snap_to_dict(s) for m, s in models_raw.items()}
    rwy_ends = runways.get(icao, [])
    for d in per_model.values():
        enrich_wind(d, rwy_ends)
    return per_model, consensus(per_model, mode="worst")


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
        now: Override for "now" (testing); defaults to current UTC time.

    Returns:
        A populated ``RouteAlternates``, or ``None`` if the destination
        assessment could not be computed (caller degrades gracefully).
    """
    from weatherbrief.airports import _load_airport_model, get_runway_ends
    from weatherbrief.tasks.route_weather import _compute_route_distances

    now = now or datetime.now(timezone.utc)
    eta_dt = _eta_hour(target_time, route.flight_duration_hours)

    model = _load_airport_model(airports_db_path)
    route_icaos = [wp.icao for wp in route.waypoints]
    dest = route.destination
    origin = route.origin

    route_distances = _compute_route_distances(route)
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
            }

    # --- 2. Drop the destination and departure themselves ---
    candidates.pop(dest.icao, None)
    candidates.pop(origin.icao, None)

    # --- 3+4. GA-appropriateness + runway suitability ---
    filtered: list[dict] = []
    for c in candidates.values():
        ap = c["airport"]
        if ap.type == "large_airport":
            continue
        if _is_scheduled_service(ap.scheduled_service):
            continue
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
        c["distance_from_dest_nm"] = _haversine_nm(
            dest.lat, dest.lon, ap.latitude_deg, ap.longitude_deg,
        )
        filtered.append(c)

    # --- 6. Cap to the nearest N by destination distance (log the cap) ---
    filtered.sort(key=lambda c: c["distance_from_dest_nm"])
    capped = filtered[:max_candidates]
    if len(filtered) > max_candidates:
        logger.info(
            "Alternates: capped candidates %d → %d (nearest-to-destination)",
            len(filtered), max_candidates,
        )

    # --- 5. Fetch destination + candidates at ETA (shared per-model split) ---
    fetch_airports = [WatchlistAirport(icao=dest.icao, lat=dest.lat, lon=dest.lon)]
    for c in capped:
        ap = c["airport"]
        fetch_airports.append(
            WatchlistAirport(icao=ap.ident, lat=ap.latitude_deg, lon=ap.longitude_deg)
        )

    by_icao = _fetch_eta_snapshots(fetch_airports, eta_dt)
    fetch_icaos = [a.icao for a in fetch_airports]
    try:
        runways = get_runway_ends(fetch_icaos, airports_db_path)
    except Exception:
        logger.warning("Alternates: runway lookup failed", exc_info=True)
        runways = {}

    # --- Destination assessment (drives axes + the instrument-approach gate) ---
    dest_assessed = _assess(dest.icao, by_icao, runways)
    if dest_assessed is None:
        logger.info("Alternates: no destination snapshot at ETA; skipping stage")
        return None
    _, dest_cons = dest_assessed
    dest_category = dest_cons["flight_category"]
    dest_wind = dest_cons.get("wind_speed_kt")
    dest_crosswind = dest_cons.get("crosswind_kt")
    dest_idx = _cat_idx(dest_category)
    require_approach = dest_idx >= _REQUIRE_APPROACH_FROM

    # --- Build each alternate ---
    alternates: list[AlternateAirport] = []
    for c in capped:
        ap = c["airport"]
        assessed = _assess(ap.ident, by_icao, runways)
        if assessed is None:
            continue
        per_model, cons = assessed

        # Suitability: instrument approach + best precision tier (minima proxy).
        # The gate itself is applied *after* the loop so we can detect when the
        # airport DB simply has no procedure data and degrade gracefully.
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
        if enroute is not None and enroute < dest_enroute_nm - ALT_POSITION_MARGIN_NM:
            position = "before"
        else:
            position = "after"
        dist_from_dest = c["distance_from_dest_nm"]
        gc_dep_alt = _haversine_nm(origin.lat, origin.lon, ap.latitude_deg, ap.longitude_deg)
        detour_early_nm = round(gc_dep_alt - gc_dep_dest, 1)
        detour_late_nm = round(dist_from_dest, 1)

        # Assessment vs destination, per axis.
        alt_cat = cons["flight_category"]
        alt_idx = _cat_idx(alt_cat)
        alt_wind = cons.get("wind_speed_kt")
        alt_xw = cons.get("crosswind_kt")

        better_category = alt_idx < dest_idx
        better_wind = (
            alt_wind is not None and dest_wind is not None and alt_wind < dest_wind
        )
        better_crosswind = (
            alt_xw is not None and dest_crosswind is not None and alt_xw < dest_crosswind
        )
        not_worse_cat = alt_idx <= dest_idx
        not_worse_wind = alt_wind is None or dest_wind is None or alt_wind <= dest_wind
        not_worse_xw = (
            alt_xw is None or dest_crosswind is None or alt_xw <= dest_crosswind
        )
        dominates = (
            not_worse_cat
            and not_worse_wind
            and not_worse_xw
            and (better_category or better_wind or better_crosswind)
        )

        alternates.append(AlternateAirport(
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
            better_category=better_category,
            better_wind=better_wind,
            better_crosswind=better_crosswind,
            dominates_destination=dominates,
        ))

    # Instrument-approach gate (applied here, not per-row, so it can degrade
    # gracefully). When the destination is MVFR/IFR/LIFR, a field with no
    # published approach is not a planning-grade IFR alternate — drop it. BUT if
    # *no* candidate has approach data, the airport DB almost certainly lacks
    # procedure data (the dev nav.db has zero procedure rows); going dark in that
    # case is worse than showing weather-better fields with the approach column
    # blank, so relax the gate and flag it.
    approach_filter_relaxed = False
    if require_approach:
        with_iap = [a for a in alternates if a.has_instrument_approach]
        if with_iap:
            alternates = with_iap
        elif alternates:
            approach_filter_relaxed = True
            logger.info(
                "Alternates: destination %s is %s but no candidate has approach "
                "data (procedure data likely absent) — showing %d unfiltered, flagged",
                dest.icao, dest_category, len(alternates),
            )

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
        eta=eta_dt,
        corridor_nm=corridor_nm,
        radius_nm=radius_nm,
        require_approach=require_approach,
        approach_filter_relaxed=approach_filter_relaxed,
        candidates_evaluated=len(capped),
        alternates=alternates,
        nearest_improving=nearest_improving,
        computed_at=now,
    )
