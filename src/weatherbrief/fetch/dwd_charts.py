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

Briefings store a single ``run_cycle`` reference plus the picked
``default_chart_id``. Cache misses at render time render an "unavailable"
placeholder — this is intentional, the briefing is small and
reproducible-as-long-as-the-cache-has-the-bytes.
"""

from __future__ import annotations

import email.utils
import json
import logging
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

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
    """Summary of a refresh_charts run.

    Caller threads ``run_cycle`` onto the briefing meta. The other lists
    are for diagnostics / logging.
    """

    run_cycle: str | None = None
    charts_refreshed: list[str] = field(default_factory=list)
    charts_unchanged: list[str] = field(default_factory=list)
    charts_failed: list[str] = field(default_factory=list)
    evicted: list[str] = field(default_factory=list)
    error: str | None = None  # set when refresh couldn't even determine a cycle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_run_cycle_from_last_modified(last_modified: str | None) -> str | None:
    """Convert an HTTP ``Last-Modified`` header into a run-cycle key.

    Rounds DOWN to the previous synoptic hour (00/06/12/18 UTC). A 09:42 UTC
    publish belongs to the 06Z run, not the (future) 12Z run.

    Returns None if the header is missing or unparseable.
    """
    if not last_modified:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(last_modified)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    synoptic_hour = (dt.hour // 6) * 6
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}T{synoptic_hour:02d}Z"


def parse_run_cycle_dt(run_cycle: str) -> datetime | None:
    """Inverse of :func:`parse_run_cycle_from_last_modified` for caption math.

    Returns an aware UTC datetime at the synoptic hour, or None if the
    string isn't a valid run-cycle key.
    """
    try:
        return datetime.strptime(run_cycle, "%Y-%m-%dT%HZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def select_default_chart_id(
    departure_time: datetime,
    run_cycle: str,
) -> str:
    """Pick the chart whose estimated valid time best brackets the flight ETD.

    Rules (from spec):
      - ETD < ~3h after issuance → analysis
      - else: nearest forecast offset (tie-break toward earlier offset)
      - if ETD beyond the +108h horizon, caller should set ``within_horizon=False``
        BEFORE calling this; we still return ``"108"`` defensively.
    """
    issued = parse_run_cycle_dt(run_cycle)
    if issued is None:
        return "ana"
    delta_hours = (departure_time - issued).total_seconds() / 3600.0
    if delta_hours < 3:
        return "ana"
    forecast_ids = ("036", "048", "060", "084", "108")
    return min(
        forecast_ids,
        key=lambda cid: (abs(FORECAST_OFFSETS_H[cid] - delta_hours), FORECAST_OFFSETS_H[cid]),
    )


def cache_root(data_dir: Path) -> Path:
    return data_dir / "dwd_charts"


def cycle_dir(data_dir: Path, run_cycle: str) -> Path:
    return cache_root(data_dir) / run_cycle


def list_cycles(data_dir: Path) -> list[str]:
    """Return all cycle subdirs sorted oldest→newest."""
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
    """Write bytes to ``path`` via tempfile + rename for atomicity."""
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


def lonlat_to_chart_pixel(
    lon: float,
    lat: float,
    chart_type: str,
) -> tuple[int, int]:
    """Project WGS84 lon/lat to native pixel coordinates on a DWD chart.

    ``chart_type`` is "analysis" (4389×3114) or "icon" (800×653 — used by
    every forecast offset 036/048/060/084/108).

    Composes pyproj's polar stereographic forward projection with a 2D
    homography fit from gridline intersections. Uses pyproj at runtime;
    not imported here at module scope so the cache module stays usable
    in environments where pyproj isn't installed (it's already a
    dependency for the frontal pipeline).
    """
    if chart_type not in _CHART_CALIBRATIONS:
        raise ValueError(f"Unknown DWD chart type: {chart_type!r}")

    import pyproj

    cal = _CHART_CALIBRATIONS[chart_type]
    proj = pyproj.Proj(**cal["proj"])  # type: ignore[arg-type]
    a, b, c, d, e, f, g, h = cal["homography"]  # type: ignore[misc]

    x, y = proj(lon, lat)
    denom = g * x + h * y + 1
    px = (a * x + b * y + c) / denom
    py = (d * x + e * y + f) / denom
    return int(px), int(py)


def build_route_overlay(
    waypoints: list[tuple[str, float, float]],
) -> dict:
    """Build the route-overlay JSON consumed by the frontend SVG renderer.

    Args:
        waypoints: ``[(icao, lat, lon), ...]`` in flight order.

    Returns a structure with both chart-types pre-computed so the
    frontend can switch between analysis and forecast tabs without
    extra round-trips:

        {
          "analysis": {
            "native_size": [4389, 3114],
            "waypoints": [{"icao": "EGTF", "lat": ..., "lon": ..., "x": 1234, "y": 567}, ...]
          },
          "icon": { "native_size": [800, 653], "waypoints": [...] }
        }
    """
    out: dict[str, dict] = {}
    for chart_type, native_size in CHART_NATIVE_SIZE.items():
        projected = []
        for icao, lat, lon in waypoints:
            x, y = lonlat_to_chart_pixel(lon, lat, chart_type)
            projected.append(
                {"icao": icao, "lat": lat, "lon": lon, "x": x, "y": y}
            )
        out[chart_type] = {
            "native_size": list(native_size),
            "waypoints": projected,
        }
    return out


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
    """Build If-None-Match / If-Modified-Since headers from prior meta."""
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
# Public API
# ---------------------------------------------------------------------------


def resolve_chart_path(
    data_dir: Path,
    run_cycle: str,
    chart_id: str,
) -> Path | None:
    """Read-only lookup. Returns None on miss."""
    if chart_id not in CHART_IDS:
        return None
    path = cycle_dir(data_dir, run_cycle) / f"{chart_id}.png"
    return path if path.exists() else None


def chart_meta(
    data_dir: Path,
    run_cycle: str,
    chart_id: str,
) -> dict | None:
    """Per-chart metadata (last_modified, etag, ...) or None on miss."""
    return _read_meta(cycle_dir(data_dir, run_cycle)).get(chart_id)


def evict_old_cycles(
    data_dir: Path,
    *,
    keep: int = _DEFAULT_KEEP_CYCLES,
) -> list[str]:
    """Delete all but the most recent ``keep`` cycle dirs. Returns evicted names."""
    cycles = list_cycles(data_dir)
    if len(cycles) <= keep:
        return []
    to_evict = cycles[: len(cycles) - keep]
    root = cache_root(data_dir)
    evicted: list[str] = []
    for name in to_evict:
        target = root / name
        try:
            shutil.rmtree(target)
            evicted.append(name)
        except OSError:
            logger.warning("Could not evict cycle %s", target, exc_info=True)
    if evicted:
        logger.info("Evicted %d old DWD chart cycles: %s", len(evicted), ", ".join(evicted))
    return evicted


def evict_cycles_older_than(
    data_dir: Path,
    *,
    max_age_hours: float,
) -> list[str]:
    """Age-based safety wipe. Used as a backstop in the retention loop.

    Cycle age is computed from the cycle's own datetime (parsed from the
    directory name), not from filesystem mtime — that's what we mean by
    "the chart is from issued + N hours ago" in the rest of the code.
    """
    now = datetime.now(timezone.utc)
    root = cache_root(data_dir)
    if not root.exists():
        return []
    evicted: list[str] = []
    for name in list_cycles(data_dir):
        dt = parse_run_cycle_dt(name)
        if dt is None:
            continue
        age_hours = (now - dt).total_seconds() / 3600.0
        if age_hours > max_age_hours:
            try:
                shutil.rmtree(root / name)
                evicted.append(name)
            except OSError:
                logger.warning("Could not age-evict cycle %s", name, exc_info=True)
    return evicted


def refresh_charts(
    data_dir: Path,
    *,
    keep_cycles: int = _DEFAULT_KEEP_CYCLES,
    timeout: float = _TIMEOUT_SECONDS,
) -> RefreshReport:
    """Conditional-GET all six charts in parallel.

    Strategy:
      1. Fetch the analysis chart conditionally against the most-recent
         existing cycle's ana entry. If 304: cycle hasn't rolled, reuse
         that cycle name. If 200: derive a new cycle from the response.
      2. Parallel-fetch the 5 forecasts into the resolved cycle dir with
         conditional headers based on that cycle's existing meta.
      3. Update meta.json and run eviction.

    Returns a :class:`RefreshReport`. Caller decides whether to gate on
    eligibility before calling this — the function is dumb about flights.
    """
    report = RefreshReport()
    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT})

    # Step 1: resolve the current cycle by fetching ana conditionally
    # against whatever the most recent on-disk cycle's ana looked like.
    most_recent = list_cycles(data_dir)
    prev_cycle = most_recent[-1] if most_recent else None
    prev_ana_meta = (
        _read_meta(cycle_dir(data_dir, prev_cycle)).get("ana") if prev_cycle else None
    )

    ana_url = f"{DWD_BASE_URL}/{_FILENAMES['ana']}"
    ana_headers = _conditional_headers(prev_ana_meta)
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
            _parse_lm_header(ana_resp.headers.get("Last-Modified"))
            or (
                datetime.fromisoformat(prev_ana_meta["last_modified"])
                if prev_ana_meta and prev_ana_meta.get("last_modified")
                else None
            )
        )
        ana_etag = ana_resp.headers.get("ETag") or (prev_ana_meta or {}).get("etag")
        ana_content_length = 0
    elif ana_resp.status_code == 200:
        ana_last_modified = _parse_lm_header(ana_resp.headers.get("Last-Modified"))
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
    cdir = cycle_dir(data_dir, run_cycle)
    cdir.mkdir(parents=True, exist_ok=True)
    existing_meta = _read_meta(cdir)

    # Persist analysis bytes if we got a 200
    if ana_status == "downloaded":
        try:
            _atomic_write_bytes(cdir / "ana.png", ana_resp.content)
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
                _fetch_one,
                session=session,
                chart_id=cid,
                existing_meta=existing_meta.get(cid),
                target_path=cdir / f"{cid}.png",
                timeout=timeout,
            )
            for cid in forecast_ids
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
        logger.warning("DWD chart eviction failed", exc_info=True)

    return report


def _fetch_one(
    *,
    session: requests.Session,
    chart_id: str,
    existing_meta: dict | None,
    target_path: Path,
    timeout: float,
) -> ChartFetchResult:
    """Conditional GET a single forecast chart.

    On 200: writes bytes atomically to ``target_path``.
    On 304: leaves the existing file alone.
    """
    filename = _FILENAMES[chart_id]
    url = f"{DWD_BASE_URL}/{filename}"
    headers = _conditional_headers(existing_meta)

    try:
        resp = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    except requests.RequestException as e:
        logger.warning("DWD chart fetch failed (%s): %s", chart_id, e)
        return ChartFetchResult(chart_id=chart_id, status="failed", error=str(e))

    if resp.status_code == 304:
        return ChartFetchResult(
            chart_id=chart_id,
            status="unchanged",
            last_modified=_parse_lm_header(resp.headers.get("Last-Modified")),
            etag=resp.headers.get("ETag"),
        )

    if resp.status_code != 200:
        msg = f"HTTP {resp.status_code}"
        logger.warning("DWD chart fetch failed (%s): %s", chart_id, msg)
        return ChartFetchResult(chart_id=chart_id, status="failed", error=msg)

    try:
        _atomic_write_bytes(target_path, resp.content)
    except OSError as e:
        logger.warning("DWD chart write failed (%s): %s", chart_id, e)
        return ChartFetchResult(chart_id=chart_id, status="failed", error=str(e))

    return ChartFetchResult(
        chart_id=chart_id,
        status="downloaded",
        last_modified=_parse_lm_header(resp.headers.get("Last-Modified")),
        etag=resp.headers.get("ETag"),
        content_length=len(resp.content),
    )
