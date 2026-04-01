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
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    BriefingPackRow,
    FlightRow,
    FlightVerificationMapRow,
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
        .where(FlightRow.verification_status != "complete")
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
    """Resolve corridor airports for a flight via spatial query.

    Returns list of (icao, distance_from_route_nm).
    """
    from euro_aip.briefing.weather.route_weather import RouteWeatherService
    from weatherbrief.airports import _load_airport_model

    waypoints_raw = json.loads(flight_row.waypoints_json or "[]")
    if len(waypoints_raw) < 2:
        return []

    route_icaos = [
        wp.get("icao", wp) if isinstance(wp, dict) else wp
        for wp in waypoints_raw
    ]

    model = _load_airport_model(airports_db_path)
    service = RouteWeatherService()
    result = service.fetch_route_weather(
        route_icaos=route_icaos,
        corridor_nm=corridor_nm,
        model=model,
    )

    airports: list[tuple[str, float]] = []
    for raw in result.airports:
        # Only keep airports that have a METAR — skip empty ones
        if raw.latest_metar is not None:
            airports.append((raw.icao, raw.distance_from_route_nm))

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


def _already_have_observation_this_hour(
    db: Session, icao: str, obs_time: datetime,
) -> bool:
    """Check if we already have an observation for this ICAO in the same clock hour."""
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
            # One-per-hour filter
            if _already_have_observation_this_hour(db, obs.icao, obs.observation_time):
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
            db.flush()  # get the id
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


def finalize_completed_flights(flights: list[FlightRow]) -> int:
    """Mark flights past their window as verification_complete.

    Returns count of flights finalized.
    """
    now = datetime.now(timezone.utc)
    finalized = 0

    for row in flights:
        dep = row.departure_time
        if dep is not None and dep.tzinfo is None:
            dep = dep.replace(tzinfo=timezone.utc)
        if dep is None:
            continue
        flight_end = dep + timedelta(hours=row.flight_duration_hours or 0)
        if now > flight_end + timedelta(hours=1):
            row.verification_status = "complete"
            finalized += 1

    return finalized


# ---------------------------------------------------------------------------
# Top-level collection orchestrator
# ---------------------------------------------------------------------------


def collect_and_store(
    db: Session,
    airports_db_path: str,
    corridor_nm: float = _DEFAULT_CORRIDOR_NM,
) -> dict:
    """Run one complete collection cycle.

    Returns summary dict with counts.
    """
    # Phase A
    flights = find_verifiable_flights(db)
    if not flights:
        return {"flights": 0, "airports": 0, "observations": 0, "finalized": 0}

    icao_to_flights = gather_airports(flights, db, airports_db_path, corridor_nm)
    if not icao_to_flights:
        finalized = finalize_completed_flights(flights)
        db.commit()
        return {"flights": len(flights), "airports": 0, "observations": 0, "finalized": finalized}

    unique_icaos = sorted(icao_to_flights.keys())
    logger.info(
        "Verification: %d flight(s), %d unique airport(s)",
        len(flights), len(unique_icaos),
    )

    # Phase B
    observations = fetch_observations_batch(unique_icaos, airports_db_path)

    # Phase C
    inserted = store_observations(observations, icao_to_flights, db)

    # Phase E
    finalized = finalize_completed_flights(flights)

    db.commit()

    logger.info(
        "Verification: %d observations stored, %d flights finalized",
        inserted, finalized,
    )

    return {
        "flights": len(flights),
        "airports": len(unique_icaos),
        "observations": inserted,
        "finalized": finalized,
    }
