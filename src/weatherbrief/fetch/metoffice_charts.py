"""Met Office surface-pressure (front) chart cache.

Fetches the colour surface-pressure analysis + forecast charts from the
Met Office consumer-digital API and stores them in a shared cross-briefing
on-disk cache. These are the same charts shown at
``https://weather.metoffice.gov.uk/maps-and-charts/surface-pressure`` —
isobars, H/L centres, and coloured fronts (warm=red, cold=blue,
occluded=purple) over Europe + the NE Atlantic.

Sibling of :mod:`weatherbrief.fetch.dwd_charts`; both configure a
:class:`~weatherbrief.fetch.chart_cache.ChartCache` for their source. The
two differ only in cycle discovery, chart-id set, native size, calibration,
file extension, and keep-count.

Discovery
---------
Unlike DWD (where we round ``Last-Modified`` down to a synoptic hour), the
Met Office publishes a JSON index that names the current run directly::

    GET https://data.consumer-digital.api.metoffice.gov.uk/v1/surface-pressure/colour
    -> {"issued": "2026-05-29T07:30:29Z",
        "products": [{"data_date": "...", "uri": ".../colour/2026-05-29T0000/FSXX00T_00.gif"}, ...]}

The run token (``2026-05-29T0000``) lives in each product URI's path; we
normalise it to the same ``YYYY-MM-DDThhZ`` key the DWD cache uses. The
forecast offset (hours) is parsed from the ``FSXX<RR>T_<HH>.gif`` filename,
where ``<RR>`` is the run hour (``00``/``12``) and ``<HH>`` the offset.

Cache layout::

    {data_dir}/metoffice_charts/
        2026-05-29T00Z/            # one subdir per run (00Z / 12Z)
            ana.gif                # +0h analysis (FSXX00T_00.gif)
            012.gif 024.gif 036.gif 048.gif 060.gif 072.gif 096.gif 120.gif
            meta.json              # per-chart Last-Modified, ETag, ...
        2026-05-29T12Z/
            ...

Charts update ~every 12h (~0730/1930 UTC); +72h/+84h are issued once a day
at 1930 UTC, so a 00Z run's index may legitimately omit them.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from weatherbrief.fetch.chart_cache import (
    ChartCache,
    ChartCalibration,
    ChartFetchResult,  # re-exported for back-compat
    RefreshReport,  # re-exported for back-compat
    parse_run_cycle_dt,  # re-exported for back-compat
)

logger = logging.getLogger(__name__)

MO_BASE_URL = "https://data.consumer-digital.api.metoffice.gov.uk/v1/surface-pressure"
MO_STYLE = "colour"  # "colour" (800x540, coloured fronts) — see designs note
MO_PAGE_URL = "https://weather.metoffice.gov.uk/maps-and-charts/surface-pressure"

# Index endpoint listing the current run's products.
MO_INDEX_URL = f"{MO_BASE_URL}/{MO_STYLE}"

# Ordered for UI tab presentation. Forecast offsets in hours; "ana" == +0h.
CHART_IDS: tuple[str, ...] = ("ana", "012", "024", "036", "048", "060", "072", "096", "120")
FORECAST_OFFSETS_H: dict[str, int] = {
    "ana": 0,
    "012": 12,
    "024": 24,
    "036": 36,
    "048": 48,
    "060": 60,
    "072": 72,
    "096": 96,
    "120": 120,
}

_TIMEOUT_SECONDS = 30
_DEFAULT_KEEP_CYCLES = 6  # ~3 days at 12h cadence
_USER_AGENT = "flyfun-weather/1.0 (+https://weather.flyfun.aero)"

# Native pixel sizes, used for client-side SVG overlay scaling. The colour
# set is uniformly 800x540 across every offset (verified against the live
# API), so a single calibration covers all tabs — unlike DWD which needed
# separate analysis/icon calibrations.
CHART_NATIVE_SIZE: dict[str, tuple[int, int]] = {
    "colour": (800, 540),
}

# Calibration converting WGS84 lon/lat -> chart-pixel coordinates. ``proj`` is
# the polar-stereographic projection spec (consumed by pyproj); ``homography``
# is an 8-coefficient 2D projective transform fit from manually identified
# control points via :mod:`weatherbrief.fetch.metoffice_calibrate`.
#
# Calibrated 2026-05-29 from 8 graticule crossings (lon -15..15, lat 30..60)
# clicked on FSXX00T_00.gif: max error 1.33px, rms 0.58px. The sweep confirmed
# the chart is polar-stereographic (lon_0 is absorbed by the homography), so
# lon_0=0 is fine.
#
# Kept as a plain dict (not ChartCalibration objects) so it stays the literal
# source of truth that ``scripts/dump_chart_calibrations.py`` reads to generate
# the TypeScript projection constants. ``homography=None`` would mean the chart
# is not yet calibrated (route overlay unavailable; chart PNG still renders).
_CHART_CALIBRATIONS: dict[str, dict[str, object]] = {
    "colour": {
        "proj": {"proj": "stere", "lat_0": 90, "lat_ts": 60, "lon_0": 0},
        "homography": (
            8.219263981133556e-05, -5.606207726787221e-05, 207.51767075737098,
            -5.659944858480127e-05, -8.104286158047984e-05, -51.726683330077734,
            8.48991737686672e-10, 2.4811569193648073e-09,
        ),
    },
}


def public_enabled() -> bool:
    """Whether the Met Office chart source is visible to non-admin users.

    Gated off by default while we await Met Office authorisation to reuse
    their charts. Flip ``METOFFICE_CHARTS_PUBLIC=1`` (env) to release it to
    everyone — no code change or redeploy of logic, just the env var.
    Admins always see it regardless.
    """
    return os.environ.get("METOFFICE_CHARTS_PUBLIC", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


_cache = ChartCache(
    slug="metoffice",
    display_name="Met Office",
    subdir="metoffice_charts",
    extension="gif",
    chart_ids=CHART_IDS,
    forecast_offsets_h=FORECAST_OFFSETS_H,
    calibrations={
        ct: ChartCalibration(
            proj=cal["proj"],  # type: ignore[arg-type]
            homography=cal.get("homography"),  # type: ignore[arg-type]
            native_size=CHART_NATIVE_SIZE[ct],
        )
        for ct, cal in _CHART_CALIBRATIONS.items()
    },
    chart_type_for=lambda _cid: "colour",  # every offset shares one calibration
    keep_cycles=_DEFAULT_KEEP_CYCLES,
    user_agent=_USER_AGENT,
    timeout=_TIMEOUT_SECONDS,
)


# ---------------------------------------------------------------------------
# Discovery / index parsing (Met Office-specific)
# ---------------------------------------------------------------------------

_RUN_TOKEN_RE = re.compile(r"/(\d{4}-\d{2}-\d{2}T\d{4})/")
# The two-digit token after ``FSXX`` is the *run hour* (00/06/12/18), not a
# fixed "00" — a 12Z run delivers ``FSXX12T_<offset>.gif``. Match any run hour
# or the cache silently parses zero products for non-00Z runs.
_OFFSET_RE = re.compile(r"FSXX\d{2}T_(\d{2,3})\.gif$", re.IGNORECASE)


def run_token_to_cycle(token: str) -> str | None:
    """Normalise a Met Office run token to the shared cycle key.

    ``"2026-05-29T0000"`` -> ``"2026-05-29T00Z"`` (matches the DWD cache key
    format so caption/selection helpers read the same).
    """
    try:
        dt = datetime.strptime(token, "%Y-%m-%dT%H%M")
    except (TypeError, ValueError):
        return None
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}T{dt.hour:02d}Z"


def _offset_to_chart_id(offset_h: int) -> str | None:
    if offset_h == 0:
        return "ana"
    cid = f"{offset_h:03d}"
    return cid if cid in FORECAST_OFFSETS_H else None


@dataclass
class IndexEntry:
    chart_id: str
    uri: str


@dataclass
class IndexResult:
    run_cycle: str | None
    issued: datetime | None
    entries: list[IndexEntry] = field(default_factory=list)
    error: str | None = None


def fetch_index(session: requests.Session, *, timeout: float = _TIMEOUT_SECONDS) -> IndexResult:
    """Fetch + parse the colour index, resolving the current run + product URIs."""
    try:
        resp = session.get(MO_INDEX_URL, timeout=timeout, allow_redirects=True)
    except requests.RequestException as e:
        return IndexResult(run_cycle=None, issued=None, error=f"index unreachable: {e}")
    if resp.status_code != 200:
        return IndexResult(run_cycle=None, issued=None, error=f"index HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError as e:
        return IndexResult(run_cycle=None, issued=None, error=f"index not JSON: {e}")

    products = payload.get("products") or []
    run_cycle: str | None = None
    entries: list[IndexEntry] = []
    for prod in products:
        uri = prod.get("uri")
        if not uri:
            continue
        tok = _RUN_TOKEN_RE.search(uri)
        off = _OFFSET_RE.search(uri)
        if not tok or not off:
            continue
        cid = _offset_to_chart_id(int(off.group(1)))
        if cid is None:
            continue
        if run_cycle is None:
            run_cycle = run_token_to_cycle(tok.group(1))
        entries.append(IndexEntry(chart_id=cid, uri=uri))

    issued = _parse_iso_z(payload.get("issued"))
    if run_cycle is None:
        return IndexResult(run_cycle=None, issued=issued, error="no parseable products in index")
    return IndexResult(run_cycle=run_cycle, issued=issued, entries=entries)


def _parse_iso_z(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Back-compat function surface (delegates to the shared ChartCache)
# ---------------------------------------------------------------------------


def select_default_chart_id(
    departure_time: datetime,
    run_cycle: str,
    available_ids: set[str] | None = None,
) -> str:
    """Pick the chart whose valid time best brackets the flight ETD.

    ETD within ~3h of issuance -> analysis; otherwise the nearest available
    forecast offset (tie-break toward the earlier offset). ``available_ids``
    constrains the choice to charts actually fetched (a run's index may omit
    the longest offsets).
    """
    return _cache.select_default_chart_id(departure_time, run_cycle, available_ids)


def cache_root(data_dir: Path) -> Path:
    return _cache.cache_root(data_dir)


def cycle_dir(data_dir: Path, run_cycle: str) -> Path:
    return _cache.cycle_dir(data_dir, run_cycle)


def list_cycles(data_dir: Path) -> list[str]:
    return _cache.list_cycles(data_dir)


def chart_meta(data_dir: Path, run_cycle: str, chart_id: str) -> dict | None:
    return _cache.chart_meta(data_dir, run_cycle, chart_id)


def resolve_chart_path(data_dir: Path, run_cycle: str, chart_id: str) -> Path | None:
    return _cache.resolve_chart_path(data_dir, run_cycle, chart_id)


def is_calibrated(chart_type: str = "colour") -> bool:
    return _cache.is_calibrated(chart_type)


def lonlat_to_chart_pixel(lon: float, lat: float, chart_type: str = "colour") -> tuple[int, int]:
    """Project WGS84 lon/lat to native pixel coordinates on a Met Office chart.

    Raises ``RuntimeError`` if the chart hasn't been calibrated yet; callers
    building the route overlay should guard with :func:`is_calibrated`.
    """
    return _cache.project(lon, lat, chart_type)


def build_route_overlay(waypoints: list[tuple[str, float, float]]) -> dict:
    """Build the route-overlay JSON consumed by the frontend SVG renderer.

    Returns ``{"colour": {"native_size": [800, 540], "waypoints": [...]}}``, or
    ``{}`` when the chart is not yet calibrated (frontend renders without an
    overlay).
    """
    return _cache.build_route_overlay(waypoints)


def evict_old_cycles(data_dir: Path, *, keep: int = _DEFAULT_KEEP_CYCLES) -> list[str]:
    return _cache.evict_old_cycles(data_dir, keep=keep)


# ---------------------------------------------------------------------------
# Refresh (Met Office-specific orchestration)
# ---------------------------------------------------------------------------


def refresh_charts(
    data_dir: Path,
    *,
    keep_cycles: int = _DEFAULT_KEEP_CYCLES,
    timeout: float = _TIMEOUT_SECONDS,
) -> RefreshReport:
    """Resolve the current run from the index and conditional-GET each chart.

    Cheap when the run hasn't rolled — the index call plus N conditional GETs
    that all 304. Returns a :class:`RefreshReport`; the caller gates eligibility
    (this function is dumb about flights).
    """
    from concurrent.futures import ThreadPoolExecutor

    report = RefreshReport()
    session = _cache.make_session()

    index = fetch_index(session, timeout=timeout)
    if index.run_cycle is None:
        report.error = index.error or "could not resolve Met Office run"
        return report

    run_cycle = index.run_cycle
    report.run_cycle = run_cycle
    cdir = _cache.cycle_dir(data_dir, run_cycle)
    cdir.mkdir(parents=True, exist_ok=True)
    existing_meta = _cache.read_meta(cdir)
    new_meta: dict[str, dict] = dict(existing_meta)

    with ThreadPoolExecutor(max_workers=max(1, len(index.entries))) as pool:
        futures = {
            entry.chart_id: pool.submit(
                _cache.fetch_one,
                session=session,
                chart_id=entry.chart_id,
                url=entry.uri,
                existing_meta=existing_meta.get(entry.chart_id),
                target_path=cdir / f"{entry.chart_id}.gif",
                timeout=timeout,
            )
            for entry in index.entries
        }
        results = {cid: fut.result() for cid, fut in futures.items()}

    _cache.apply_results_to_meta(results, report, new_meta)
    _cache.write_meta(cdir, new_meta)

    try:
        report.evicted = _cache.evict_old_cycles(data_dir, keep=keep_cycles)
    except Exception:
        logger.warning("Met Office chart eviction failed", exc_info=True)

    return report
