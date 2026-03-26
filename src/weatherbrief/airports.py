"""Airport resolution from ICAO codes via euro_aip database."""

from __future__ import annotations

import logging
from functools import lru_cache

from euro_aip.storage.database_storage import DatabaseStorage
from timezonefinder import TimezoneFinder

from weatherbrief.models import RunwayEnd, Waypoint

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _timezone_finder() -> TimezoneFinder:
    return TimezoneFinder()


@lru_cache(maxsize=1)
def _load_airport_model(db_path: str):
    """Load the euro_aip model once and cache it for the process lifetime.

    The model is read-only after loading, so safe to share across calls.
    """
    logger.info("Loading euro_aip airport model from %s", db_path)
    storage = DatabaseStorage(db_path)
    return storage.load_model()


def get_timezone(lat: float, lon: float) -> str | None:
    """Return the IANA timezone name for a lat/lon, or None if unknown."""
    return _timezone_finder().timezone_at(lat=lat, lng=lon)


def resolve_waypoints(codes: list[str], db_path: str) -> list[Waypoint]:
    """Resolve waypoint codes to Waypoints using euro_aip RouteResolver.

    Accepts ICAO airport codes (4 letters), navaid codes (2-3 letters),
    and five-letter fix names. Each code is resolved by trying airport
    lookup first, then waypoint/navaid lookup.

    Args:
        codes: Ordered list of waypoint codes (min 2).
        db_path: Path to the euro_aip SQLite database.

    Returns:
        List of Waypoint objects with coordinates from the database.

    Raises:
        KeyError: If any code is not found in the database.
    """
    from euro_aip.models.route_resolver import RouteResolver

    model = _load_airport_model(db_path)
    resolver = RouteResolver(model)

    waypoints: list[Waypoint] = []
    missing: list[str] = []

    for code in codes:
        point = resolver.resolve_point(code)
        if point is None:
            missing.append(code)
            continue

        waypoints.append(
            Waypoint(
                icao=point.name,
                name=point.name,
                lat=point.latitude,
                lon=point.longitude,
            )
        )

    if missing:
        raise KeyError(f"We did not find in our database: {', '.join(missing)}")

    return waypoints


def get_runway_ends(icao_codes: list[str], db_path: str) -> dict[str, list[RunwayEnd]]:
    """Get all runway end options for given airports.

    Args:
        icao_codes: ICAO codes to look up.
        db_path: Path to the euro_aip SQLite database.

    Returns:
        Dict mapping ICAO code to list of RunwayEnd objects.
    """
    model = _load_airport_model(db_path)

    result: dict[str, list[RunwayEnd]] = {}
    for icao in icao_codes:
        airport = model.airports.get(icao)
        if airport is None:
            result[icao] = []
            continue

        ends: list[RunwayEnd] = []
        for rwy in airport.runways:
            if rwy.closed:
                continue
            if rwy.le_ident and rwy.le_heading_degT is not None:
                ends.append(RunwayEnd(id=rwy.le_ident, heading_deg=rwy.le_heading_degT))
            if rwy.he_ident and rwy.he_heading_degT is not None:
                ends.append(RunwayEnd(id=rwy.he_ident, heading_deg=rwy.he_heading_degT))

        result[icao] = ends

    return result
