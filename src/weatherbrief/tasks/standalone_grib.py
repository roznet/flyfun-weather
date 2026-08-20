"""GRIB cloud diagnostics adapter for standalone verification.

Thin wrapper around existing GRIB fetch/decode to extract ceiling and cloud base
at airport coordinates. Reuses the shared GRIB cache — no duplicate downloads.

Decode goes through the shared pool by cache path, same as the briefing
path (``_fetch_cloud_diag_for_fhour``) — never in this process. The per-hour
decodes are collected and fanned out via ``_dispatch_decode_parallel`` (#459)
so pool workers overlap instead of one blocking dispatch at a time. cfgrib/xarray
decode of full-domain grids in the orchestrating process was a parent-RSS
contributor before issue #236 (the dispatcher exists precisely to keep that
out of the parent). The subprocess cycle runs its own small pool since #448
PR B (``GRIB_DECODE_WORKERS`` = ``STANDALONE_ANALYSIS_WORKERS``, default 2;
``0`` restores inline dispatch, which is fine — that process is disposable).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import requests

from weatherbrief.fetch.grib.cache import cache_dir_for_run, cache_key, is_cached, put_cached

if TYPE_CHECKING:
    from weatherbrief.fetch.grib import DecodePriority

logger = logging.getLogger(__name__)


@dataclass
class AirportGribDiagnostics:
    """GRIB diagnostics extracted for one airport at one forecast hour.

    Carries the subset of :class:`NWPCloudDiagnostics` that the standalone
    snapshot persists. It used to hold ceiling and cloud base *only*, which
    made it the narrowest point in the pipeline: the GFS and ICON cloud-diag
    decode already produced convective cover, base, top, precipitation rate and
    mixed-layer CAPE/CIN, and every one of them was thrown away here — after
    being downloaded and decoded (#565/#566).
    """

    nwp_ceiling_ft: float | None = None
    cloud_base_ft: float | None = None
    convective_cover_pct: float | None = None
    convective_base_ft: float | None = None
    convective_top_ft: float | None = None
    convective_precip_mm_h: float | None = None
    ml_cape_jkg: float | None = None
    ml_cin_jkg: float | None = None


#: Historical name. The class outgrew "ceiling" once it started carrying the
#: convective ingredients, but it is referenced widely enough that renaming in
#: place is the smaller change than a sweep.
AirportCeilingData = AirportGribDiagnostics


def _diagnostics_from(diag) -> AirportGribDiagnostics:
    """Project an ``NWPCloudDiagnostics`` onto the persisted subset.

    Shared by the GFS and ICON fetchers so the two cannot drift — they build
    their diagnostics through different decoders but persist the same fields.
    """
    return AirportGribDiagnostics(
        nwp_ceiling_ft=diag.ceiling_ft,
        # Lowest available cloud base (low layer).
        cloud_base_ft=diag.low.base_ft if diag.low else None,
        convective_cover_pct=diag.convective_cover_pct,
        convective_base_ft=diag.convective_base_ft,
        convective_top_ft=diag.convective_top_ft,
        convective_precip_mm_h=diag.convective_precip_mm_h,
        ml_cape_jkg=diag.ml_cape_jkg,
        ml_cin_jkg=diag.ml_cin_jkg,
    )


def fetch_gfs_cloud_diag(
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    lats: list[float],
    lons: list[float],
    data_dir: Path | None = None,
    session: requests.Session | None = None,
    priority: "int | DecodePriority | None" = None,
) -> dict[int, list[AirportGribDiagnostics]]:
    """Fetch GFS cloud diagnostics for airports at specified forecast hours.

    Args:
        priority: Decode priority for the dispatched cloud-diag jobs. ``None``
            (default) falls through to the decode-priority ContextVar via
            ``_resolve_priority`` — so an interactive briefing's alternates
            decode inherits INTERACTIVE. The standalone verification cycle
            passes ``DecodePriority.BACKGROUND`` explicitly.

    Returns:
        Dict mapping forecast_hour → list of AirportCeilingData (same order as lats/lons).
        Missing hours are omitted from the dict.
    """
    from weatherbrief.fetch.grib import _dispatch_decode_parallel
    from weatherbrief.fetch.grib.decode import build_cloud_diagnostics
    from weatherbrief.fetch.grib.gfs_idx import plan_cloud_diag_byte_ranges
    from weatherbrief.fetch.grib.grib_fetch import fetch_cloud_diag_ranges, fetch_idx

    if data_dir is None:
        data_dir = Path(os.environ.get("DATA_DIR", "data"))
    if session is None:
        session = requests.Session()

    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="gfs")
    result: dict[int, list[AirportGribDiagnostics]] = {}

    # Phase 1: ensure each hour is cached (sequential network I/O), collecting
    # the decode jobs to fan out. Decode is GIL-bound and was walked one
    # blocking dispatch per hour (#459); batching lets the pool workers actually
    # overlap. No dedicated concurrency cap — this leg is light (single field).
    decode_jobs: list[tuple[str, tuple]] = []
    job_fhours: list[int] = []
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

        decode_jobs.append(
            ("decode_gfs_cloud_diag", (str(run_dir / ck), lats, lons)),
        )
        job_fhours.append(fhour)

    if not decode_jobs:
        return result

    # Phase 2: fan the decodes out through the pool. TOCTOU: the decode worker
    # re-reads the path, so a TTL expiry between is_cached and decode surfaces
    # as a per-job exception here and skips that fhour (snapshot just lacks
    # nwp_ceiling — same graceful degradation as any decode failure). TTLs are
    # 12-24h, the window is milliseconds, and the briefing path
    # (_fetch_cloud_diag_for_fhour) accepts the identical race.
    decoded_all = _dispatch_decode_parallel(
        decode_jobs, priority=priority, return_exceptions=True,
    )

    for fhour, decoded in zip(job_fhours, decoded_all):
        if isinstance(decoded, Exception):
            logger.warning(
                "GFS cloud diag decode failed f%03d", fhour, exc_info=decoded,
            )
            continue
        if not decoded:
            continue

        airport_data: list[AirportGribDiagnostics] = []
        for raw in decoded:
            diag = build_cloud_diagnostics(raw)
            if diag is None:
                airport_data.append(AirportGribDiagnostics())
            else:
                airport_data.append(_diagnostics_from(diag))
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
    priority: "int | DecodePriority | None" = None,
) -> dict[int, list[AirportGribDiagnostics]]:
    """Fetch ICON-EU cloud diagnostics for airports at specified forecast hours.

    Same interface as fetch_gfs_cloud_diag (incl. the ``priority`` pass-through)
    but uses DWD ICON-EU data source.
    """
    from weatherbrief.fetch.grib import _dispatch_decode_parallel
    from weatherbrief.fetch.grib.decode import build_icon_cloud_diagnostics
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU_CLOUD_DIAG_CACHE_KEY,
        fetch_icon_eu_single_level,
    )

    if data_dir is None:
        data_dir = Path(os.environ.get("DATA_DIR", "data"))
    if session is None:
        session = requests.Session()

    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="icon-eu")
    result: dict[int, list[AirportGribDiagnostics]] = {}

    # Phase 1: ensure each hour is cached (sequential network I/O), collecting
    # decode jobs to fan out — see fetch_gfs_cloud_diag for the rationale (#459).
    decode_jobs: list[tuple[str, tuple]] = []
    job_fhours: list[int] = []
    for fhour in forecast_hours:
        ck = cache_key(fhour, ICON_EU_CLOUD_DIAG_CACHE_KEY)

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

        decode_jobs.append(
            ("decode_icon_cloud_diag", (str(run_dir / ck), lats, lons)),
        )
        job_fhours.append(fhour)

    if not decode_jobs:
        return result

    # Phase 2: fan the decodes out through the pool. TOCTOU window accepted —
    # see the GFS branch above.
    decoded_all = _dispatch_decode_parallel(
        decode_jobs, priority=priority, return_exceptions=True,
    )

    for fhour, decoded in zip(job_fhours, decoded_all):
        if isinstance(decoded, Exception):
            logger.warning(
                "ICON cloud diag decode failed f%03d", fhour, exc_info=decoded,
            )
            continue
        if not decoded:
            continue

        airport_data: list[AirportGribDiagnostics] = []
        for raw in decoded:
            diag = build_icon_cloud_diagnostics(raw)
            if diag is None:
                airport_data.append(AirportGribDiagnostics())
            else:
                airport_data.append(_diagnostics_from(diag))
        result[fhour] = airport_data

    return result


def datetime_to_init_parts(dt: datetime) -> tuple[str, int]:
    """Convert a datetime to (init_date_YYYYMMDD, init_hour) tuple."""
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y%m%d"), utc.hour
