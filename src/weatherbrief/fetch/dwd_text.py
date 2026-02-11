"""DWD Open Data text forecast client.

Fetches synoptic overviews from https://opendata.dwd.de/weather/text_forecasts/txt/:
- SXDL31 (Synoptische Übersicht Kurzfrist) — short-range (2-3 day), updated 2x daily
- SXDL33 (Synoptische Übersicht Mittelfrist) — medium-range (7-day), updated daily ~10:30 UTC
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests
from pydantic import BaseModel

logger = logging.getLogger(__name__)

DWD_BASE_URL = "https://opendata.dwd.de/weather/text_forecasts/txt"

# DWD filenames for latest synoptic overviews
_SXDL31_PATH = "SXDL31_DWAV_LATEST"  # Kurzfrist (short-range)
_SXDL33_PATH = "SXDL33_DWAV_LATEST"  # Mittelfrist (medium-range)

_TIMEOUT_SECONDS = 15


class DWDTextForecasts(BaseModel):
    """Container for fetched DWD text forecasts."""

    short_range: str | None = None  # SXDL31 - Kurzfrist
    medium_range: str | None = None  # SXDL33 - Mittelfrist
    fetched_at: datetime


def _fetch_text(url: str) -> str | None:
    """Fetch a single text forecast, returning None on failure."""
    try:
        resp = requests.get(url, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        # DWD files are latin-1 encoded
        resp.encoding = "latin-1"
        return resp.text
    except (requests.RequestException, ConnectionError):
        logger.warning("Failed to fetch DWD forecast from %s", url, exc_info=True)
        return None


def fetch_dwd_text_forecasts() -> DWDTextForecasts:
    """Fetch latest DWD synoptic overviews.

    Gracefully handles failures — if DWD is unreachable, text fields are None.
    """
    short_range = _fetch_text(f"{DWD_BASE_URL}/{_SXDL31_PATH}")
    medium_range = _fetch_text(f"{DWD_BASE_URL}/{_SXDL33_PATH}")

    return DWDTextForecasts(
        short_range=short_range,
        medium_range=medium_range,
        fetched_at=datetime.now(timezone.utc),
    )
