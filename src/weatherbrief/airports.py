"""Airport resolution from ICAO codes via euro_aip database."""

from __future__ import annotations

from euro_aip.storage.database_storage import DatabaseStorage

from weatherbrief.models import Waypoint


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
        raise KeyError(f"Airport(s) not found in database: {', '.join(missing)}")

    return waypoints
