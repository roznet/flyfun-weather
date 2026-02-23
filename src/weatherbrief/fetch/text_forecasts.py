"""Unified text forecast container, region detection, and dispatcher.

Routes through US airspace get NWS Area Forecast Discussions.
Routes through Europe get DWD synoptic overviews.
Region is determined by the destination (last waypoint) ICAO prefix.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from weatherbrief.models import RouteConfig

logger = logging.getLogger(__name__)


class ForecastRegion(str, Enum):
    US = "us"
    EUROPE = "europe"


class TextForecastEntry(BaseModel):
    """A single labelled text forecast block."""

    label: str  # e.g. "KMTR (San Francisco Bay)" or "Kurzfrist (short-range)"
    text: str


class TextForecasts(BaseModel):
    """Unified container for regional text forecasts."""

    region: ForecastRegion
    source_label: str  # "NWS AFD" or "DWD Synoptic Overview"
    language_note: str  # "English" or "German — translate relevant content"
    entries: list[TextForecastEntry]
    fetched_at: datetime


def detect_region(route: RouteConfig | None) -> ForecastRegion:
    """Detect the forecast region from the destination ICAO prefix.

    K-prefix and P-prefix (Alaska, Hawaii) → US.
    Everything else → Europe (default).
    """
    if route is None or not route.waypoints:
        return ForecastRegion.EUROPE

    destination = route.waypoints[-1]
    icao = destination.icao.upper()

    if icao.startswith("K") or icao.startswith("P"):
        return ForecastRegion.US

    return ForecastRegion.EUROPE


def fetch_text_forecasts(
    route: RouteConfig | None = None,
    cache_dir: Path | None = None,
) -> TextForecasts | None:
    """Fetch text forecasts appropriate for the route region.

    Returns None if all fetches fail.
    """
    region = detect_region(route)

    if region == ForecastRegion.US:
        return _fetch_us_text(route, cache_dir)
    else:
        return _fetch_eu_text()


def _fetch_us_text(
    route: RouteConfig,
    cache_dir: Path | None,
) -> TextForecasts | None:
    """Fetch NWS AFDs for US routes."""
    from weatherbrief.fetch.nws_text import fetch_nws_afd

    try:
        result = fetch_nws_afd(route.waypoints, cache_dir=cache_dir)
    except Exception:
        logger.warning("NWS AFD fetch failed", exc_info=True)
        return None

    if not result.afd_texts:
        return None

    entries = [
        TextForecastEntry(label=f"K{cwa} ({cwa})", text=text)
        for cwa, text in result.afd_texts.items()
    ]

    return TextForecasts(
        region=ForecastRegion.US,
        source_label="NWS AFD",
        language_note="English",
        entries=entries,
        fetched_at=result.fetched_at,
    )


def _fetch_eu_text() -> TextForecasts | None:
    """Fetch DWD synoptic overviews for European routes."""
    from weatherbrief.fetch.dwd_text import fetch_dwd_text_forecasts

    try:
        result = fetch_dwd_text_forecasts()
    except Exception:
        logger.warning("DWD text forecast fetch failed", exc_info=True)
        return None

    entries: list[TextForecastEntry] = []
    if result.medium_range:
        entries.append(
            TextForecastEntry(label="Mittelfrist (medium-range)", text=result.medium_range)
        )
    if result.short_range:
        entries.append(
            TextForecastEntry(label="Kurzfrist (short-range)", text=result.short_range)
        )

    if not entries:
        return None

    return TextForecasts(
        region=ForecastRegion.EUROPE,
        source_label="DWD Synoptic Overview",
        language_note="German — translate relevant content",
        entries=entries,
        fetched_at=result.fetched_at,
    )
