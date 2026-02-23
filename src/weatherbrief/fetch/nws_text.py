"""NWS Area Forecast Discussion (AFD) fetcher.

Fetches AFDs from aviationweather.gov for US-based routes.
Each Weather Forecast Office (WFO/CWA) publishes AFDs ~4x daily with
synoptic discussion, aviation-specific sections, and forecaster reasoning.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
from pydantic import BaseModel

logger = logging.getLogger(__name__)

NWS_POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"
AWC_AFD_URL = "https://aviationweather.gov/api/data/fcstdisc?cwa={cwa}&type=af"

_TIMEOUT_SECONDS = 15
_USER_AGENT = "weatherbrief/1.0 (aviation weather briefing tool)"
_WFO_CACHE_FILENAME = "airport_wfo_cache.json"


class NWSTextForecasts(BaseModel):
    """Container for fetched NWS Area Forecast Discussions."""

    afd_texts: dict[str, str]  # CWA code -> AFD text
    fetched_at: datetime


def _load_wfo_cache(cache_dir: Path | None) -> dict[str, str]:
    """Load ICAO -> CWA cache from disk. Returns empty dict on failure."""
    if cache_dir is None:
        return {}
    cache_file = cache_dir / _WFO_CACHE_FILENAME
    if not cache_file.exists():
        return {}
    try:
        return json.loads(cache_file.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt WFO cache file, starting fresh")
        return {}


def _save_wfo_cache(cache_dir: Path | None, cache: dict[str, str]) -> None:
    """Persist ICAO -> CWA cache to disk."""
    if cache_dir is None:
        return
    cache_file = cache_dir / _WFO_CACHE_FILENAME
    try:
        cache_file.write_text(json.dumps(cache))
    except OSError:
        logger.warning("Failed to write WFO cache file", exc_info=True)


def _lookup_wfo(lat: float, lon: float, session: requests.Session) -> str | None:
    """Look up the 3-letter CWA code for a lat/lon via the NWS points API."""
    url = NWS_POINTS_URL.format(lat=f"{lat:.4f}", lon=f"{lon:.4f}")
    try:
        resp = session.get(url, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        return data["properties"]["cwa"]
    except (requests.RequestException, KeyError, ValueError):
        logger.warning("Failed WFO lookup for %.4f,%.4f", lat, lon, exc_info=True)
        return None


def _cwa_to_icao_prefix(cwa: str) -> str:
    """Convert a 3-letter CWA to the ICAO station prefix for the AWC AFD API.

    CONUS offices use K prefix (e.g. MTR -> KMTR).
    Alaska offices (2-letter starting with A) use PA prefix.
    Hawaii offices use PH prefix.
    Puerto Rico/Virgin Islands (SJU) use TJ prefix.
    """
    if cwa in ("SJU",):
        return f"TJ{cwa}"
    if cwa.startswith("A") and len(cwa) == 3:
        # Alaska WFOs: AFG, AJK, AER -> PAFG, PAJK, PAER
        return f"PA{cwa}"
    if cwa in ("HFO",):
        return f"PH{cwa}"
    # CONUS default
    return f"K{cwa}"


_AFD_MIN_LENGTH = 100  # Real AFDs are much longer; short responses are errors


def _fetch_afd(cwa_icao: str, session: requests.Session) -> str | None:
    """Fetch a single AFD for the given ICAO CWA identifier."""
    url = AWC_AFD_URL.format(cwa=cwa_icao)
    try:
        resp = session.get(url, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        text = resp.text.strip()
        if not text or len(text) < _AFD_MIN_LENGTH:
            logger.warning("AFD response too short for %s (%d chars), ignoring", cwa_icao, len(text))
            return None
        return text
    except requests.RequestException:
        logger.warning("Failed to fetch AFD for %s", cwa_icao, exc_info=True)
        return None


def fetch_nws_afd(
    waypoints: list,
    cache_dir: Path | None = None,
) -> NWSTextForecasts:
    """Fetch NWS Area Forecast Discussions for route waypoints.

    For each waypoint, looks up the responsible WFO (Weather Forecast Office),
    de-duplicates CWAs, and fetches the full AFD for each unique office.

    Args:
        waypoints: List of Waypoint objects (must have .icao, .lat, .lon).
        cache_dir: Optional directory for caching ICAO->CWA mappings.

    Returns:
        NWSTextForecasts with afd_texts keyed by CWA code.
    """
    wfo_cache = _load_wfo_cache(cache_dir)
    cache_dirty = False

    session = requests.Session()
    session.headers["User-Agent"] = _USER_AGENT

    # Resolve CWA for each waypoint, de-duplicate
    cwa_set: dict[str, str] = {}  # CWA -> ICAO CWA prefix
    for wp in waypoints:
        # Check cache first
        cwa = wfo_cache.get(wp.icao)
        if cwa is None:
            cwa = _lookup_wfo(wp.lat, wp.lon, session)
            if cwa is not None:
                wfo_cache[wp.icao] = cwa
                cache_dirty = True
        if cwa is not None and cwa not in cwa_set:
            cwa_set[cwa] = _cwa_to_icao_prefix(cwa)

    if cache_dirty:
        _save_wfo_cache(cache_dir, wfo_cache)

    # Fetch AFD for each unique CWA
    afd_texts: dict[str, str] = {}
    for cwa, cwa_icao in cwa_set.items():
        text = _fetch_afd(cwa_icao, session)
        if text is not None:
            afd_texts[cwa] = text

    return NWSTextForecasts(
        afd_texts=afd_texts,
        fetched_at=datetime.now(timezone.utc),
    )
