"""NOAA HRRR CONUS GRIB2 access via AWS S3 (issue #457).

HRRR (High-Resolution Rapid Refresh, 3 km, convection-allowing,
radar-assimilating) upgrades the ``gfs`` model slot in place when the whole
route fits the CONUS domain and the flight window is within the selected
run's horizon — the short-range quality upgrade for US routes, same product
decision as the ICON-D2 upgrade of the icon slot (#456). Unlike the GFS
patch-onto-Open-Meteo style, HRRR does a FULL sounding replacement
(ICON/ECMWF pattern): a genuinely different model (WRF-ARW regional vs GFS
FV3 global), so the upgrade is visibly badged ``GFS (HRRR)``.

Data layout (verified against the live bucket):
    https://noaa-hrrr-bdp-pds.s3.amazonaws.com/
        hrrr.{YYYYMMDD}/conus/hrrr.t{HH}z.wrfprsf{FF}.grib2      (FF is 2-digit)
        ... + companion .idx files, same format as GFS.

Everything needed lives in the single ``wrfprs`` file per forecast hour:
the full sounding on 40 pressure levels at 25 hPa spacing (TMP, DPT direct,
RH, UGRD/VGRD, VVEL already Pa/s, HGT direct), microphysics (CLMR +
``CIMIXR`` — HRRR's name for ice mixing ratio, NOT ``ICMR``), and
instantaneous cloud diagnostics (band covers, ceiling, cloud base) plus
mixed-layer CAPE/CIN.

Cycles run HOURLY, but only 00/06/12/18z extend to 48 h — the other cycles
stop at 18 h. Run selection prefers the freshest cycle whose horizon covers
the flight window, which usually means an extended cycle for anything beyond
a few hours out.

The grid is Lambert-projected (not a lat/lon rectangle), so the domain gate
transforms route points into grid x/y with the same pyproj projection the
decoder uses and checks they land within grid bounds — exact and free once
the projection exists. Grid constants below are from the live GRIB header
(2026-07-26 12z) and are stable product properties; the decode path reads
them from each file's own attributes, so a grid change degrades to a decode
miss, never to wrong positions.

Out of scope (#457): HRRR-Alaska, sub-hourly output, HRRR ensemble. RRFS
(NOAA's successor model) should be mostly a bucket/path + constants swap
within this module.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

from weatherbrief.fetch.grib.gfs_idx import (
    ByteRange,
    CloudDiagByteRange,
    plan_byte_ranges,
    plan_cloud_diag_byte_ranges,
)
from weatherbrief.fetch.grib.grib_fetch import MAX_DOWNLOAD_WORKERS

logger = logging.getLogger(__name__)

HRRR_S3_BASE_URL = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"

# Full wrfprs file set completes ~45–60 min after init; 75 min with margin.
# The run-finder HEAD-probes the actual last-needed forecast hour anyway, so
# a small miss just walks back one cycle.
HRRR_PUBLISH_DELAY_HOURS = 1.25

# Only the synoptic cycles extend to 48 h; every other hourly cycle stops at 18 h.
HRRR_EXTENDED_CYCLES = frozenset({0, 6, 12, 18})
HRRR_HORIZON_EXTENDED_H = 48
HRRR_HORIZON_SHORT_H = 18

# Sounding variables in the .idx (wgrib2 names). DPT and HGT are direct — no
# Magnus derivation, no hypsometric fallback needed. VVEL is already Pa/s (no
# omega conversion). CIMIXR is HRRR's name for ice mixing ratio (the CLMR/CLWMR
# style naming quirk the decode map also handles). Bonus species (RWMR, SNMR,
# GRLE) are deliberately not fetched — no consumer yet.
#
# Cloud liquid water: the live bucket's .idx uses ``CLMR`` (verified
# 2026-07-26 12z: 40 CLMR entries, zero CLWMR — see the committed
# tests/fixtures/hrrr_idx_excerpt.txt), but NCO's inventory page documents
# the field as ``CLWMR``, so BOTH spellings are planned defensively; the
# decode map already accepts either. Whichever the product publishes is
# fetched, a rename upstream cannot silently drop the condensate field
# (PR #508 review).
HRRR_SOUNDING_VARIABLES = frozenset(
    {"TMP", "DPT", "RH", "UGRD", "VGRD", "VVEL", "HGT", "CLMR", "CLWMR", "CIMIXR"}
)

# Skip 50–125 hPa: above the GA envelope and above the 150 hPa top the
# Open-Meteo GFS sounding already uses. Expressed as a floor rather than an
# explicit level list so a level-set change upstream degrades gracefully.
HRRR_SOUNDING_MIN_LEVEL_HPA = 150

# Cloud diagnostics + convective indices, all instantaneous ("N hour fcst" —
# HRRR publishes no averaged forms, so none of the GFS averaged-window
# machinery applies). Level strings verified against the live .idx:
# - band covers + total cover (entire atmosphere)
# - HGT at cloud ceiling / overall cloud base (per-band base/top/temp geometry
#   does NOT exist in HRRR — the diagnostics shape is closer to ECMWF's than
#   GFS's). ``HGT:cloud top`` / ``PRES:cloud base|top`` exist upstream but have
#   no NWPCloudDiagnostics home, so they are not fetched.
# - CAPE/CIN: only the 90-0 mb mixed layer (≈ ECMWF's lowest-100-hPa mixed
#   layer feeding ml_cape/ml_cin). Fetching the other layers (surface, 180-0,
#   255-0 mb) would decode into the SAME cfgrib (cape, pressureFromGroundLayer)
#   key and make the decode ambiguous.
HRRR_CLOUD_DIAG_VARIABLES: dict[str, set[str]] = {
    "LCDC": {"low cloud layer"},
    "MCDC": {"middle cloud layer"},
    "HCDC": {"high cloud layer"},
    "TCDC": {"entire atmosphere"},
    "HGT": {"cloud ceiling", "cloud base"},
    "CAPE": {"90-0 mb above ground"},
    "CIN": {"90-0 mb above ground"},
}

_HRRR_CLOUD_DIAG_PAIRS: set[tuple[str, str]] = {
    (var, lev)
    for var, levels in HRRR_CLOUD_DIAG_VARIABLES.items()
    for lev in levels
}

_REQUEST_TIMEOUT = 60  # seconds


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def hrrr_grib2_url(init_date: str, init_hour: int, forecast_hour: int) -> str:
    """Build the S3 URL for an HRRR CONUS pressure-file (wrfprs).

    Note the 2-digit forecast hour — GFS uses 3 digits.
    """
    return (
        f"{HRRR_S3_BASE_URL}/hrrr.{init_date}/conus/"
        f"hrrr.t{init_hour:02d}z.wrfprsf{forecast_hour:02d}.grib2"
    )


def hrrr_idx_url(init_date: str, init_hour: int, forecast_hour: int) -> str:
    """Build the S3 URL for an HRRR .idx file."""
    return hrrr_grib2_url(init_date, init_hour, forecast_hour) + ".idx"


# ---------------------------------------------------------------------------
# CONUS domain gate (Lambert grid)
# ---------------------------------------------------------------------------

# HRRR CONUS grid definition, read off the live GRIB header (2026-07-26 12z).
# Lambert conformal, tangent at 38.5°N, central meridian 262.5°E, on the
# GRIB2 spherical earth (shapeOfTheEarth=6, R=6371229 m — cfgrib does not
# expose the radius as an attribute, so the constant is pinned here).
HRRR_GRID_NX = 1799
HRRR_GRID_NY = 1059
HRRR_GRID_DX_M = 3000.0
HRRR_GRID_DY_M = 3000.0
HRRR_GRID_LAT_FIRST = 21.138123
HRRR_GRID_LON_FIRST = 237.280472
HRRR_GRID_LAD = 38.5
HRRR_GRID_LOV = 262.5
HRRR_GRID_LATIN1 = 38.5
HRRR_GRID_LATIN2 = 38.5
HRRR_EARTH_RADIUS_M = 6371229.0


def _hrrr_projection():
    """The HRRR CONUS Lambert projection (pyproj, lazy import)."""
    import pyproj

    return pyproj.Proj(
        proj="lcc",
        lat_1=HRRR_GRID_LATIN1,
        lat_2=HRRR_GRID_LATIN2,
        lat_0=HRRR_GRID_LAD,
        lon_0=HRRR_GRID_LOV,
        R=HRRR_EARTH_RADIUS_M,
    )


def hrrr_grid_fractional_indices(
    latitudes: list[float],
    longitudes: list[float],
) -> "tuple[list[float], list[float]]":
    """Transform lat/lon points into fractional HRRR grid indices (fx, fy).

    ``fx`` runs along the x (west→east) axis 0..Nx-1, ``fy`` along y
    (south→north) 0..Ny-1; values outside those ranges are off-grid. Same
    math as the decode-time interpolation, so the domain gate and the
    decoder can never disagree about what "inside CONUS" means.
    """
    proj = _hrrr_projection()
    x0, y0 = proj(HRRR_GRID_LON_FIRST, HRRR_GRID_LAT_FIRST)
    xs, ys = proj(longitudes, latitudes)
    try:
        fx = [(x - x0) / HRRR_GRID_DX_M for x in xs]
        fy = [(y - y0) / HRRR_GRID_DY_M for y in ys]
    except TypeError:
        # Single scalar in, single scalar out (pyproj collapses 1-lists).
        fx = [(xs - x0) / HRRR_GRID_DX_M]
        fy = [(ys - y0) / HRRR_GRID_DY_M]
    return fx, fy


def route_in_hrrr_domain(route_points: list) -> bool:
    """Check whether ALL route points fall inside the HRRR CONUS grid.

    All-or-nothing (#457 gate, same product rule as ICON-D2): one point
    outside means the whole gfs slot stays on plain GFS — never a mixed
    HRRR/GFS briefing.
    """
    if not route_points:
        return False
    fx, fy = hrrr_grid_fractional_indices(
        [rp.lat for rp in route_points], [rp.lon for rp in route_points],
    )
    return all(
        0.0 <= x <= HRRR_GRID_NX - 1 and 0.0 <= y <= HRRR_GRID_NY - 1
        for x, y in zip(fx, fy)
    )


# ---------------------------------------------------------------------------
# Run selection (hourly cycles, per-cycle horizon)
# ---------------------------------------------------------------------------


def hrrr_run_horizon_hours(init_hour: int) -> int:
    """Forecast horizon for the cycle starting at ``init_hour`` UTC."""
    if init_hour in HRRR_EXTENDED_CYCLES:
        return HRRR_HORIZON_EXTENDED_H
    return HRRR_HORIZON_SHORT_H


def _last_needed_fhour(init_time: datetime, need_until: datetime) -> int:
    """Last forecast hour the run must have published to cover ``need_until``."""
    delta_h = (need_until - init_time).total_seconds() / 3600.0
    return max(0, math.ceil(delta_h))


def find_latest_hrrr_run(
    target_time: datetime,
    session: requests.Session | None = None,
    as_of_time: datetime | None = None,
    cover_until: datetime | None = None,
    extended_cycles_only: bool = False,
) -> tuple[str, int] | None:
    """Find the latest HRRR run covering the flight window.

    Thin wrapper around :func:`find_latest_hrrr_run_with_response` for
    callers that don't need the HEAD response.
    """
    found = find_latest_hrrr_run_with_response(
        target_time, session, as_of_time, cover_until, extended_cycles_only,
    )
    return (found[0], found[1]) if found is not None else None


def find_latest_hrrr_run_with_response(
    target_time: datetime,
    session: requests.Session | None = None,
    as_of_time: datetime | None = None,
    cover_until: datetime | None = None,
    extended_cycles_only: bool = False,
) -> tuple[str, int, requests.Response] | None:
    """Find the latest HRRR run + return the matching probe HEAD response.

    Walks hourly cycles in reverse chronological order, skipping cycles that
    are not yet expected published or whose horizon (48 h extended /
    18 h otherwise) doesn't reach ``cover_until``. The publication probe
    HEADs the .idx of the LAST forecast hour the flight needs — HRRR
    publishes forecast hours progressively over the delivery window, so
    probing f00 could select a run whose later hours aren't up yet.

    Args:
        target_time: Flight departure time.
        session: Optional requests session.
        as_of_time: If set, only consider runs initialized before this time
            (historical "as-of" briefings). Uses ``now`` if None.
        cover_until: Latest time the run must cover (typically departure +
            flight duration). Defaults to ``target_time``.
        extended_cycles_only: Only consider 00/06/12/18z cycles. Used by the
            freshness readiness check so staleness tracks the 6-hourly
            extended cadence rather than flagging every hourly cycle (which
            would auto-refresh short-lead US flights every hour).

    Returns:
        ``(init_date_YYYYMMDD, init_hour, head_response)`` or ``None``.
    """
    sess = session or requests.Session()
    reference_time = as_of_time or datetime.now(timezone.utc)
    need_until = cover_until or target_time

    start = reference_time.replace(minute=0, second=0, microsecond=0)
    # 2 days of hourly cycles is enough: even the longest-horizon flight a
    # publishable run can cover was initialized < 48 h ago.
    for hours_back in range(48):
        init_time = start - timedelta(hours=hours_back)
        cycle = init_time.hour
        if extended_cycles_only and cycle not in HRRR_EXTENDED_CYCLES:
            continue
        hours_since_init = (reference_time - init_time).total_seconds() / 3600
        if hours_since_init < HRRR_PUBLISH_DELAY_HOURS:
            continue

        horizon_h = hrrr_run_horizon_hours(cycle)
        if init_time + timedelta(hours=horizon_h) < need_until:
            continue

        probe_fhour = min(_last_needed_fhour(init_time, need_until), horizon_h)
        date_str = init_time.strftime("%Y%m%d")
        try:
            resp = sess.head(
                hrrr_idx_url(date_str, cycle, probe_fhour), timeout=10,
            )
            if resp.status_code == 200:
                logger.info(
                    "Found HRRR run: %s %02dz (horizon %dh, probed f%02d)",
                    date_str, cycle, horizon_h, probe_fhour,
                )
                return date_str, cycle, resp
        except requests.RequestException:
            continue

    return None


def hrrr_window_out_of_range(
    target_time: datetime,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
) -> bool:
    """True when no publishable HRRR run has enough horizon for the flight.

    Deterministic (no network): mirrors the cycle/delay walk of
    :func:`find_latest_hrrr_run_with_response`. Distinguishes the expected
    "flight beyond the 48 h horizon" skip from a genuine probe failure.
    """
    reference_time = as_of_time or datetime.now(timezone.utc)
    need_until = target_time + timedelta(hours=flight_duration_hours)
    start = reference_time.replace(minute=0, second=0, microsecond=0)
    for hours_back in range(48):
        init_time = start - timedelta(hours=hours_back)
        hours_since_init = (reference_time - init_time).total_seconds() / 3600
        if hours_since_init < HRRR_PUBLISH_DELAY_HOURS:
            continue
        horizon_h = hrrr_run_horizon_hours(init_time.hour)
        if init_time + timedelta(hours=horizon_h) >= need_until:
            return False
    return True


def compute_hrrr_flight_window_hours(
    init_date: str,
    init_hour: int,
    departure_time: datetime,
    flight_duration_hours: float,
) -> list[int]:
    """Compute HRRR forecast hours covering a flight window.

    HRRR output is hourly across the whole horizon, so the covering set is
    simply the CONTIGUOUS inclusive range ``floor(departure) .. ceil(end)``
    in hours-from-init, clamped to ``[0, horizon]``.

    Deliberately NOT the sample-and-round shape the GFS/ICON helpers use:
    ``round()`` is ties-to-even, so a :30 departure collapses consecutive
    half-hour offsets onto the same even hour and silently drops every other
    forecast hour (PR #508 review) — degrading the headline hourly-3km
    benefit to 2-hourly for one of the most common GA departure slots. A
    contiguous range cannot skip an in-window native hour by construction.
    """
    init_dt = datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc,
    )
    horizon_h = hrrr_run_horizon_hours(init_hour)
    end_dt = departure_time + timedelta(hours=max(flight_duration_hours, 0.0))

    first = math.floor((departure_time - init_dt).total_seconds() / 3600)
    last = math.ceil((end_dt - init_dt).total_seconds() / 3600)
    first = max(0, min(first, horizon_h))
    last = max(first, min(last, horizon_h))
    return list(range(first, last + 1))


# ---------------------------------------------------------------------------
# Idx planning + byte-range fetch
# ---------------------------------------------------------------------------


def fetch_hrrr_idx(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    session: requests.Session | None = None,
) -> str:
    """Download an HRRR .idx file (same format as GFS)."""
    sess = session or requests.Session()
    url = hrrr_idx_url(init_date, init_hour, forecast_hour)
    logger.debug("Fetching HRRR .idx: %s", url)
    resp = sess.get(url, timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def plan_hrrr_sounding_byte_ranges(idx_text: str) -> list[ByteRange]:
    """Byte ranges for the full-sounding replacement set (~190 MB/fhour).

    All :data:`HRRR_SOUNDING_VARIABLES` on every integer pressure level at or
    below (altitude-wise) :data:`HRRR_SOUNDING_MIN_LEVEL_HPA`.
    """
    return plan_byte_ranges(
        idx_text,
        variables=HRRR_SOUNDING_VARIABLES,
        min_level_hpa=HRRR_SOUNDING_MIN_LEVEL_HPA,
    )


def plan_hrrr_cloud_diag_byte_ranges(idx_text: str) -> list[CloudDiagByteRange]:
    """Byte ranges for the cloud-diagnostics + CAPE/CIN set (~10 MB/fhour).

    ``prefer_averaged=set()`` states explicitly that HRRR has no averaged
    forms — leaving the GFS default (which prefers averaged LCDC/MCDC/HCDC)
    would quietly start selecting an averaged message if NCEP ever added
    one, and averaged data must never enter this all-instantaneous path.
    """
    return plan_cloud_diag_byte_ranges(
        idx_text, pairs=_HRRR_CLOUD_DIAG_PAIRS, prefer_averaged=set(),
    )


# Streamed download: ranges are coalesced, then fetched in offset-ordered
# batches of this many HTTP requests. Peak in-flight memory is one batch of
# chunks (~2 MB per coalesced chunk), not the whole ~190 MB forecast hour —
# the accumulate-everything pattern inherited from the (30× smaller) GFS
# payload held ~3 copies transiently (PR #508 review).
_DOWNLOAD_BATCH_REQUESTS = 16


class HrrrIncompleteArtifact(RuntimeError):
    """A mandatory part of an HRRR artifact is missing.

    Raised by the plan validator and the streaming downloader. The HRRR
    sounding is all-or-nothing by construction: it replaces the entire
    pressure column rather than patching fields onto one, so a partially
    downloaded artifact is not a degraded sounding but a corrupt one — and a
    committed one is served from cache for hours. (PR #508 review)
    """


# Fields every retained pressure level must carry for the sounding artifact to
# count as complete. RH and DPT are alternatives (HRRR ships both; the decoder
# prefers DPT and derives from RH otherwise), so they are validated as a pair.
# VVEL is deliberately absent: it is genuinely optional for the analyses that
# consume this column, and that is a policy, not an accident of a failed
# request — the download-side check above guarantees a planned VVEL span that
# fails still kills the artifact.
_HRRR_MANDATORY_LEVEL_VARIABLES = ("TMP", "UGRD", "VGRD", "HGT")
_HRRR_HUMIDITY_VARIABLES = ("RH", "DPT")
# At least one microphysics field per level — the reason HRRR is worth
# fetching at all. CLMR is the operational name, CLWMR the documented one.
_HRRR_MICROPHYSICS_VARIABLES = ("CLMR", "CLWMR", "CIMIXR")


# Raw decoded field names (``_HRRR_FULL_VAR_MAP`` values) that must be present
# on every level of a decoded column. Mirrors the plan check above, one stage
# later: the plan proves the messages were OFFERED, this proves they DECODED.
_HRRR_REQUIRED_DECODED = ("raw_temperature_k", "raw_u_wind_m_s",
                          "raw_v_wind_m_s", "geopotential_height_m")
_HRRR_REQUIRED_DECODED_HUMIDITY = ("raw_dewpoint_k", "raw_relative_humidity_pct")
_HRRR_REQUIRED_DECODED_MICRO = ("cloud_liquid_water_kg_kg", "ice_mixing_ratio_kg_kg")


def hrrr_column_incomplete_reason(
    point_data: "dict[int, dict[str, float]]",
) -> str | None:
    """Why this decoded column is unusable, or None if it is complete.

    ``all(decoded_points)`` only proved each point's dict is non-empty. A
    truncated artifact decodes to a column that is *structurally* fine —
    levels present, heights present — but with ``temperature_c=None`` and
    ``wind_speed_kt=None`` on every level, because
    ``build_pressure_levels_from_grib`` emits whatever converted cleanly.
    Those levels would then REPLACE a complete GFS/Open-Meteo sounding.
    (PR #508 review)
    """
    if not point_data:
        return "no levels decoded"
    for level, fields in sorted(point_data.items(), reverse=True):
        missing = [k for k in _HRRR_REQUIRED_DECODED if fields.get(k) is None]
        if all(fields.get(k) is None for k in _HRRR_REQUIRED_DECODED_HUMIDITY):
            missing.append("dewpoint|rh")
        if all(fields.get(k) is None for k in _HRRR_REQUIRED_DECODED_MICRO):
            missing.append("clw|icmr")
        if missing:
            return f"{level}hPa missing {'+'.join(missing)}"
    return None


def validate_hrrr_sounding_plan(ranges: "list[ByteRange]") -> None:
    """Raise :class:`HrrrIncompleteArtifact` if the planned set is deficient.

    Checked BEFORE spending ~190 MB of downloads: if the ``.idx`` does not
    offer every mandatory variable at every level it offers at all, the
    resulting artifact could never pass column validation, so the hour should
    fail now rather than after the transfer.
    """
    if not ranges:
        raise HrrrIncompleteArtifact("no HRRR sounding messages in the .idx")

    by_level: dict[int, set[str]] = {}
    for r in ranges:
        by_level.setdefault(r.level_hpa, set()).add(r.variable.upper())

    deficient: list[str] = []
    for level, present in sorted(by_level.items(), reverse=True):
        missing = [v for v in _HRRR_MANDATORY_LEVEL_VARIABLES if v not in present]
        if not any(v in present for v in _HRRR_HUMIDITY_VARIABLES):
            missing.append("RH|DPT")
        if not any(v in present for v in _HRRR_MICROPHYSICS_VARIABLES):
            missing.append("CLMR|CLWMR|CIMIXR")
        if missing:
            deficient.append(f"{level}hPa missing {'+'.join(missing)}")
    if deficient:
        raise HrrrIncompleteArtifact(
            "HRRR sounding plan incomplete: " + "; ".join(deficient[:5])
            + (f" (+{len(deficient) - 5} more levels)" if len(deficient) > 5 else "")
        )


def coalesce_ranges(
    ranges: "list[ByteRange] | list[CloudDiagByteRange]",
) -> list[tuple[int, int | None]]:
    """Merge byte-adjacent planned ranges into single HTTP requests.

    Messages for the variables we plan are frequently adjacent in the wrfprs
    file (e.g. CLMR directly followed by CIMIXR at each level), so merging
    ranges whose start is exactly the previous end + 1 cuts the request
    count substantially at zero extra bytes — deliberately NOT bridging
    gaps, which would download unplanned messages. A range with ``end=None``
    (last message, runs to EOF) terminates its merged run.
    """
    spans = sorted((r.start, r.end) for r in ranges)
    out: list[tuple[int, int | None]] = []
    for start, end in spans:
        if out and out[-1][1] is not None and start == out[-1][1] + 1:
            out[-1] = (out[-1][0], end)
        else:
            out.append((start, end))
    return out


def _fetch_span(
    url: str,
    span: tuple[int, int | None],
    session: requests.Session,
) -> bytes | None:
    """Download one coalesced byte span. None on failure (messages skipped)."""
    start, end = span
    range_header = f"bytes={start}-{'' if end is None else end}"
    try:
        resp = session.get(
            url, headers={"Range": range_header}, timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code not in (200, 206):
            logger.warning(
                "Failed to fetch HRRR range %s: HTTP %d", range_header,
                resp.status_code,
            )
            return None
        return resp.content
    except requests.RequestException:
        logger.warning("Request failed for HRRR range %s", range_header, exc_info=True)
        return None


def iter_hrrr_range_chunks(
    url: str,
    spans: list[tuple[int, int | None]],
    session: requests.Session,
):
    """Yield coalesced spans' bytes in offset order, batch-parallel.

    Each batch of ``_DOWNLOAD_BATCH_REQUESTS`` spans downloads concurrently;
    chunks are yielded in offset order so the consumer can stream them
    straight into the concatenated-GRIB2 cache file.

    **A failed span raises** :class:`HrrrIncompleteArtifact` (PR #508 review).
    The GFS fetcher's skip-the-range semantics are safe there because GFS
    enrichment PATCHES fields onto an existing sounding — a missing message
    leaves the Open-Meteo value in place. HRRR REPLACES the whole column, and
    ``put_cached_from_chunks`` commits whenever any bytes arrived, so a
    skipped span produced a truncated file that then served as a valid cache
    hit for the full-sounding key for hours. ``put_cached_from_chunks``
    unlinks its tempfile when the iterator raises, so nothing is committed.
    """
    for i in range(0, len(spans), _DOWNLOAD_BATCH_REQUESTS):
        batch = spans[i:i + _DOWNLOAD_BATCH_REQUESTS]
        with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as pool:
            futures = [pool.submit(_fetch_span, url, span, session) for span in batch]
            for span, future in zip(batch, futures):  # offset order, not completion
                data = future.result()
                if data is None:
                    raise HrrrIncompleteArtifact(
                        f"byte range {span} failed; refusing to commit a "
                        f"partial HRRR artifact from {url}"
                    )
                yield data


def fetch_hrrr_ranges_to_cache(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    ranges: "list[ByteRange] | list[CloudDiagByteRange]",
    run_dir,
    filename: str,
    session: requests.Session | None = None,
    label: str = "sounding",
) -> bool:
    """Download planned ranges for one forecast hour, streamed to the cache.

    Coalesces adjacent ranges, downloads in offset-ordered batches, and
    writes incrementally via :func:`put_cached_from_chunks` — the full
    payload never lives in memory. Logs the instrumentation the production
    cost question needs: planned messages, actual HTTP requests after
    coalescing, and bytes written.

    Returns True when a non-empty cache entry was committed.
    """
    from weatherbrief.fetch.grib.cache import put_cached_from_chunks

    if not ranges:
        return False
    sess = session or requests.Session()
    url = hrrr_grib2_url(init_date, init_hour, forecast_hour)
    spans = coalesce_ranges(ranges)

    path = put_cached_from_chunks(
        run_dir, filename, iter_hrrr_range_chunks(url, spans, sess),
    )
    if path is None:
        logger.warning(
            "HRRR %s f%02d: all %d requests failed, nothing cached",
            label, forecast_hour, len(spans),
        )
        return False
    size = path.stat().st_size
    logger.info(
        "Downloaded HRRR %s f%02d: %d messages in %d requests, %.1f MB",
        label, forecast_hour, len(ranges), len(spans), size / 1e6,
    )
    return True
