"""Phase 2 — Verification scoring: model-vs-METAR and TAF-vs-METAR.

Core principle: scoring uses the SAME derivation functions as the advisory
pipeline so we verify exactly what users see.

Runs automatically when a flight completes (verification_status → "complete"),
and can be re-run via CLI for backfill.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    BriefingPackRow,
    FlightRow,
    FlightVerificationMapRow,
    TafVerificationScoreRow,
    VerificationObservationRow,
    VerificationScoreRow,
)

logger = logging.getLogger(__name__)

_M_PER_SM = 1609.34

# METAR weather phenomena indicating precipitation
_PRECIP_PHENOMENA = frozenset({
    "DZ", "RA", "SN", "SG", "IC", "PL", "GR", "GS", "UP",
    "+DZ", "+RA", "+SN", "+SG", "+PL", "+GR", "+GS",
    "-DZ", "-RA", "-SN", "-SG", "-PL", "-GR", "-GS",
    "FZRA", "FZDZ", "+FZRA", "+FZDZ", "-FZRA", "-FZDZ",
    "RASN", "SNRA", "SHSN", "SHRA", "SHGR", "SHGS",
    "+SHRA", "+SHSN", "+SHGR", "+SHGS",
    "-SHRA", "-SHSN", "-SHGR", "-SHGS",
    "TSRA", "TSSN", "TSGR", "TSGS",
})


# ---------------------------------------------------------------------------
# Delta helpers
# ---------------------------------------------------------------------------


def _delta(model_val: float | None, obs_val: float | None) -> float | None:
    """Return model - obs, or None if either missing."""
    if model_val is None or obs_val is None:
        return None
    return model_val - obs_val


def _circular_delta(dir_a: float | None, dir_b: float | None) -> float | None:
    """Circular delta for wind direction, result in -180..+180."""
    if dir_a is None or dir_b is None:
        return None
    d = dir_a - dir_b
    while d > 180:
        d -= 360
    while d < -180:
        d += 360
    return d


def _has_precip(weather: list[str]) -> bool:
    """Check if METAR weather list contains precipitation."""
    return any(w in _PRECIP_PHENOMENA for w in weather)


# ---------------------------------------------------------------------------
# Nearest route point matching
# ---------------------------------------------------------------------------


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    R_NM = 3440.065  # Earth radius in nautical miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return 2 * R_NM * math.asin(math.sqrt(a))


def _find_nearest_route_point(
    airport_lat: float,
    airport_lon: float,
    analyses: list,
) -> int | None:
    """Find index of the nearest RoutePointAnalysis to an airport.

    Returns None if analyses is empty.
    """
    if not analyses:
        return None
    best_idx = 0
    best_dist = float("inf")
    for i, rpa in enumerate(analyses):
        d = _haversine_nm(airport_lat, airport_lon, rpa.lat, rpa.lon)
        if d < best_dist:
            best_dist = d
            best_idx = i
    return best_idx


def _find_nearest_waypoint_forecasts(
    airport_lat: float,
    airport_lon: float,
    forecasts: list,
) -> dict:
    """Find the WaypointForecast nearest to an airport for each model.

    Returns dict mapping model_name → WaypointForecast for the nearest waypoint.
    """
    if not forecasts:
        return {}

    # Group forecasts by model, pick nearest waypoint per model
    best_per_model: dict = {}  # model_name → (distance, WaypointForecast)
    for wpf in forecasts:
        model_name = wpf.model.value
        d = _haversine_nm(airport_lat, airport_lon, wpf.waypoint.lat, wpf.waypoint.lon)
        current = best_per_model.get(model_name)
        if current is None or d < current[0]:
            best_per_model[model_name] = (d, wpf)

    return {k: v[1] for k, v in best_per_model.items()}


# ---------------------------------------------------------------------------
# Score one observation against one model forecast
# ---------------------------------------------------------------------------


def _score_model_vs_metar(
    obs_row: VerificationObservationRow,
    obs_weather: list[str],
    sounding,  # SoundingAnalysis | None
    hourly,  # HourlyForecast | None
    runway_ends: list,
    model: str,
    model_init_time: datetime,
    days_out: int,
) -> VerificationScoreRow | None:
    """Compute a single model-vs-METAR score using advisory pipeline functions."""
    from weatherbrief.analysis.airport_conditions import (
        classify_flight_category,
        reconcile_ceiling,
    )
    from weatherbrief.models.analysis import ConvectiveRisk
    from weatherbrief.tasks.route_weather import compute_wind_advisory

    if hourly is None:
        return None

    lead_hours = int(
        (obs_row.observation_time - model_init_time).total_seconds() / 3600
    )

    # Ceiling — same as advisory pipeline
    model_ceiling = reconcile_ceiling(sounding, hourly)

    # Visibility in statute miles — same conversion as advisory
    model_vis_sm = (
        round(hourly.visibility_m / _M_PER_SM, 1)
        if hourly.visibility_m is not None
        else None
    )
    obs_vis_sm = (
        round(obs_row.visibility_m / _M_PER_SM, 1)
        if obs_row.visibility_m is not None
        else None
    )

    # Flight category — same function
    model_cat = classify_flight_category(model_ceiling, model_vis_sm)
    obs_cat = classify_flight_category(
        float(obs_row.ceiling_ft) if obs_row.ceiling_ft is not None else None,
        obs_vis_sm,
    )

    # Wind advisory — same function + thresholds
    model_adv, _, _, _ = compute_wind_advisory(
        hourly.wind_direction_10m_deg,
        hourly.wind_speed_10m_kt,
        hourly.wind_gusts_10m_kt,
        runway_ends,
    )
    obs_adv, _, _, _ = compute_wind_advisory(
        obs_row.wind_dir, obs_row.wind_speed_kt, obs_row.wind_gust_kt,
        runway_ends,
    )

    # Precipitation
    model_has_precip = (
        (hourly.precipitation_mm or 0) > 0
        or (hourly.snowfall_cm or 0) > 0
    )
    obs_has_precip = _has_precip(obs_weather)

    # Convection
    model_has_convection = (
        sounding is not None
        and sounding.convective is not None
        and sounding.convective.risk_level is not None
        and sounding.convective.risk_level not in (
            ConvectiveRisk.NONE, ConvectiveRisk.MARGINAL,
        )
    )
    obs_has_convection = "TS" in obs_weather

    return VerificationScoreRow(
        observation_id=obs_row.id,
        icao=obs_row.icao,
        observation_time=obs_row.observation_time,
        model=model,
        model_init_time=model_init_time,
        lead_hours=lead_hours,
        days_out=days_out,
        obs_flight_category=obs_cat.value,
        model_flight_category=model_cat.value,
        category_match=(obs_cat == model_cat),
        ceiling_delta_ft=(
            _delta(model_ceiling, float(obs_row.ceiling_ft))
            if obs_row.ceiling_ft is not None and model_ceiling is not None
            else None
        ),
        visibility_delta_m=_delta(
            hourly.visibility_m, float(obs_row.visibility_m)
        ) if obs_row.visibility_m is not None and hourly.visibility_m is not None else None,
        wind_speed_delta_kt=_delta(
            hourly.wind_speed_10m_kt, float(obs_row.wind_speed_kt)
        ) if obs_row.wind_speed_kt is not None and hourly.wind_speed_10m_kt is not None else None,
        wind_dir_delta_deg=_circular_delta(
            hourly.wind_direction_10m_deg, float(obs_row.wind_dir)
        ) if obs_row.wind_dir is not None and hourly.wind_direction_10m_deg is not None else None,
        temperature_delta_c=_delta(
            hourly.temperature_2m_c, float(obs_row.temperature_c)
        ) if obs_row.temperature_c is not None and hourly.temperature_2m_c is not None else None,
        obs_wind_advisory=obs_adv,
        model_wind_advisory=model_adv,
        advisory_match=(obs_adv == model_adv) if obs_adv is not None and model_adv is not None else None,
        obs_has_precipitation=obs_has_precip,
        model_has_precipitation=model_has_precip,
        obs_has_convection=obs_has_convection,
        model_has_convection=model_has_convection,
    )


# ---------------------------------------------------------------------------
# Score TAF vs METAR
# ---------------------------------------------------------------------------


def _score_taf_vs_metar(
    obs_row: VerificationObservationRow,
    obs_weather: list[str],
    runway_ends: list,
) -> TafVerificationScoreRow | None:
    """Score one TAF prediction against its METAR observation."""
    from weatherbrief.tasks.route_weather import compute_wind_advisory

    if obs_row.taf_issue_time is None or obs_row.taf_flight_category is None:
        return None

    lead_hours = int(
        (obs_row.observation_time - obs_row.taf_issue_time).total_seconds() / 3600
    )

    obs_adv, _, _, _ = compute_wind_advisory(
        obs_row.wind_dir, obs_row.wind_speed_kt, obs_row.wind_gust_kt,
        runway_ends,
    )
    taf_adv, _, _, _ = compute_wind_advisory(
        obs_row.taf_wind_dir, obs_row.taf_wind_speed_kt, obs_row.taf_wind_gust_kt,
        runway_ends,
    )

    return TafVerificationScoreRow(
        observation_id=obs_row.id,
        icao=obs_row.icao,
        observation_time=obs_row.observation_time,
        taf_issue_time=obs_row.taf_issue_time,
        lead_hours=lead_hours,
        obs_flight_category=obs_row.flight_category,
        taf_flight_category=obs_row.taf_flight_category,
        category_match=(obs_row.flight_category == obs_row.taf_flight_category),
        ceiling_delta_ft=_delta(
            float(obs_row.taf_ceiling_ft) if obs_row.taf_ceiling_ft is not None else None,
            float(obs_row.ceiling_ft) if obs_row.ceiling_ft is not None else None,
        ),
        visibility_delta_m=_delta(
            float(obs_row.taf_visibility_m) if obs_row.taf_visibility_m is not None else None,
            float(obs_row.visibility_m) if obs_row.visibility_m is not None else None,
        ),
        wind_speed_delta_kt=_delta(
            float(obs_row.taf_wind_speed_kt) if obs_row.taf_wind_speed_kt is not None else None,
            float(obs_row.wind_speed_kt) if obs_row.wind_speed_kt is not None else None,
        ),
        wind_dir_delta_deg=_circular_delta(
            float(obs_row.taf_wind_dir) if obs_row.taf_wind_dir is not None else None,
            float(obs_row.wind_dir) if obs_row.wind_dir is not None else None,
        ),
        obs_wind_advisory=obs_adv,
        taf_wind_advisory=taf_adv,
        advisory_match=(obs_adv == taf_adv) if obs_adv is not None and taf_adv is not None else None,
    )


# ---------------------------------------------------------------------------
# Pack loading helpers
# ---------------------------------------------------------------------------


def _select_packs_for_scoring(
    db: Session,
    flight_id: str,
) -> list[BriefingPackRow]:
    """Select one pack per days_out (latest fetch_timestamp wins)."""
    all_packs = db.execute(
        select(BriefingPackRow)
        .where(BriefingPackRow.flight_id == flight_id)
        .order_by(BriefingPackRow.days_out, BriefingPackRow.fetch_timestamp.desc())
    ).scalars().all()

    seen_days: set[int] = set()
    selected: list[BriefingPackRow] = []
    for pack in all_packs:
        if pack.days_out not in seen_days:
            seen_days.add(pack.days_out)
            selected.append(pack)

    return selected


def _load_pack_data(pack_row: BriefingPackRow) -> tuple:
    """Load forecast and route analysis data from a pack.

    Returns (forecast_snapshot, route_analyses, model_init_times) or
    (None, None, None) if data unavailable.
    """
    from weatherbrief.models.analysis import ForecastSnapshot, RouteAnalysesManifest
    from weatherbrief.tasks.artifacts import load_forecasts, load_route_analyses

    pack_dir = Path(pack_row.artifact_path)
    if not pack_dir.exists():
        return None, None, None

    try:
        forecast_data = load_forecasts(pack_dir)
        if forecast_data is None:
            return None, None, None
        snapshot = ForecastSnapshot.model_validate(forecast_data)
    except Exception:
        logger.warning("Failed to load forecasts from %s", pack_dir, exc_info=True)
        return None, None, None

    try:
        route_analyses = load_route_analyses(pack_dir)
    except Exception:
        logger.warning("Failed to load route analyses from %s", pack_dir, exc_info=True)
        route_analyses = None

    model_init_times: dict[str, int] = {}
    if pack_row.model_init_times_json:
        try:
            model_init_times = json.loads(pack_row.model_init_times_json)
        except (json.JSONDecodeError, TypeError):
            pass

    return snapshot, route_analyses, model_init_times


# ---------------------------------------------------------------------------
# Score all observations for a completed flight
# ---------------------------------------------------------------------------


def score_flight(
    flight_row: FlightRow,
    db: Session,
    airports_db_path: str,
) -> dict[str, int]:
    """Score all observations for a completed flight against model forecasts and TAF.

    Returns dict with counts: {"model_scores": N, "taf_scores": N}.
    """
    from weatherbrief.airports import _load_airport_model, get_runway_ends

    # Get all observations linked to this flight
    map_rows = db.execute(
        select(FlightVerificationMapRow)
        .where(FlightVerificationMapRow.flight_id == flight_row.id)
        .where(FlightVerificationMapRow.observation_id.isnot(None))
    ).scalars().all()

    if not map_rows:
        return {"model_scores": 0, "taf_scores": 0}

    # Collect unique ICAOs and observation IDs
    obs_ids = {m.observation_id for m in map_rows}
    icaos = {m.icao for m in map_rows}

    # Batch-load runway data
    runway_map = get_runway_ends(sorted(icaos), airports_db_path)

    # Load airport coordinates for route-point matching
    airport_model = _load_airport_model(airports_db_path)
    airport_coords: dict[str, tuple[float, float]] = {}
    for icao in icaos:
        apt = airport_model.airports.get(icao)
        if apt is not None:
            airport_coords[icao] = (apt.latitude, apt.longitude)

    # Load observations
    obs_rows = db.execute(
        select(VerificationObservationRow)
        .where(VerificationObservationRow.id.in_(obs_ids))
    ).scalars().all()
    obs_by_id: dict[int, VerificationObservationRow] = {o.id: o for o in obs_rows}

    # Select packs to score (one per days_out)
    packs = _select_packs_for_scoring(db, flight_row.id)
    if not packs:
        logger.info("No packs for flight %s, scoring TAF only", flight_row.id)

    model_scores = 0
    taf_scores = 0

    # Score against model forecasts from each pack
    for pack_row in packs:
        snapshot, route_analyses, init_times = _load_pack_data(pack_row)
        if snapshot is None:
            continue

        # Build model → init_datetime mapping
        model_init_datetimes: dict[str, datetime] = {}
        for model_name, init_ts in init_times.items():
            model_init_datetimes[model_name] = datetime.fromtimestamp(
                init_ts, tz=timezone.utc,
            )

        # Build per-model forecast lookup: model → {icao → (sounding, hourly)}
        for obs_row in obs_rows:
            icao = obs_row.icao
            runway_ends = runway_map.get(icao, [])
            coords = airport_coords.get(icao)
            weather = json.loads(obs_row.weather) if obs_row.weather else []

            # Find nearest route point for sounding data
            rpa_idx = None
            if route_analyses and coords:
                rpa_idx = _find_nearest_route_point(
                    coords[0], coords[1], route_analyses.analyses,
                )

            # Find nearest waypoint forecast per model for this airport
            nearest_wpf: dict = {}
            if coords:
                nearest_wpf = _find_nearest_waypoint_forecasts(
                    coords[0], coords[1], snapshot.forecasts,
                )

            for model_name, wpf in nearest_wpf.items():
                init_dt = model_init_datetimes.get(model_name)
                if init_dt is None:
                    continue

                # Check if score already exists
                existing = db.execute(
                    select(VerificationScoreRow.id)
                    .where(VerificationScoreRow.icao == icao)
                    .where(VerificationScoreRow.observation_time == obs_row.observation_time)
                    .where(VerificationScoreRow.model == model_name)
                    .where(VerificationScoreRow.model_init_time == init_dt)
                    .limit(1)
                ).scalar_one_or_none()
                if existing is not None:
                    continue

                # Get hourly forecast closest to observation time
                hourly = wpf.at_time(obs_row.observation_time)

                # Get sounding from route analyses if available
                sounding = None
                if rpa_idx is not None and route_analyses:
                    rpa = route_analyses.analyses[rpa_idx]
                    sounding = rpa.sounding.get(model_name)

                score_row = _score_model_vs_metar(
                    obs_row=obs_row,
                    obs_weather=weather,
                    sounding=sounding,
                    hourly=hourly,
                    runway_ends=runway_ends,
                    model=model_name,
                    model_init_time=init_dt,
                    days_out=pack_row.days_out,
                )
                if score_row is not None:
                    db.add(score_row)
                    model_scores += 1

    # Score TAF vs METAR (independent of pack data)
    for obs_row in obs_rows:
        icao = obs_row.icao
        weather = json.loads(obs_row.weather) if obs_row.weather else []
        runway_ends = runway_map.get(icao, [])

        if obs_row.taf_issue_time is None:
            continue

        # Check if TAF score already exists
        existing = db.execute(
            select(TafVerificationScoreRow.id)
            .where(TafVerificationScoreRow.icao == icao)
            .where(TafVerificationScoreRow.observation_time == obs_row.observation_time)
            .where(TafVerificationScoreRow.taf_issue_time == obs_row.taf_issue_time)
            .limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            continue

        taf_row = _score_taf_vs_metar(obs_row, weather, runway_ends)
        if taf_row is not None:
            db.add(taf_row)
            taf_scores += 1

    db.flush()
    return {"model_scores": model_scores, "taf_scores": taf_scores}


# ---------------------------------------------------------------------------
# Batch scoring for completed flights
# ---------------------------------------------------------------------------


def score_completed_flights(
    db: Session,
    airports_db_path: str,
) -> dict[str, int]:
    """Score all flights with verification_status='complete' that haven't been scored.

    Sets verification_status='scored' after successful scoring.
    Returns aggregate counts.
    """
    flights = db.execute(
        select(FlightRow)
        .where(FlightRow.verification_status == "complete")
    ).scalars().all()

    total_model = 0
    total_taf = 0
    scored_flights = 0

    for flight_row in flights:
        try:
            result = score_flight(flight_row, db, airports_db_path)
            total_model += result["model_scores"]
            total_taf += result["taf_scores"]
            flight_row.verification_status = "scored"
            scored_flights += 1
        except Exception:
            logger.warning(
                "Failed to score flight %s", flight_row.id, exc_info=True,
            )

    db.flush()
    return {
        "flights_scored": scored_flights,
        "model_scores": total_model,
        "taf_scores": total_taf,
    }


def backfill_scores(
    db: Session,
    airports_db_path: str,
    flight_id: str | None = None,
) -> dict[str, int]:
    """Re-run scoring for flights, useful for backfill after code changes.

    If flight_id is provided, scores only that flight.
    Otherwise, scores all flights with status 'complete' or 'scored'.
    """
    if flight_id:
        flight_row = db.get(FlightRow, flight_id)
        if flight_row is None:
            raise ValueError(f"Flight '{flight_id}' not found")
        flights = [flight_row]
    else:
        flights = db.execute(
            select(FlightRow)
            .where(FlightRow.verification_status.in_(["complete", "scored"]))
        ).scalars().all()

    total_model = 0
    total_taf = 0
    scored_flights = 0

    for flight_row in flights:
        try:
            result = score_flight(flight_row, db, airports_db_path)
            total_model += result["model_scores"]
            total_taf += result["taf_scores"]
            if flight_row.verification_status == "complete":
                flight_row.verification_status = "scored"
            scored_flights += 1
        except Exception:
            logger.warning(
                "Backfill: failed to score flight %s", flight_row.id,
                exc_info=True,
            )

    db.flush()
    return {
        "flights_scored": scored_flights,
        "model_scores": total_model,
        "taf_scores": total_taf,
    }
