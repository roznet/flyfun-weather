"""Airport resolution from ICAO codes via euro_aip database."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Literal

from euro_aip.storage.database_storage import DatabaseStorage
from timezonefinder import TimezoneFinder

from weatherbrief.fetch.route_walk import walk_route
from weatherbrief.models import AirportApproaches, RunwayApproach, RunwayEnd, Waypoint

if TYPE_CHECKING:
    from euro_aip.briefing.models.route import Route


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


# IANA timezones identifying the countries we gate country-specific weather
# models on. Mainland coverage is sufficient — a route over French or UK soil
# returns these from the timezone polygons. Extend as country-specific models
# are added.
_COUNTRY_TIMEZONES: dict[str, frozenset[str]] = {
    "FR": frozenset({"Europe/Paris"}),
    "GB": frozenset({"Europe/London"}),
}
_TZ_TO_COUNTRY: dict[str, str] = {
    tz: iso for iso, tzs in _COUNTRY_TIMEZONES.items() for tz in tzs
}


def route_countries(route, spacing_nm: float = 25.0) -> set[str]:
    """ISO-3166 alpha-2 countries (from the gated set) the route passes over.

    Samples the great-circle route every ``spacing_nm`` (named waypoints are
    always included) and maps each point to a country via timezone polygons.
    Detects genuine overflight — a route crossing France without landing there
    still reports ``"FR"`` — which ICAO-prefix matching cannot.

    Only countries in ``_COUNTRY_TIMEZONES`` are reported; that is all the
    model gate needs, so no full boundary dataset is required. ``spacing_nm``
    of 25 keeps even a thin corner-clip from slipping between samples while
    staying a handful of sub-millisecond lookups per route.
    """
    found: set[str] = set()
    for lat, lon, *_ in walk_route(route, spacing_nm):
        iso = _TZ_TO_COUNTRY.get(get_timezone(lat, lon) or "")
        if iso:
            found.add(iso)
    return found


def is_known_waypoint(code: str, db_path: str) -> bool:
    """Check if a single waypoint code is known in the database.

    Checks airports, navaids, and five-letter fixes.
    """
    from euro_aip.models.route_resolver import RouteResolver

    model = _load_airport_model(db_path)
    resolver = RouteResolver(model)
    return resolver.resolve_point(code.upper()) is not None


def resolve_route(codes: list[str], db_path: str) -> Route:
    """Resolve waypoint codes to a euro_aip ``Route`` with coordinates.

    Unlike :func:`resolve_waypoints` (which flattens to a single ``Waypoint``
    list), this returns the richer ``euro_aip.briefing.models.route.Route``
    object the resolver builds: explicit ``departure``/``destination`` endpoints,
    intermediate ``waypoints`` (middle tokens only), resolved coordinates, and
    ``rejected_waypoints``. That ``Route`` is the type ``FlightExchange`` wraps,
    so this is the entry point for emitting a cross-app flight payload.

    Args:
        codes: Ordered waypoint codes (min 2); ``codes[0]`` is the departure,
            ``codes[-1]`` the destination.
        db_path: Path to the euro_aip SQLite database.

    Returns:
        The resolved ``Route``. Timing/altitude/aircraft are NOT set here —
        those live on weather's ``Flight`` and are layered on by the caller.

    Raises:
        KeyError: If the departure or destination can't be placed on the map.
    """
    from euro_aip.models.route_resolver import RouteResolver

    model = _load_airport_model(db_path)
    resolver = RouteResolver(model)
    route = resolver.resolve(" ".join(codes))

    if not route.departure_coords:
        raise KeyError(f"We did not find in our database: {codes[0]}")
    if not route.destination_coords:
        raise KeyError(f"We did not find in our database: {codes[-1]}")

    return route


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
    # resolve_route handles the resolver setup (proximity disambiguation +
    # detour filtering) and the departure/destination coord guards, raising
    # KeyError if an endpoint can't be placed on the map.
    route = resolve_route(codes, db_path)

    # euro_aip emits detour-rejected entries as dicts: {"name", "reason",
    # "detour_nm", "leg_nm", "threshold_nm"}. Older releases lack the field
    # entirely — the getattr fallback treats that as "nothing rejected".
    rejected: list[RejectedWaypoint] = [
        RejectedWaypoint(name=str(r["name"]), reason="detour")
        for r in getattr(route, "rejected_waypoints", [])
    ]

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

    # Same model instance resolve_route just used — _load_airport_model is
    # lru_cached, so this is a dict lookup, not a second DB load.
    model = _load_airport_model(db_path)

    waypoints: list[Waypoint] = []
    for name, coord in all_points:
        if hasattr(coord, "latitude"):
            # RoutePoint object (from waypoint_coords)
            lat, lon = coord.latitude, coord.longitude
        else:
            # Tuple (from departure_coords / destination_coords)
            lat, lon = coord
        code = name.upper()
        waypoints.append(
            Waypoint(icao=code, lat=lat, lon=lon, **_identify(code, model))
        )

    return waypoints, rejected


def _identify(code: str, model) -> dict[str, str | None]:
    """Resolve a waypoint code to its full name (airports) or kind (navaids).

    Only airports carry a plain-language name; euro_aip's waypoint records use
    the identifier as their ``name`` ("GWC", "SITET"), so for those we return
    the code unchanged plus the ``point_type`` that says what it is. Callers
    must never fill the gap with a remembered name — see the airfield-naming
    rule in ``configs/weather_digest/prompts/briefer_v2.md``.
    """
    airport = model.airports.get(code)
    airport_name = getattr(airport, "name", None) if airport is not None else None
    if airport_name:
        return {"name": str(airport_name), "kind": None}

    kind = None
    try:
        waypoint = model.get_waypoint(code)
    except Exception:  # noqa: BLE001 - identity is cosmetic, never fail a route on it
        logger.debug("Waypoint lookup failed for %s", code, exc_info=True)
    else:
        kind = getattr(waypoint, "point_type", None) if waypoint is not None else None

    return {"name": code, "kind": str(kind) if kind else None}


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


# ICAO names a circling-only approach with a single trailing letter ("RNP A",
# "NDB C", "LOC A") instead of a runway number. X/Y/Z are excluded: those are
# straight-in *variant* designators ("ILS Z RWY 09"), not circling. A name
# carrying any digit is a runway-numbered procedure, so it is never circling —
# that guard is what keeps parser artifacts like
# "MIPS: RNP (LNAV) ARINC CODING" (issue #509: 223 of 262 ident-less rows) from
# being mislabelled as circling.
_CIRCLING_SUFFIX_RE = re.compile(r"(?:^|[\s/-])([A-W])$")


def _is_circling_name(name: str | None) -> bool:
    """True when a procedure name reads as an ICAO circling-only approach."""
    if not name:
        return False
    text = name.strip().upper()
    if any(ch.isdigit() for ch in text):
        return False
    return bool(_CIRCLING_SUFFIX_RE.search(text))


# A runway identifier is a 1-2 digit number with an optional L/C/R parallel
# designator. Matching that shape (rather than sifting digits and letters out of
# the whole string) is what keeps a decorated ident like "RWY 09R" from
# normalizing to nonsense.
_RUNWAY_IDENT_RE = re.compile(r"(?<!\d)(\d{1,2})\s*([LCR])?(?![\dA-Z])")


def _normalize_runway_ident(ident: str | None) -> str | None:
    """Canonicalize a runway identifier for joining procedures to runway ends.

    Drops zero padding on the numeric part ("02" -> "2") and any surrounding
    decoration ("RWY 09R" -> "9R"), so a procedure's runway reference joins a
    runway end whichever way either side spelled it. Returns ``None`` when the
    string holds no runway number at all.
    """
    if not ident:
        return None
    match = _RUNWAY_IDENT_RE.search(ident.upper())
    if match is None:
        return None
    return f"{int(match.group(1))}{match.group(2) or ''}"


def get_runway_approaches(
    icao_codes: list[str], db_path: str,
) -> dict[str, AirportApproaches]:
    """Get published instrument approaches per airport, joined to runway ends.

    Mirrors :func:`get_runway_ends`, which supplies the wind side of the same
    question ("which runway does the wind favour?"). This supplies the
    infrastructure side ("which runway can I actually shoot an approach to?"),
    consumed by the ``approach_feasibility`` advisory (issue #509).

    Each :class:`RunwayApproach` resolves into exactly one of three states:

    * ``runway_id`` set — a straight-in approach to that runway END. Only set
      when the procedure's runway identifier joins a live (non-closed) runway
      end that has a true heading, so the evaluator can always pair it with the
      wind components for that end.
    * ``circling=True`` — an ICAO letter-suffixed circling-only procedure.
    * neither — an approach whose alignment could not be resolved. Deliberately
      NOT collapsed into "circling": the consumer treats unresolved alignment as
      uncertainty (which may only soften a grade), never as a hard misalignment.

    The same principle governs failure: an unknown ICAO or a procedure query
    that raises yields ``lookup_failed=True``, never an empty approach list. An
    empty list is a *fact* ("this field has no instrument approach") that the
    grade map treats as the most severe input there is, so a data gap must not
    be able to impersonate one.

    Args:
        icao_codes: ICAO codes to look up.
        db_path: Path to the euro_aip SQLite database.

    Returns:
        Dict mapping ICAO code to :class:`AirportApproaches`, one entry per
        requested code.
    """
    model = _load_airport_model(db_path)

    result: dict[str, AirportApproaches] = {}
    for icao in icao_codes:
        airport = model.airports.get(icao)
        if airport is None:
            result[icao] = AirportApproaches(icao=icao, lookup_failed=True)
            continue

        # Live runway ends with a true heading — the only ends the wind side can
        # grade, so an approach that joins nothing else is left unresolved.
        live_ends: dict[str, str] = {}
        for rwy in getattr(airport, "runways", None) or []:
            if getattr(rwy, "closed", False):
                continue
            for ident, heading in (
                (rwy.le_ident, rwy.le_heading_degT),
                (rwy.he_ident, rwy.he_heading_degT),
            ):
                key = _normalize_runway_ident(ident)
                if key is not None and heading is not None:
                    live_ends[key] = ident.strip().upper()

        try:
            procedures = list(airport.procedures_query.approaches().all())
            has_procedure_data = bool(getattr(airport, "procedures", None))
        except Exception:  # noqa: BLE001 - a bad procedure row must not fail a briefing
            # ``lookup_failed``, NOT an empty approach list: a malformed row is
            # our gap, and "no approach at all" is the single most severe input
            # the grade map takes. Degrading a parse failure into that fact
            # would manufacture a RED out of a data problem.
            logger.debug("Approach lookup failed for %s", icao, exc_info=True)
            result[icao] = AirportApproaches(icao=icao, lookup_failed=True)
            continue

        approaches: list[RunwayApproach] = []
        for proc in procedures:
            name = str(getattr(proc, "name", "") or "")
            raw_ident = None
            try:
                raw_ident = proc.get_full_runway_ident()
            except Exception:  # noqa: BLE001 - fall back to the plain column
                raw_ident = getattr(proc, "runway_ident", None)
            key = _normalize_runway_ident(raw_ident)
            runway_id = live_ends.get(key) if key else None
            approaches.append(RunwayApproach(
                name=name,
                approach_type=getattr(proc, "approach_type", None),
                runway_id=runway_id,
                circling=runway_id is None and _is_circling_name(
                    getattr(proc, "raw_name", None) or name,
                ),
            ))

        result[icao] = AirportApproaches(
            icao=icao,
            has_procedure_data=has_procedure_data,
            approaches=approaches,
        )

    return result


def get_airport_elevations(icao_codes: list[str], db_path: str) -> dict[str, float | None]:
    """Get published field elevation (ft AMSL) for given airports.

    Used to convert MSL model/sounding ceilings to AGL for aviation
    flight-category classification — METAR ceilings are AGL above this
    published field elevation. (#441 finding #3)

    Returns:
        Dict mapping ICAO code to elevation in feet AMSL, or None if unknown.
    """
    model = _load_airport_model(db_path)
    result: dict[str, float | None] = {}
    for icao in icao_codes:
        airport = model.airports.get(icao)
        result[icao] = (
            float(airport.elevation_ft)
            if airport is not None and airport.elevation_ft is not None
            else None
        )
    return result
