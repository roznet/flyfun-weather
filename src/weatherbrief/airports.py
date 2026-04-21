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


def is_known_waypoint(code: str, db_path: str) -> bool:
    """Check if a single waypoint code is known in the database.

    Checks airports, navaids, and five-letter fixes.
    """
    from euro_aip.models.route_resolver import RouteResolver

    model = _load_airport_model(db_path)
    resolver = RouteResolver(model)
    return resolver.resolve_point(code.upper()) is not None


def resolve_waypoints(
    codes: list[str], db_path: str
) -> tuple[list[Waypoint], list[str]]:
    """Resolve waypoint codes to Waypoints using euro_aip RouteResolver.

    Accepts ICAO airport codes (4 letters), navaid codes (2-3 letters),
    and five-letter fix names. Uses the full route context (departure +
    destination midpoint, then progressive proximity) to disambiguate
    navaids that exist in multiple regions (e.g. "ABB" in Europe vs US).

    Middle waypoints whose resolved coordinates fall too far from the
    route (per RouteResolver's detour filter) are dropped and returned
    in the rejected list instead of raising.

    Args:
        codes: Ordered list of waypoint codes (min 2).
        db_path: Path to the euro_aip SQLite database.

    Returns:
        Tuple of (waypoints, rejected):
          - waypoints: resolved Waypoint objects in order, always includes
            departure and destination.
          - rejected: token names that resolved but were filtered out for
            being too far off route (empty list when nothing was filtered).

    Raises:
        KeyError: If departure, destination, or any middle code is not
            found in the database at all.
    """
    from euro_aip.models.route_resolver import RouteResolver

    model = _load_airport_model(db_path)
    resolver = RouteResolver(model)

    # Use the full route resolver which disambiguates by proximity and
    # filters points whose detour from the route is too large.
    route_string = " ".join(codes)
    route = resolver.resolve(route_string)

    rejected_names = [str(r["name"]) for r in route.rejected_waypoints]

    # Collect all resolved points in order: departure, waypoints, destination
    all_points = []
    if route.departure_coords:
        all_points.append((codes[0], route.departure_coords))
    else:
        raise KeyError(f"We did not find in our database: {codes[0]}")

    for name, coord in zip(route.waypoints, route.waypoint_coords):
        all_points.append((name, coord))

    if route.destination_coords:
        all_points.append((codes[-1], route.destination_coords))
    else:
        raise KeyError(f"We did not find in our database: {codes[-1]}")

    # Middle tokens that were neither resolved nor rejected = genuinely unknown.
    resolved_middle = set(route.waypoints)
    rejected_set = {n.upper() for n in rejected_names}
    missing = [
        c for c in codes[1:-1]
        if c.upper() not in resolved_middle and c.upper() not in rejected_set
    ]
    if missing:
        raise KeyError(f"We did not find in our database: {', '.join(missing)}")

    waypoints: list[Waypoint] = []
    for name, coord in all_points:
        if hasattr(coord, "latitude"):
            # RoutePoint object (from waypoint_coords)
            lat, lon = coord.latitude, coord.longitude
        else:
            # Tuple (from departure_coords / destination_coords)
            lat, lon = coord
        waypoints.append(
            Waypoint(icao=name.upper(), name=name.upper(), lat=lat, lon=lon)
        )

    return waypoints, rejected_names


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
