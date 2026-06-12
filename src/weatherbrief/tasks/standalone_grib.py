"""GRIB cloud diagnostics adapter for standalone verification.

Thin wrapper around existing GRIB fetch/decode to extract ceiling and cloud base
at airport coordinates. Reuses the shared GRIB cache — no duplicate downloads.

Decode goes through ``_dispatch_decode`` by cache path, same as the briefing
path (``_fetch_cloud_diag_for_fhour``) — never in this process. cfgrib/xarray
decode of full-domain grids in the orchestrating process was a parent-RSS
contributor before issue #236 (the dispatcher exists precisely to keep that
out of the parent). With ``GRIB_DECODE_WORKERS=0`` (the subprocess cycle's
setting) the dispatch runs inline, which is fine — that process is disposable.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from weatherbrief.fetch.grib.cache import cache_dir_for_run, cache_key, is_cached, put_cached

logger = logging.getLogger(__name__)


@dataclass
class AirportCeilingData:
    """Ceiling and cloud base extracted from GRIB for one airport at one hour."""

    nwp_ceiling_ft: float | None = None
    cloud_base_ft: float | None = None


def fetch_gfs_cloud_diag(
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    lats: list[float],
    lons: list[float],
    data_dir: Path | None = None,
    session: requests.Session | None = None,
) -> dict[int, list[AirportCeilingData]]:
    """Fetch GFS cloud diagnostics for airports at specified forecast hours.

    Returns:
        Dict mapping forecast_hour → list of AirportCeilingData (same order as lats/lons).
        Missing hours are omitted from the dict.
    """
    from weatherbrief.fetch.grib import DecodePriority, _dispatch_decode
    from weatherbrief.fetch.grib.decode import build_cloud_diagnostics
    from weatherbrief.fetch.grib.gfs_idx import plan_cloud_diag_byte_ranges
    from weatherbrief.fetch.grib.grib_fetch import fetch_cloud_diag_ranges, fetch_idx

    if data_dir is None:
        data_dir = Path(os.environ.get("DATA_DIR", "data"))
    if session is None:
        session = requests.Session()

    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="gfs")
    result: dict[int, list[AirportCeilingData]] = {}

    for fhour in forecast_hours:
        ck = cache_key(fhour, "CLOUD_DIAG")

        if not is_cached(run_dir, ck):
            try:
                idx_text = fetch_idx(init_date, init_hour, fhour, session=session)
                ranges = plan_cloud_diag_byte_ranges(idx_text)
                if not ranges:
                    continue
                grib_bytes = fetch_cloud_diag_ranges(
                    init_date, init_hour, fhour, ranges, session=session,
                )
                put_cached(run_dir, ck, grib_bytes)
                del grib_bytes
            except Exception:
                logger.warning("GFS cloud diag fetch failed f%03d", fhour, exc_info=True)
                continue

        # TOCTOU: the decode worker re-reads the path, so a TTL expiry
        # between is_cached and dispatch surfaces as FileNotFoundError here
        # and skips this fhour (snapshot just lacks nwp_ceiling — same
        # graceful degradation as any decode failure). TTLs are 12-24h, the
        # window is milliseconds, and the briefing path
        # (_fetch_cloud_diag_for_fhour) accepts the identical race.
        try:
            decoded = _dispatch_decode(
                "decode_gfs_cloud_diag", str(run_dir / ck), lats, lons,
                priority=DecodePriority.BACKGROUND,
            )
        except Exception:
            logger.warning("GFS cloud diag decode failed f%03d", fhour, exc_info=True)
            continue

        if not decoded:
            continue

        airport_data: list[AirportCeilingData] = []
        for raw in decoded:
            diag = build_cloud_diagnostics(raw)
            if diag is None:
                airport_data.append(AirportCeilingData())
            else:
                # cloud_base_ft: use lowest available cloud base (low layer)
                cloud_base = diag.low.base_ft if diag.low else None
                airport_data.append(AirportCeilingData(
                    nwp_ceiling_ft=diag.ceiling_ft,
                    cloud_base_ft=cloud_base,
                ))
        result[fhour] = airport_data

    return result


def fetch_icon_cloud_diag(
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    lats: list[float],
    lons: list[float],
    data_dir: Path | None = None,
    session: requests.Session | None = None,
) -> dict[int, list[AirportCeilingData]]:
    """Fetch ICON-EU cloud diagnostics for airports at specified forecast hours.

    Same interface as fetch_gfs_cloud_diag but uses DWD ICON-EU data source.
    """
    from weatherbrief.fetch.grib import DecodePriority, _dispatch_decode
    from weatherbrief.fetch.grib.decode import build_icon_cloud_diagnostics
    from weatherbrief.fetch.grib.icon_eu_fetch import fetch_icon_eu_single_level

    if data_dir is None:
        data_dir = Path(os.environ.get("DATA_DIR", "data"))
    if session is None:
        session = requests.Session()

    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="icon-eu")
    result: dict[int, list[AirportCeilingData]] = {}

    for fhour in forecast_hours:
        ck = cache_key(fhour, "ICON_EU_CLOUD_DIAG")

        if not is_cached(run_dir, ck):
            try:
                fetched = fetch_icon_eu_single_level(
                    init_date, init_hour, [fhour], session=session,
                )
                grib_bytes = fetched.get(fhour)
                if not grib_bytes:
                    continue
                put_cached(run_dir, ck, grib_bytes)
                del grib_bytes
            except Exception:
                logger.warning("ICON cloud diag fetch failed f%03d", fhour, exc_info=True)
                continue

        # TOCTOU window accepted — see the GFS branch above.
        try:
            decoded = _dispatch_decode(
                "decode_icon_cloud_diag", str(run_dir / ck), lats, lons,
                priority=DecodePriority.BACKGROUND,
            )
        except Exception:
            logger.warning("ICON cloud diag decode failed f%03d", fhour, exc_info=True)
            continue

        if not decoded:
            continue

        airport_data: list[AirportCeilingData] = []
        for raw in decoded:
            diag = build_icon_cloud_diagnostics(raw)
            if diag is None:
                airport_data.append(AirportCeilingData())
            else:
                cloud_base = diag.low.base_ft if diag.low else None
                airport_data.append(AirportCeilingData(
                    nwp_ceiling_ft=diag.ceiling_ft,
                    cloud_base_ft=cloud_base,
                ))
        result[fhour] = airport_data

    return result


def datetime_to_init_parts(dt: datetime) -> tuple[str, int]:
    """Convert a datetime to (init_date_YYYYMMDD, init_hour) tuple."""
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y%m%d"), utc.hour
