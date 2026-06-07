"""DWD hobbymet surface chart cache.

Fetches the analysis + ICON forecast Bodenwetter charts from
``https://www.dwd.de/DWD/wetter/wv_spez/hobbymet/wetterkarten/`` and stores
them in a shared cross-briefing on-disk cache.

Cache layout:

    {data_dir}/dwd_charts/
        2026-05-08T06Z/                # one subdir per ICON run cycle
            ana.png
            036.png 048.png 060.png 084.png 108.png
            meta.json                  # per-chart Last-Modified, ETag, ...
        2026-05-08T12Z/
            ...

Run-cycle key = analysis chart's ``Last-Modified`` rounded DOWN to the
previous synoptic hour (00/06/12/18 UTC). Forecast charts are stored in
the same cycle dir; their individual ``Last-Modified`` is recorded in
``meta.json`` so callers can render an honest "Issued Xh ago" caption.

Shared cache/projection/fetch machinery lives in
:mod:`weatherbrief.fetch.chart_cache`; this module configures a
:class:`~weatherbrief.fetch.chart_cache.ChartCache` for the DWD source and
keeps DWD-specific cycle discovery + a back-compat function surface.
"""

from __future__ import annotations

import email.utils
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

from weatherbrief.fetch.chart_cache import (
    ChartCache,
    ChartCalibration,
    ChartFetchResult,  # re-exported for back-compat
    RefreshReport,  # re-exported for back-compat
    parse_http_datetime,
    parse_run_cycle_dt,  # re-exported for back-compat
)

logger = logging.getLogger(__name__)

DWD_BASE_URL = "https://www.dwd.de/DWD/wetter/wv_spez/hobbymet/wetterkarten"
DWD_PAGE_URL = "https://www.dwd.de/DE/leistungen/hobbymet_wk_europa/hobbyeuropakarten.html"

# Ordered for UI tab presentation. Forecast offsets in hours.
CHART_IDS: tuple[str, ...] = ("ana", "036", "048", "060", "084", "108")
FORECAST_OFFSETS_H: dict[str, int] = {
    "ana": 0,
    "036": 36,
    "048": 48,
    "060": 60,
    "084": 84,
    "108": 108,
}

_FILENAMES: dict[str, str] = {
    "ana": "bwk_bodendruck_na_ana.png",
    "036": "ico_tkboden_na_036.png",
    "048": "ico_tkboden_na_048.png",
    "060": "ico_tkboden_na_060.png",
    "084": "ico_tkboden_na_084.png",
    "108": "ico_tkboden_na_108.png",
}

_TIMEOUT_SECONDS = 30
_DEFAULT_KEEP_CYCLES = 8  # ~2 days at 6h cadence
_USER_AGENT = "flyfun-weather/1.0 (+https://weather.flyfun.aero)"

# Native pixel sizes of each chart, used for client-side SVG overlay scaling.
CHART_NATIVE_SIZE: dict[str, tuple[int, int]] = {
    "analysis": (4389, 3114),
    "icon": (800, 653),
}

# Calibrations for converting WGS84 lon/lat to chart-pixel coordinates.
# Each entry holds the polar-stereographic projection parameters (consumed
# by pyproj) and an 8-coefficient 2D homography fit from gridline
# intersections identified manually on the chart. Originally calibrated
# in src/weatherbrief/frontal/cli.py — moved here so the same math can
# drive both the frontal zone overlay and the briefing route overlay.
#
# Kept as a plain dict (not ChartCalibration objects) so it stays the literal
# source of truth that ``scripts/dump_chart_calibrations.py`` reads to generate
# the TypeScript projection constants.
_CHART_CALIBRATIONS: dict[str, dict[str, object]] = {
    # Max calibration error: 1.2px
    "icon": {
        "proj": {"proj": "stere", "lat_0": 90, "lat_ts": 60, "lon_0": 5},
        "homography": (
            0.00010064544583772085, 8.021406574208543e-06, 505.2081110061641,
            8.455010167934436e-06, -0.00010106842893807126, -114.44765418753545,
            -3.747121446686481e-10, -3.0137722186545355e-10,
        ),
    },
    # Max calibration error: 3.0px
    "analysis": {
        "proj": {"proj": "stere", "lat_0": 90, "lat_ts": 90, "lon_0": 10},
        "homography": (
            0.0005145316041850823, 5.082539130306949e-06, 2793.4808547432303,
            1.1777669359815649e-06, -0.0005125590704322343, -606.7192569143913,
            8.930427981271965e-10, 1.7297125807436028e-09,
        ),
    },
}


def _chart_type_for(chart_id: str) -> str:
    """DWD: the analysis tab uses the high-res ``analysis`` chart; every
    forecast offset uses the ICON ``icon`` chart."""
    return "analysis" if chart_id == "ana" else "icon"


_cache = ChartCache(
    slug="dwd",
    display_name="DWD",
    subdir="dwd_charts",
    extension="png",
    chart_ids=CHART_IDS,
    forecast_offsets_h=FORECAST_OFFSETS_H,
    calibrations={
        ct: ChartCalibration(
            proj=cal["proj"],  # type: ignore[arg-type]
            homography=cal["homography"],  # type: ignore[arg-type]
            native_size=CHART_NATIVE_SIZE[ct],
        )
        for ct, cal in _CHART_CALIBRATIONS.items()
    },
    chart_type_for=_chart_type_for,
    keep_cycles=_DEFAULT_KEEP_CYCLES,
    user_agent=_USER_AGENT,
    timeout=_TIMEOUT_SECONDS,
)


# ---------------------------------------------------------------------------
# DWD-specific cycle discovery
# ---------------------------------------------------------------------------


def parse_run_cycle_from_last_modified(last_modified: str | None) -> str | None:
    """Convert an HTTP ``Last-Modified`` header into a run-cycle key.

    Rounds DOWN to the previous synoptic hour (00/06/12/18 UTC). A 09:42 UTC
    publish belongs to the 06Z run, not the (future) 12Z run.

    Returns None if the header is missing or unparseable.
    """
    dt = parse_http_datetime(last_modified)
    if dt is None:
        return None
    synoptic_hour = (dt.hour // 6) * 6
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}T{synoptic_hour:02d}Z"


# ---------------------------------------------------------------------------
# Back-compat function surface (delegates to the shared ChartCache)
# ---------------------------------------------------------------------------


def select_default_chart_id(
    departure_time: datetime,
    run_cycle: str,
    available_ids: set[str] | None = None,
) -> str:
    """Pick the chart whose estimated valid time best brackets the flight ETD.

    ETD < ~3h after issuance -> analysis; else the nearest available forecast
    offset (tie-break toward the earlier offset). ``available_ids`` constrains
    the choice to charts that were actually fetched (DWD's server is flaky and
    individual forecast charts can fail independently).
    """
    return _cache.select_default_chart_id(departure_time, run_cycle, available_ids)


def chart_type_for(chart_id: str) -> str:
    """Which calibration a chart-id renders with (``analysis`` for ``ana``,
    ``icon`` for every forecast offset)."""
    return _cache.chart_type_for(chart_id)


def cache_root(data_dir: Path) -> Path:
    return _cache.cache_root(data_dir)


def cycle_dir(data_dir: Path, run_cycle: str) -> Path:
    return _cache.cycle_dir(data_dir, run_cycle)


def list_cycles(data_dir: Path) -> list[str]:
    """Return all cycle subdirs sorted oldest->newest."""
    return _cache.list_cycles(data_dir)


def lonlat_to_chart_pixel(lon: float, lat: float, chart_type: str) -> tuple[int, int]:
    """Project WGS84 lon/lat to native pixel coordinates on a DWD chart.

    ``chart_type`` is "analysis" (4389×3114) or "icon" (800×653 — used by every
    forecast offset 036/048/060/084/108).
    """
    return _cache.project(lon, lat, chart_type)


def build_route_overlay(waypoints: list[tuple[str, float, float]]) -> dict:
    """Build the route-overlay JSON consumed by the frontend SVG renderer.

    Returns a structure with both chart-types pre-computed so the frontend can
    switch between analysis and forecast tabs without extra round-trips.
    """
    return _cache.build_route_overlay(waypoints)


def resolve_chart_path(data_dir: Path, run_cycle: str, chart_id: str) -> Path | None:
    """Read-only lookup. Returns None on miss."""
    return _cache.resolve_chart_path(data_dir, run_cycle, chart_id)


def chart_meta(data_dir: Path, run_cycle: str, chart_id: str) -> dict | None:
    """Per-chart metadata (last_modified, etag, ...) or None on miss."""
    return _cache.chart_meta(data_dir, run_cycle, chart_id)


def evict_old_cycles(data_dir: Path, *, keep: int = _DEFAULT_KEEP_CYCLES) -> list[str]:
    """Delete all but the most recent ``keep`` cycle dirs. Returns evicted names."""
    return _cache.evict_old_cycles(data_dir, keep=keep)


def evict_cycles_older_than(data_dir: Path, *, max_age_hours: float) -> list[str]:
    """Age-based safety wipe. Used as a backstop in the retention loop."""
    return _cache.evict_cycles_older_than(data_dir, max_age_hours=max_age_hours)


# ---------------------------------------------------------------------------
# Refresh (DWD-specific orchestration)
# ---------------------------------------------------------------------------


def refresh_charts(
    data_dir: Path,
    *,
    keep_cycles: int = _DEFAULT_KEEP_CYCLES,
    timeout: float = _TIMEOUT_SECONDS,
) -> RefreshReport:
    """Conditional-GET all six charts in parallel.

    Strategy:
      1. Fetch the analysis chart conditionally against the most-recent
         existing cycle's ana entry. If 304: cycle hasn't rolled, reuse that
         cycle name. If 200: derive a new cycle from the response.
      2. Parallel-fetch the 5 forecasts into the resolved cycle dir with
         conditional headers based on that cycle's existing meta.
      3. Update meta.json and run eviction.
    """
    from concurrent.futures import ThreadPoolExecutor

    report = RefreshReport()
    session = _cache.make_session()

    # Step 1: resolve the current cycle by fetching ana conditionally against
    # whatever the most recent on-disk cycle's ana looked like.
    most_recent = _cache.list_cycles(data_dir)
    prev_cycle = most_recent[-1] if most_recent else None
    prev_ana_meta = (
        _cache.read_meta(_cache.cycle_dir(data_dir, prev_cycle)).get("ana") if prev_cycle else None
    )

    ana_url = f"{DWD_BASE_URL}/{_FILENAMES['ana']}"
    ana_headers = _cache.conditional_headers(prev_ana_meta)
    try:
        ana_resp = session.get(
            ana_url, headers=ana_headers, timeout=timeout, allow_redirects=True,
        )
    except requests.RequestException as e:
        report.error = f"analysis chart unreachable: {e}"
        report.charts_failed.append("ana")
        return report

    if ana_resp.status_code == 304:
        # Cycle hasn't rolled — reuse prev_cycle. ana bytes already on disk.
        run_cycle = prev_cycle
        ana_status = "unchanged"
        ana_last_modified = (
            parse_http_datetime(ana_resp.headers.get("Last-Modified"))
            or (
                datetime.fromisoformat(prev_ana_meta["last_modified"])
                if prev_ana_meta and prev_ana_meta.get("last_modified")
                else None
            )
        )
        ana_etag = ana_resp.headers.get("ETag") or (prev_ana_meta or {}).get("etag")
        ana_content_length = 0
    elif ana_resp.status_code == 200:
        ana_last_modified = parse_http_datetime(ana_resp.headers.get("Last-Modified"))
        if ana_last_modified is None:
            report.error = "analysis chart 200 OK but missing/invalid Last-Modified"
            report.charts_failed.append("ana")
            return report
        run_cycle = parse_run_cycle_from_last_modified(
            email.utils.format_datetime(ana_last_modified),
        )
        if run_cycle is None:
            report.error = "could not derive run cycle from Last-Modified"
            report.charts_failed.append("ana")
            return report
        ana_status = "downloaded"
        ana_etag = ana_resp.headers.get("ETag")
        ana_content_length = len(ana_resp.content)
    else:
        report.error = f"analysis chart HTTP {ana_resp.status_code}"
        report.charts_failed.append("ana")
        return report

    if run_cycle is None:
        # Defensive — shouldn't happen given the branches above
        report.error = "no run cycle resolved"
        return report

    report.run_cycle = run_cycle
    cdir = _cache.cycle_dir(data_dir, run_cycle)
    cdir.mkdir(parents=True, exist_ok=True)
    existing_meta = _cache.read_meta(cdir)

    # Persist analysis bytes if we got a 200
    if ana_status == "downloaded":
        try:
            _cache.atomic_write_bytes(cdir / "ana.png", ana_resp.content)
        except OSError as e:
            logger.warning("DWD chart write failed (ana): %s", e)
            report.charts_failed.append("ana")
            return report
        report.charts_refreshed.append("ana")
    else:
        report.charts_unchanged.append("ana")

    new_meta: dict[str, dict] = dict(existing_meta)
    new_meta["ana"] = {
        "last_modified": ana_last_modified.isoformat() if ana_last_modified else None,
        "etag": ana_etag,
        "content_length": ana_content_length or (existing_meta.get("ana", {}) or {}).get("content_length", 0),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "http_status": ana_resp.status_code,
    }

    # Step 2: parallel fetch 5 forecast charts
    forecast_ids = tuple(cid for cid in CHART_IDS if cid != "ana")
    with ThreadPoolExecutor(max_workers=len(forecast_ids)) as pool:
        futures = {
            cid: pool.submit(
                _cache.fetch_one,
                session=session,
                chart_id=cid,
                url=f"{DWD_BASE_URL}/{_FILENAMES[cid]}",
                existing_meta=existing_meta.get(cid),
                target_path=cdir / f"{cid}.png",
                timeout=timeout,
            )
            for cid in forecast_ids
        }
        results = {cid: fut.result() for cid, fut in futures.items()}

    _cache.apply_results_to_meta(results, report, new_meta)
    _cache.write_meta(cdir, new_meta)

    try:
        report.evicted = _cache.evict_old_cycles(data_dir, keep=keep_cycles)
    except Exception:
        logger.warning("DWD chart eviction failed", exc_info=True)

    return report
