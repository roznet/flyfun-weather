"""METAR/TAF verification collection — Phase 1.

Collects METAR/TAF observations for flights in their active window
and stores them in the verification database. Observations are
standalone (keyed by icao + observation_time), flights link to them
via flight_verification_map.

Phase 2 adds scoring (model vs METAR, TAF vs METAR).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    BriefingPackRow,
    FlightRow,
    FlightVerificationMapRow,
    VerificationCycleRow,
    VerificationObservationRow,
)
from weatherbrief.models.verification import VerificationObservation

logger = logging.getLogger(__name__)

_DEFAULT_CORRIDOR_NM = 15.0
_BATCH_SIZE = 400  # aviationweather.gov limit per request


# ---------------------------------------------------------------------------
# Phase A — Find active flights
# ---------------------------------------------------------------------------


def find_verifiable_flights(db: Session) -> list[FlightRow]:
    """Find flights in the observation window with at least one briefing pack.

    Window: departure_time - 1h  ≤  now  ≤  departure_time + duration + 1h
    """
    now = datetime.now(timezone.utc)

    stmt = (
        select(FlightRow)
        .join(BriefingPackRow)
        .where(
            FlightRow.verification_status.is_(None)
            | ~FlightRow.verification_status.in_(["complete", "scored"])
        )
        .where(FlightRow.departure_time <= now + timedelta(hours=1))
        .distinct()
    )
    rows = db.execute(stmt).scalars().all()

    active: list[FlightRow] = []
    for row in rows:
        dep = row.departure_time
        if dep is not None and dep.tzinfo is None:
            dep = dep.replace(tzinfo=timezone.utc)
        if dep is None:
            continue
        flight_end = dep + timedelta(hours=row.flight_duration_hours or 0)
        window_end = flight_end + timedelta(hours=1)
        if now <= window_end:
            active.append(row)

    return active


# ---------------------------------------------------------------------------
# Phase A — Gather & deduplicate airports
# ---------------------------------------------------------------------------


def _resolve_corridor_airports(
    flight_row: FlightRow,
    airports_db_path: str,
    corridor_nm: float,
) -> list[tuple[str, float]]:
    """Resolve corridor airports for a flight via local spatial query.

    Uses model.find_airports_near_route() which queries the local SQLite
    airport database — no network calls. METAR/TAF fetching happens only
    once, later in Phase B.

    Returns list of (icao, distance_from_route_nm).
    """
    from weatherbrief.airports import _load_airport_model

    waypoints_raw = json.loads(flight_row.waypoints_json or "[]")
    if len(waypoints_raw) < 2:
        return []

    route_icaos = [
        wp.get("icao", wp) if isinstance(wp, dict) else wp
        for wp in waypoints_raw
    ]

    model = _load_airport_model(airports_db_path)
    nearby = model.find_airports_near_route(route_icaos, distance_nm=corridor_nm)

    airports: list[tuple[str, float]] = []
    for entry in nearby:
        airport = entry["airport"]
        airports.append((airport.ident, entry["segment_distance_nm"]))

    return airports


def gather_airports(
    flights: list[FlightRow],
    db: Session,
    airports_db_path: str,
    corridor_nm: float = _DEFAULT_CORRIDOR_NM,
) -> dict[str, set[str]]:
    """Gather all corridor airports across active flights, deduplicated.

    For flights with verification_status=NULL (first cycle), runs a spatial
    query and caches (flight_id, icao, distance) in flight_verification_map.
    For flights with verification_status="collecting", reads cached ICAOs.

    Returns:
        dict mapping icao → set of flight_ids that need it.
    """
    icao_to_flights: dict[str, set[str]] = {}

    for row in flights:
        if row.verification_status == "collecting":
            # Read cached ICAOs from map
            cached = db.execute(
                select(FlightVerificationMapRow.icao)
                .where(FlightVerificationMapRow.flight_id == row.id)
                .distinct()
            ).scalars().all()
            for icao in cached:
                icao_to_flights.setdefault(icao, set()).add(row.id)
        else:
            # First cycle — spatial query, cache results, set status
            try:
                airports = _resolve_corridor_airports(
                    row, airports_db_path, corridor_nm,
                )
            except Exception:
                logger.warning(
                    "Failed to resolve corridor airports for %s", row.id,
                    exc_info=True,
                )
                continue

            for icao, distance in airports:
                icao_to_flights.setdefault(icao, set()).add(row.id)
                # Cache in map (observation_id=NULL for now)
                existing = db.execute(
                    select(FlightVerificationMapRow)
                    .where(FlightVerificationMapRow.flight_id == row.id)
                    .where(FlightVerificationMapRow.icao == icao)
                    .where(FlightVerificationMapRow.observation_id.is_(None))
                ).scalar_one_or_none()
                if existing is None:
                    db.add(FlightVerificationMapRow(
                        flight_id=row.id,
                        icao=icao,
                        distance_from_route_nm=distance,
                    ))

            row.verification_status = "collecting"

    db.flush()
    return icao_to_flights


# ---------------------------------------------------------------------------
# Phase B — Batch fetch METAR/TAF
# ---------------------------------------------------------------------------


def fetch_observations_batch(
    icaos: list[str],
    airports_db_path: str,
) -> list[VerificationObservation]:
    """Fetch current METAR/TAF for a list of ICAOs.

    Chunks into batches of 400 (aviationweather.gov limit).
    Returns parsed VerificationObservation objects for airports with METARs.
    """
    from euro_aip.briefing.weather.analysis import WeatherAnalyzer
    from euro_aip.briefing.weather.route_weather import RouteWeatherService
    from weatherbrief.airports import _load_airport_model

    model = _load_airport_model(airports_db_path)
    service = RouteWeatherService()
    now = datetime.now(timezone.utc)
    all_obs: list[VerificationObservation] = []

    # Process in chunks
    for i in range(0, len(icaos), _BATCH_SIZE):
        chunk = icaos[i : i + _BATCH_SIZE]

        try:
            result = service.fetch_route_weather(
                route_icaos=chunk,
                corridor_nm=1,  # minimal corridor — just enough to find the airports themselves
                model=model,
            )
        except Exception:
            logger.warning(
                "METAR/TAF fetch failed for chunk %d-%d", i, i + len(chunk),
                exc_info=True,
            )
            continue

        for raw in result.airports:
            metar = raw.latest_metar
            if metar is None:
                continue  # Skip airports without METAR

            obs = VerificationObservation(
                icao=raw.icao,
                observation_time=metar.observation_time,
                collected_at=now,
                metar_raw=metar.raw_text,
                ceiling_ft=metar.ceiling_ft,
                visibility_m=metar.visibility_meters,
                wind_dir=metar.wind_direction,
                wind_speed_kt=metar.wind_speed,
                wind_gust_kt=metar.wind_gust,
                temperature_c=metar.temperature,
                dewpoint_c=metar.dewpoint,
                qnh=metar.altimeter,
                weather=list(metar.weather_conditions) if metar.weather_conditions else [],
            )

            if metar.flight_category is not None:
                obs.flight_category = metar.flight_category.value

            # TAF fields
            taf = raw.latest_taf
            if taf is not None:
                obs.taf_raw = taf.raw_text
                if hasattr(taf, "issue_time"):
                    obs.taf_issue_time = taf.issue_time

                applicable = WeatherAnalyzer.find_applicable_taf(
                    taf, metar.observation_time,
                )
                if applicable is not None:
                    obs.taf_applicable = applicable.raw_text
                    if applicable.flight_category is not None:
                        obs.taf_flight_category = applicable.flight_category.value
                    obs.taf_ceiling_ft = getattr(applicable, "ceiling_ft", None)
                    obs.taf_visibility_m = getattr(applicable, "visibility_meters", None)
                    obs.taf_wind_dir = applicable.wind_direction
                    obs.taf_wind_speed_kt = applicable.wind_speed
                    obs.taf_wind_gust_kt = applicable.wind_gust

            all_obs.append(obs)

    return all_obs


# ---------------------------------------------------------------------------
# Phase C — Store & link
# ---------------------------------------------------------------------------


def _should_skip_for_hourly_dedup(
    db: Session, icao: str, obs_time: datetime,
) -> bool:
    """One-per-hour filter: keep at most one observation per airport per clock hour.

    Returns True if an observation already exists for this (icao, hour) —
    the caller should skip this observation. Once an observation is stored
    it is never replaced, since scores or FK references may already point
    to it.
    """
    hour_start = obs_time.replace(minute=0, second=0, microsecond=0)
    hour_end = hour_start + timedelta(hours=1)
    existing = db.execute(
        select(VerificationObservationRow.id)
        .where(VerificationObservationRow.icao == icao)
        .where(VerificationObservationRow.observation_time >= hour_start)
        .where(VerificationObservationRow.observation_time < hour_end)
        .limit(1)
    ).scalar_one_or_none()

    return existing is not None


def store_observations(
    observations: list[VerificationObservation],
    icao_to_flights: dict[str, set[str]],
    db: Session,
) -> int:
    """Store observations and create flight linkages.

    Deduplicates on (icao, observation_time). Applies one-per-hour filter.
    Returns count of newly inserted observations.
    """
    inserted = 0

    for obs in observations:
        # Exact dedup: check if this METAR already stored
        existing = db.execute(
            select(VerificationObservationRow)
            .where(VerificationObservationRow.icao == obs.icao)
            .where(VerificationObservationRow.observation_time == obs.observation_time)
        ).scalar_one_or_none()

        if existing is not None:
            obs_row_id = existing.id
        else:
            # One-per-hour filter: keep whichever observation is closer to :00
            if _should_skip_for_hourly_dedup(db, obs.icao, obs.observation_time):
                continue

            row = VerificationObservationRow(
                icao=obs.icao,
                observation_time=obs.observation_time,
                collected_at=obs.collected_at,
                metar_raw=obs.metar_raw,
                flight_category=obs.flight_category,
                ceiling_ft=obs.ceiling_ft,
                visibility_m=obs.visibility_m,
                wind_dir=obs.wind_dir,
                wind_speed_kt=obs.wind_speed_kt,
                wind_gust_kt=obs.wind_gust_kt,
                temperature_c=obs.temperature_c,
                dewpoint_c=obs.dewpoint_c,
                qnh=obs.qnh,
                weather=obs.weather_json(),
                taf_raw=obs.taf_raw,
                taf_applicable=obs.taf_applicable,
                taf_issue_time=obs.taf_issue_time,
                taf_flight_category=obs.taf_flight_category,
                taf_ceiling_ft=obs.taf_ceiling_ft,
                taf_visibility_m=obs.taf_visibility_m,
                taf_wind_dir=obs.taf_wind_dir,
                taf_wind_speed_kt=obs.taf_wind_speed_kt,
                taf_wind_gust_kt=obs.taf_wind_gust_kt,
            )
            db.add(row)
            try:
                db.flush()  # get the id
            except IntegrityError:
                db.rollback()
                # Lost the race — another cycle inserted this observation
                existing = db.execute(
                    select(VerificationObservationRow)
                    .where(VerificationObservationRow.icao == obs.icao)
                    .where(VerificationObservationRow.observation_time == obs.observation_time)
                ).scalar_one_or_none()
                if existing is not None:
                    obs_row_id = existing.id
                else:
                    continue
            else:
                obs_row_id = row.id
                inserted += 1

        # Link to flights
        flight_ids = icao_to_flights.get(obs.icao, set())
        for flight_id in flight_ids:
            existing_link = db.execute(
                select(FlightVerificationMapRow)
                .where(FlightVerificationMapRow.flight_id == flight_id)
                .where(FlightVerificationMapRow.icao == obs.icao)
                .where(FlightVerificationMapRow.observation_id == obs_row_id)
            ).scalar_one_or_none()
            if existing_link is None:
                # Find distance from the cache row
                cache_row = db.execute(
                    select(FlightVerificationMapRow)
                    .where(FlightVerificationMapRow.flight_id == flight_id)
                    .where(FlightVerificationMapRow.icao == obs.icao)
                    .where(FlightVerificationMapRow.observation_id.is_(None))
                ).scalar_one_or_none()
                distance = cache_row.distance_from_route_nm if cache_row else None

                db.add(FlightVerificationMapRow(
                    flight_id=flight_id,
                    icao=obs.icao,
                    observation_id=obs_row_id,
                    distance_from_route_nm=distance,
                ))

    return inserted


# ---------------------------------------------------------------------------
# Phase E — Finalize completed flights
# ---------------------------------------------------------------------------


def finalize_completed_flights(db: Session) -> int:
    """Mark 'collecting' flights past their window as verification_complete.

    Queries the DB directly for flights still in 'collecting' status whose
    observation window has closed (now > departure + duration + 1h).

    Returns count of flights finalized.
    """
    now = datetime.now(timezone.utc)

    stmt = (
        select(FlightRow)
        .where(FlightRow.verification_status == "collecting")
        .where(FlightRow.departure_time.isnot(None))
    )
    rows = db.execute(stmt).scalars().all()

    finalized = 0
    for row in rows:
        dep = row.departure_time
        if dep is not None and dep.tzinfo is None:
            dep = dep.replace(tzinfo=timezone.utc)
        flight_end = dep + timedelta(hours=row.flight_duration_hours or 0)
        if now > flight_end + timedelta(hours=1):
            row.verification_status = "complete"
            finalized += 1

    return finalized


# ---------------------------------------------------------------------------
# Top-level collection orchestrator
# ---------------------------------------------------------------------------


def _ms_since(t0: float) -> int:
    """Milliseconds elapsed since monotonic timestamp *t0*."""
    return int((time.monotonic() - t0) * 1000)


def collect_and_store(
    db: Session,
    airports_db_path: str,
    corridor_nm: float = _DEFAULT_CORRIDOR_NM,
) -> dict:
    """Run one complete collection cycle.

    Returns summary dict with counts.
    """
    cycle_start = time.monotonic()
    started_at = datetime.now(timezone.utc)
    timings: dict[str, int] = {}

    # Phase E — finalize flights past their window (runs independently)
    t0 = time.monotonic()
    finalized = finalize_completed_flights(db)
    if finalized:
        db.commit()
    timings["finalize"] = _ms_since(t0)

    t0 = time.monotonic()
    scored = _score_completed(db, airports_db_path)
    timings["score"] = _ms_since(t0)

    # Phase A — find active flights
    t0 = time.monotonic()
    flights = find_verifiable_flights(db)
    timings["find"] = _ms_since(t0)

    if not flights:
        # No active flights — skip metrics row
        return {"flights": 0, "airports": 0, "observations": 0, "finalized": finalized, "scored": scored}

    t0 = time.monotonic()
    icao_to_flights = gather_airports(flights, db, airports_db_path, corridor_nm)
    timings["gather"] = _ms_since(t0)

    if not icao_to_flights:
        _record_cycle(db, started_at, cycle_start, timings,
                      flights=len(flights), airports=0, inserted=0,
                      finalized=finalized, scored=scored)
        return {"flights": len(flights), "airports": 0, "observations": 0, "finalized": finalized, "scored": scored}

    unique_icaos = sorted(icao_to_flights.keys())
    logger.info(
        "Verification: %d flight(s), %d unique airport(s)",
        len(flights), len(unique_icaos),
    )

    # Phase B — fetch METARs
    t0 = time.monotonic()
    observations = fetch_observations_batch(unique_icaos, airports_db_path)
    timings["fetch"] = _ms_since(t0)

    # Phase C — store observations
    t0 = time.monotonic()
    inserted = store_observations(observations, icao_to_flights, db)
    timings["store"] = _ms_since(t0)

    _record_cycle(db, started_at, cycle_start, timings,
                  flights=len(flights), airports=len(unique_icaos),
                  inserted=inserted, finalized=finalized, scored=scored)

    db.commit()

    logger.info(
        "Verification: %d observations stored, %d flights finalized, %d scored",
        inserted, finalized, scored,
    )

    return {
        "flights": len(flights),
        "airports": len(unique_icaos),
        "observations": inserted,
        "finalized": finalized,
        "scored": scored,
    }


def _record_cycle(
    db: Session,
    started_at: datetime,
    cycle_start: float,
    timings: dict[str, int],
    *,
    flights: int,
    airports: int,
    inserted: int,
    finalized: int,
    scored: int,
) -> None:
    """Insert a verification_cycles row with timing metrics."""
    db.add(VerificationCycleRow(
        started_at=started_at,
        duration_ms=_ms_since(cycle_start),
        phase_finalize_ms=timings.get("finalize", 0),
        phase_find_ms=timings.get("find", 0),
        phase_gather_ms=timings.get("gather", 0),
        phase_fetch_ms=timings.get("fetch", 0),
        phase_store_ms=timings.get("store", 0),
        phase_score_ms=timings.get("score", 0),
        flights=flights,
        airports=airports,
        observations_stored=inserted,
        finalized=finalized,
        scored=scored,
    ))


def _score_completed(db: Session, airports_db_path: str) -> int:
    """Score any flights with verification_status='complete'.

    Returns count of flights scored.
    """
    from weatherbrief.tasks.scoring import score_completed_flights

    try:
        result = score_completed_flights(db, airports_db_path)
        db.commit()
        return result["flights_scored"]
    except Exception:
        logger.warning("Scoring cycle failed", exc_info=True)
        db.rollback()
        return 0
