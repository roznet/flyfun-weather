"""Met Office surface-pressure (front) chart cache.

Fetches the colour surface-pressure analysis + forecast charts from the
Met Office consumer-digital API and stores them in a shared cross-briefing
on-disk cache. These are the same charts shown at
``https://weather.metoffice.gov.uk/maps-and-charts/surface-pressure`` —
isobars, H/L centres, and coloured fronts (warm=red, cold=blue,
occluded=purple) over Europe + the NE Atlantic.

Sibling of :mod:`weatherbrief.fetch.dwd_charts`; intentionally mirrors its
shape (cache layout, conditional GETs, eviction, route overlay) so the two
sources can share a single briefing UI panel with a source toggle.

Discovery
---------
Unlike DWD (where we round ``Last-Modified`` down to a synoptic hour), the
Met Office publishes a JSON index that names the current run directly::

    GET https://data.consumer-digital.api.metoffice.gov.uk/v1/surface-pressure/colour
    -> {"issued": "2026-05-29T07:30:29Z",
        "products": [{"data_date": "...", "uri": ".../colour/2026-05-29T0000/FSXX00T_00.gif"}, ...]}

The run token (``2026-05-29T0000``) lives in each product URI's path; we
normalise it to the same ``YYYY-MM-DDThhZ`` key the DWD cache uses. The
forecast offset (hours) is parsed from the ``FSXX00T_<HH>.gif`` filename.

Cache layout::

    {data_dir}/metoffice_charts/
        2026-05-29T00Z/            # one subdir per run (00Z / 12Z)
            ana.gif                # +0h analysis (FSXX00T_00.gif)
            012.gif 024.gif 036.gif 048.gif 060.gif 072.gif 084.gif
            meta.json              # per-chart Last-Modified, ETag, ...
        2026-05-29T12Z/
            ...

Charts update ~every 12h (~0730/1930 UTC); +72h/+84h are issued once a day
at 1930 UTC, so a 00Z run's index may legitimately omit them.
"""

from __future__ import annotations

import email.utils
import json
import logging
import os
import re
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

MO_BASE_URL = "https://data.consumer-digital.api.metoffice.gov.uk/v1/surface-pressure"
MO_STYLE = "colour"  # "colour" (800x540, coloured fronts) — see designs note
MO_PAGE_URL = "https://weather.metoffice.gov.uk/maps-and-charts/surface-pressure"

# Index endpoint listing the current run's products.
MO_INDEX_URL = f"{MO_BASE_URL}/{MO_STYLE}"

# Ordered for UI tab presentation. Forecast offsets in hours; "ana" == +0h.
CHART_IDS: tuple[str, ...] = ("ana", "012", "024", "036", "048", "060", "072", "084")
FORECAST_OFFSETS_H: dict[str, int] = {
    "ana": 0,
    "012": 12,
    "024": 24,
    "036": 36,
    "048": 48,
    "060": 60,
    "072": 72,
    "084": 84,
}

_TIMEOUT_SECONDS = 30
_DEFAULT_KEEP_CYCLES = 6  # ~3 days at 12h cadence
_USER_AGENT = "flyfun-weather/1.0 (+https://weather.flyfun.aero)"

_MAX_FETCH_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 1.0

# Native pixel sizes, used for client-side SVG overlay scaling. The colour
# set is uniformly 800x540 across every offset (verified against the live
# API), so a single calibration covers all tabs — unlike DWD which needed
# separate analysis/icon calibrations.
CHART_NATIVE_SIZE: dict[str, tuple[int, int]] = {
    "colour": (800, 540),
}

# Calibration converting WGS84 lon/lat -> chart-pixel coordinates.
#
# ``proj`` is the polar-stereographic projection guess (consumed by pyproj);
# ``homography`` is an 8-coefficient 2D projective transform fit from manually
# identified control points via :mod:`weatherbrief.fetch.metoffice_calibrate`.
#
# PLACEHOLDER until the chart is calibrated. ``homography=None`` means the
# route overlay is unavailable (endpoints degrade gracefully); the chart PNG
# still renders. Run::
#
#     python -m weatherbrief.fetch.metoffice_calibrate <points.json>
#
# and paste the printed tuple in below, then drop the ``# noqa`` once real.
#
# Calibrated 2026-05-29 from 8 graticule crossings (lon -15..15, lat 30..60)
# clicked on FSXX00T_00.gif: max error 1.33px, rms 0.58px. The sweep
# confirmed the chart is polar-stereographic (lon_0 is absorbed by the
# homography), so lon_0=0 is fine.
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


@dataclass
class ChartFetchResult:
    """Outcome of a single chart fetch."""

    chart_id: str
    status: str  # "downloaded" | "unchanged" | "failed"
    last_modified: datetime | None = None
    etag: str | None = None
    content_length: int = 0
    error: str | None = None


@dataclass
class RefreshReport:
    """Summary of a :func:`refresh_charts` run."""

    run_cycle: str | None = None
    charts_refreshed: list[str] = field(default_factory=list)
    charts_unchanged: list[str] = field(default_factory=list)
    charts_failed: list[str] = field(default_factory=list)
    evicted: list[str] = field(default_factory=list)
    error: str | None = None  # set when refresh couldn't even determine a cycle


# ---------------------------------------------------------------------------
# Discovery / index parsing
# ---------------------------------------------------------------------------

_RUN_TOKEN_RE = re.compile(r"/(\d{4}-\d{2}-\d{2}T\d{4})/")
_OFFSET_RE = re.compile(r"FSXX00T_(\d{2,3})\.gif$", re.IGNORECASE)


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


def parse_run_cycle_dt(run_cycle: str) -> datetime | None:
    """Inverse of :func:`run_token_to_cycle` for caption math."""
    try:
        return datetime.strptime(run_cycle, "%Y-%m-%dT%HZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


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
# Selection helpers
# ---------------------------------------------------------------------------


def select_default_chart_id(departure_time: datetime, run_cycle: str) -> str:
    """Pick the chart whose valid time best brackets the flight ETD.

    ETD within ~3h of issuance -> analysis; otherwise the nearest available
    forecast offset (tie-break toward the earlier offset).
    """
    issued = parse_run_cycle_dt(run_cycle)
    if issued is None:
        return "ana"
    delta_hours = (departure_time - issued).total_seconds() / 3600.0
    if delta_hours < 3:
        return "ana"
    forecast_ids = tuple(cid for cid in CHART_IDS if cid != "ana")
    return min(
        forecast_ids,
        key=lambda cid: (abs(FORECAST_OFFSETS_H[cid] - delta_hours), FORECAST_OFFSETS_H[cid]),
    )


# ---------------------------------------------------------------------------
# Cache paths / meta
# ---------------------------------------------------------------------------


def cache_root(data_dir: Path) -> Path:
    return data_dir / "metoffice_charts"


def cycle_dir(data_dir: Path, run_cycle: str) -> Path:
    return cache_root(data_dir) / run_cycle


def list_cycles(data_dir: Path) -> list[str]:
    root = cache_root(data_dir)
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def _read_meta(cdir: Path) -> dict[str, dict]:
    path = cdir / "meta.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_meta(cdir: Path, meta: dict[str, dict]) -> None:
    path = cdir / "meta.json"
    cdir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".meta.", suffix=".tmp", dir=cdir)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(meta, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _parse_lm_header(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _conditional_headers(existing_meta: dict | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if not existing_meta:
        return headers
    etag = existing_meta.get("etag")
    if etag:
        headers["If-None-Match"] = etag
    lm = existing_meta.get("last_modified")
    if lm:
        try:
            lm_dt = datetime.fromisoformat(lm)
            headers["If-Modified-Since"] = email.utils.format_datetime(lm_dt)
        except (TypeError, ValueError):
            pass
    return headers


# ---------------------------------------------------------------------------
# Georeferencing
# ---------------------------------------------------------------------------


def is_calibrated(chart_type: str = "colour") -> bool:
    cal = _CHART_CALIBRATIONS.get(chart_type)
    return bool(cal and cal.get("homography"))


def lonlat_to_chart_pixel(lon: float, lat: float, chart_type: str = "colour") -> tuple[int, int]:
    """Project WGS84 lon/lat to native pixel coordinates on a Met Office chart.

    Composes pyproj's polar-stereographic forward projection with a 2D
    homography. Raises if the chart hasn't been calibrated yet (homography
    is None) — callers building the route overlay should guard with
    :func:`is_calibrated`.
    """
    cal = _CHART_CALIBRATIONS.get(chart_type)
    if cal is None:
        raise ValueError(f"Unknown Met Office chart type: {chart_type!r}")
    homography = cal.get("homography")
    if not homography:
        raise RuntimeError(
            f"Met Office chart {chart_type!r} is not calibrated yet "
            "(run weatherbrief.fetch.metoffice_calibrate)"
        )

    import pyproj

    proj = pyproj.Proj(**cal["proj"])  # type: ignore[arg-type]
    a, b, c, d, e, f, g, h = homography  # type: ignore[misc]
    x, y = proj(lon, lat)
    denom = g * x + h * y + 1
    px = (a * x + b * y + c) / denom
    py = (d * x + e * y + f) / denom
    return int(px), int(py)


def build_route_overlay(waypoints: list[tuple[str, float, float]]) -> dict:
    """Build the route-overlay JSON consumed by the frontend SVG renderer.

    Args:
        waypoints: ``[(icao, lat, lon), ...]`` in flight order.

    Returns ``{"colour": {"native_size": [800, 540], "waypoints": [...]}}``.
    Returns ``{}`` when the chart is not yet calibrated, so the frontend
    simply renders the chart without an overlay.
    """
    if not is_calibrated("colour"):
        return {}
    out: dict[str, dict] = {}
    for chart_type, native_size in CHART_NATIVE_SIZE.items():
        projected = []
        for icao, lat, lon in waypoints:
            x, y = lonlat_to_chart_pixel(lon, lat, chart_type)
            projected.append({"icao": icao, "lat": lat, "lon": lon, "x": x, "y": y})
        out[chart_type] = {"native_size": list(native_size), "waypoints": projected}
    return out


# ---------------------------------------------------------------------------
# Read-only lookups
# ---------------------------------------------------------------------------


def resolve_chart_path(data_dir: Path, run_cycle: str, chart_id: str) -> Path | None:
    if chart_id not in CHART_IDS:
        return None
    path = cycle_dir(data_dir, run_cycle) / f"{chart_id}.gif"
    return path if path.exists() else None


def chart_meta(data_dir: Path, run_cycle: str, chart_id: str) -> dict | None:
    return _read_meta(cycle_dir(data_dir, run_cycle)).get(chart_id)


# ---------------------------------------------------------------------------
# Eviction
# ---------------------------------------------------------------------------


def evict_old_cycles(data_dir: Path, *, keep: int = _DEFAULT_KEEP_CYCLES) -> list[str]:
    cycles = list_cycles(data_dir)
    if len(cycles) <= keep:
        return []
    to_evict = cycles[: len(cycles) - keep]
    root = cache_root(data_dir)
    evicted: list[str] = []
    for name in to_evict:
        try:
            shutil.rmtree(root / name)
            evicted.append(name)
        except OSError:
            logger.warning("Could not evict cycle %s", name, exc_info=True)
    if evicted:
        logger.info("Evicted %d old Met Office chart cycles: %s", len(evicted), ", ".join(evicted))
    return evicted


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


def refresh_charts(
    data_dir: Path,
    *,
    keep_cycles: int = _DEFAULT_KEEP_CYCLES,
    timeout: float = _TIMEOUT_SECONDS,
) -> RefreshReport:
    """Resolve the current run from the index and conditional-GET each chart.

    Cheap when the run hasn't rolled — the index call plus N conditional
    GETs that all 304. Returns a :class:`RefreshReport`; the caller gates
    eligibility (this function is dumb about flights).
    """
    report = RefreshReport()
    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT})

    index = fetch_index(session, timeout=timeout)
    if index.run_cycle is None:
        report.error = index.error or "could not resolve Met Office run"
        return report

    run_cycle = index.run_cycle
    report.run_cycle = run_cycle
    cdir = cycle_dir(data_dir, run_cycle)
    cdir.mkdir(parents=True, exist_ok=True)
    existing_meta = _read_meta(cdir)
    new_meta: dict[str, dict] = dict(existing_meta)

    with ThreadPoolExecutor(max_workers=max(1, len(index.entries))) as pool:
        futures = {
            entry.chart_id: pool.submit(
                _fetch_one,
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

    for cid, res in results.items():
        if res.status == "downloaded":
            report.charts_refreshed.append(cid)
            new_meta[cid] = {
                "last_modified": res.last_modified.isoformat() if res.last_modified else None,
                "etag": res.etag,
                "content_length": res.content_length,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "http_status": 200,
            }
        elif res.status == "unchanged":
            report.charts_unchanged.append(cid)
            prev = dict(new_meta.get(cid, {}))
            prev["fetched_at"] = datetime.now(timezone.utc).isoformat()
            prev["http_status"] = 304
            new_meta[cid] = prev
        else:
            report.charts_failed.append(cid)

    _write_meta(cdir, new_meta)

    try:
        report.evicted = evict_old_cycles(data_dir, keep=keep_cycles)
    except Exception:
        logger.warning("Met Office chart eviction failed", exc_info=True)

    return report


def _fetch_one(
    *,
    session: requests.Session,
    chart_id: str,
    url: str,
    existing_meta: dict | None,
    target_path: Path,
    timeout: float,
) -> ChartFetchResult:
    """Conditional GET a single chart. On 200 writes bytes; on 304 leaves them."""
    headers = _conditional_headers(existing_meta)

    resp = None
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_FETCH_ATTEMPTS + 1):
        try:
            resp = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            break
        except requests.RequestException as e:
            last_exc = e
            if attempt < _MAX_FETCH_ATTEMPTS:
                logger.debug(
                    "Met Office chart fetch attempt %d/%d failed (%s): %s — retrying",
                    attempt, _MAX_FETCH_ATTEMPTS, chart_id, e,
                )
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
    if resp is None:
        logger.warning(
            "Met Office chart fetch failed after %d attempts (%s): %s",
            _MAX_FETCH_ATTEMPTS, chart_id, last_exc,
        )
        return ChartFetchResult(chart_id=chart_id, status="failed", error=str(last_exc))

    if resp.status_code == 304:
        return ChartFetchResult(
            chart_id=chart_id,
            status="unchanged",
            last_modified=_parse_lm_header(resp.headers.get("Last-Modified")),
            etag=resp.headers.get("ETag"),
        )

    if resp.status_code != 200:
        msg = f"HTTP {resp.status_code}"
        logger.warning("Met Office chart fetch failed (%s): %s", chart_id, msg)
        return ChartFetchResult(chart_id=chart_id, status="failed", error=msg)

    try:
        _atomic_write_bytes(target_path, resp.content)
    except OSError as e:
        logger.warning("Met Office chart write failed (%s): %s", chart_id, e)
        return ChartFetchResult(chart_id=chart_id, status="failed", error=str(e))

    return ChartFetchResult(
        chart_id=chart_id,
        status="downloaded",
        last_modified=_parse_lm_header(resp.headers.get("Last-Modified")),
        etag=resp.headers.get("ETag"),
        content_length=len(resp.content),
    )
