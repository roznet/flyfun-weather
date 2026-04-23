"""Airport resolution from ICAO codes via euro_aip database."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from euro_aip.storage.database_storage import DatabaseStorage
from timezonefinder import TimezoneFinder

from weatherbrief.models import RunwayEnd, Waypoint


RejectReason = Literal["detour", "unknown"]


@dataclass(frozen=True)
class RejectedWaypoint:
    """A middle waypoint that didn't make the resolved route.

    - ``detour``: resolved to a real point but too far off the direct route
      (RouteResolver's detour filter).
    - ``unknown``: not found in the DB under the current route context.
    """

    name: str
    reason: RejectReason

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
) -> tuple[list[Waypoint], list[RejectedWaypoint]]:
    """Resolve waypoint codes to Waypoints using euro_aip RouteResolver.

    Accepts ICAO airport codes (4 letters), navaid codes (2-3 letters),
    and five-letter fix names. Uses the full route context (departure +
    destination midpoint, then progressive proximity) to disambiguate
    navaids that exist in multiple regions (e.g. "ABB" in Europe vs US).

    Middle waypoints that don't survive resolution are returned in the
    ``rejected`` list with a reason — either ``detour`` (resolved but too
    far off the direct route) or ``unknown`` (no DB match under the route
    context). Callers decide how to react: strict endpoints (create, move,
    update) surface these as 422; lenient endpoints (briefing, distance)
    log and continue with the survivors.

    Args:
        codes: Ordered list of waypoint codes (min 2).
        db_path: Path to the euro_aip SQLite database.

    Returns:
        Tuple of (waypoints, rejected):
          - waypoints: resolved Waypoint objects in order, always includes
            departure and destination.
          - rejected: middle tokens that didn't resolve, each tagged with
            a reason. Empty when the full list was accepted.

    Raises:
        KeyError: If the departure or destination code can't be placed
            on the map — those are mandatory. Missing middles go into
            ``rejected`` instead.
    """
    from euro_aip.models.route_resolver import RouteResolver

    model = _load_airport_model(db_path)
    resolver = RouteResolver(model)

    # Use the full route resolver which disambiguates by proximity and
    # filters points whose detour from the route is too large.
    route_string = " ".join(codes)
    route = resolver.resolve(route_string)

    # euro_aip emits detour-rejected entries as dicts: {"name", "reason",
    # "detour_nm", "leg_nm", "threshold_nm"}. Older releases lack the field
    # entirely — the getattr fallback treats that as "nothing rejected".
    rejected: list[RejectedWaypoint] = [
        RejectedWaypoint(name=str(r["name"]), reason="detour")
        for r in getattr(route, "rejected_waypoints", [])
    ]

    if not route.departure_coords:
        raise KeyError(f"We did not find in our database: {codes[0]}")
    if not route.destination_coords:
        raise KeyError(f"We did not find in our database: {codes[-1]}")

    # Middle tokens that the resolver neither placed nor rejected: the
    # resolver silently dropped them because no DB entry matched under
    # route context. Surface them as ``unknown`` so callers see a single
    # categorized rejection list.
    resolved_middle_upper = {n.upper() for n in route.waypoints}
    detour_rejected_upper = {r.name.upper() for r in rejected}
    for c in codes[1:-1]:
        cu = c.upper()
        if cu not in resolved_middle_upper and cu not in detour_rejected_upper:
            rejected.append(RejectedWaypoint(name=c, reason="unknown"))

    all_points: list[tuple[str, object]] = [(codes[0], route.departure_coords)]
    for name, coord in zip(route.waypoints, route.waypoint_coords):
        all_points.append((name, coord))
    all_points.append((codes[-1], route.destination_coords))

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

    return waypoints, rejected


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
