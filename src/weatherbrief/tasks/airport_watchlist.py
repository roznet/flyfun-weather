"""Airport watchlist for standalone verification.

Manages a list of METAR-reporting airports used by the standalone
verification pipeline. Airports are grouped by ICAO prefix (LF, ED, etc.)
with coordinates from the euro_aip airport database.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# aviationweather.gov METAR endpoint — supports @prefix queries
_AWC_METAR_URL = "https://aviationweather.gov/api/data/metar"

# Default prefixes: FR, DE, UK, NL, BE, CH, AT
DEFAULT_PREFIXES = ["LF", "ED", "EG", "EH", "EB", "LS", "LO"]

_WATCHLIST_FILENAME = "airport_watchlist.json"


@dataclass(frozen=True)
class WatchlistAirport:
    """Airport in the verification watchlist with coordinates."""

    icao: str
    lat: float
    lon: float


_AWC_BATCH_SIZE = 400  # aviationweather.gov limit per request


def discover_airports(
    prefixes: list[str],
    airports_db_path: str,
    timeout: float = 30.0,
) -> dict[str, list[str]]:
    """Discover which airports in the euro_aip database currently report METARs.

    Strategy: load all airports matching the prefixes from the euro_aip DB,
    then batch-query aviationweather.gov to find which ones have recent METARs.

    Args:
        prefixes: ICAO prefixes to search (e.g. ["LF", "ED"]).
        airports_db_path: Path to the euro_aip SQLite database.
        timeout: HTTP request timeout per API call.

    Returns:
        Dict mapping prefix to sorted list of ICAO codes that report METARs.
    """
    from weatherbrief.airports import _load_airport_model

    model = _load_airport_model(airports_db_path)

    # Gather all candidate ICAOs from DB matching the prefixes
    candidates: dict[str, list[str]] = {p: [] for p in prefixes}
    for prefix in prefixes:
        for airport in model.airports:
            if airport.ident.startswith(prefix):
                candidates[prefix].append(airport.ident)
        candidates[prefix].sort()
        logger.info("Prefix %s: %d candidate airports in DB", prefix, len(candidates[prefix]))

    # Flatten and batch-query aviationweather.gov
    all_icaos = [icao for icaos in candidates.values() for icao in icaos]
    reporting: set[str] = set()

    for i in range(0, len(all_icaos), _AWC_BATCH_SIZE):
        chunk = all_icaos[i : i + _AWC_BATCH_SIZE]
        ids_str = ",".join(chunk)
        try:
            resp = requests.get(
                _AWC_METAR_URL,
                params={"ids": ids_str, "format": "json"},
                timeout=timeout,
            )
            if resp.status_code == 204:
                continue  # No data for this chunk
            resp.raise_for_status()
            data = resp.json()
            for entry in data:
                if isinstance(entry, dict) and "icaoId" in entry:
                    reporting.add(entry["icaoId"])
        except Exception:
            logger.warning(
                "METAR check failed for chunk %d-%d", i, i + len(chunk),
                exc_info=True,
            )

    # Filter candidates to only those that report
    result: dict[str, list[str]] = {}
    for prefix in prefixes:
        result[prefix] = sorted(icao for icao in candidates[prefix] if icao in reporting)
        logger.info("Prefix %s: %d reporting (of %d candidates)",
                     prefix, len(result[prefix]), len(candidates[prefix]))

    return result


def save_watchlist(
    airports_by_prefix: dict[str, list[str]],
    configs_dir: str | Path,
) -> Path:
    """Save discovered airports to configs/airport_watchlist.json."""
    path = Path(configs_dir) / _WATCHLIST_FILENAME
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "airports": airports_by_prefix,
    }
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def load_watchlist(configs_dir: str | Path) -> dict[str, list[str]]:
    """Load airport watchlist from configs directory.

    Returns:
        Dict mapping ICAO prefix to list of ICAO codes.
    """
    path = Path(configs_dir) / _WATCHLIST_FILENAME
    if not path.exists():
        raise FileNotFoundError(
            f"Airport watchlist not found at {path}. "
            "Run: python -m weatherbrief.verify discover"
        )
    data = json.loads(path.read_text())
    return data["airports"]


def load_watchlist_with_coords(
    configs_dir: str | Path,
    airports_db_path: str,
) -> list[WatchlistAirport]:
    """Load watchlist and enrich with coordinates from euro_aip database.

    Airports not found in the database are silently skipped.
    """
    from weatherbrief.airports import _load_airport_model

    airports_by_prefix = load_watchlist(configs_dir)
    all_icaos = [icao for icaos in airports_by_prefix.values() for icao in icaos]

    model = _load_airport_model(airports_db_path)
    result: list[WatchlistAirport] = []

    for icao in all_icaos:
        try:
            airport = model.airports[icao]
        except (KeyError, IndexError):
            logger.debug("Watchlist airport %s not in euro_aip database, skipping", icao)
            continue
        result.append(WatchlistAirport(
            icao=icao, lat=airport.latitude_deg, lon=airport.longitude_deg,
        ))

    logger.info(
        "Loaded %d watchlist airports with coordinates (%d skipped — not in DB)",
        len(result),
        len(all_icaos) - len(result),
    )
    return result


def get_configs_dir() -> Path:
    """Get the configs directory path."""
    # Look relative to project root
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    configs = project_root / "configs"
    if configs.is_dir():
        return configs

    # Fall back to env var
    data_dir = os.environ.get("DATA_DIR", "data")
    return Path(data_dir) / "configs"
