"""Airport resolution from ICAO codes via euro_aip database."""

from __future__ import annotations

from euro_aip.storage.database_storage import DatabaseStorage

from weatherbrief.models import RunwayEnd, Waypoint


def resolve_waypoints(icao_codes: list[str], db_path: str) -> list[Waypoint]:
    """Resolve ICAO codes to Waypoints using the euro_aip airport database.

    Args:
        icao_codes: Ordered list of ICAO codes (min 2).
        db_path: Path to the euro_aip SQLite database.

    Returns:
        List of Waypoint objects with coordinates from the database.

    Raises:
        KeyError: If any ICAO code is not found in the database.
    """
    storage = DatabaseStorage(db_path)
    model = storage.load_model()

    waypoints: list[Waypoint] = []
    missing: list[str] = []

    for icao in icao_codes:
        airport = model.airports.get(icao)
        if airport is None:
            missing.append(icao)
            continue

        if airport.latitude_deg is None or airport.longitude_deg is None:
            missing.append(f"{icao} (no coordinates)")
            continue

        waypoints.append(
            Waypoint(
                icao=airport.ident,
                name=airport.name or airport.ident,
                lat=airport.latitude_deg,
                lon=airport.longitude_deg,
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
    storage = DatabaseStorage(db_path)
    model = storage.load_model()

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
