"""Shared surface-chart cache machinery for DWD + Met Office sources.

Both :mod:`weatherbrief.fetch.dwd_charts` and
:mod:`weatherbrief.fetch.metoffice_charts` fetch a set of surface
analysis/forecast charts, store them in a shared cross-briefing on-disk cache
keyed by ``(run_cycle, chart_id)``, georeference WGS84 lon/lat to chart pixels
via a polar-stereographic projection + 2D homography, and build a route
overlay for the frontend. The two sources differ only in:

  - **cycle discovery** — DWD rounds an HTTP ``Last-Modified`` down to a
    synoptic hour; Met Office reads a JSON product index;
  - **chart-id / offset set**, native pixel sizes, calibrations;
  - **file extension** (``png`` vs ``gif``) and **keep-count**;
  - the **chart_type** a given chart-id renders with (DWD ``ana`` ->
    ``analysis`` else ``icon``; Met Office always ``colour``).

Everything else — cache paths, meta read/write, atomic writes, conditional
GETs with retry, eviction, default-chart selection, projection, route overlay
— lives here as :class:`ChartCache`. Each source module constructs a module
-level :class:`ChartCache` and exposes thin function shims that delegate to it,
preserving each module's historical public API.
"""

from __future__ import annotations

import email.utils
import json
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared data structures
# ---------------------------------------------------------------------------


@dataclass
class ChartCalibration:
    """Georeferencing for one chart *type* (e.g. DWD ``analysis`` / ``icon``,
    Met Office ``colour``).

    ``proj`` is the polar-stereographic projection spec consumed by pyproj;
    ``homography`` is an 8-coefficient 2D projective transform fit from gridline
    intersections clicked on the chart (``None`` until calibrated);
    ``native_size`` is the chart's native ``(width, height)`` in pixels.
    """

    proj: Mapping[str, object]
    homography: tuple[float, ...] | None
    native_size: tuple[int, int]


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
    """Summary of a ``refresh_charts`` run.

    Caller threads ``run_cycle`` onto the briefing meta. The other lists are
    for diagnostics / logging.
    """

    run_cycle: str | None = None
    charts_refreshed: list[str] = field(default_factory=list)
    charts_unchanged: list[str] = field(default_factory=list)
    charts_failed: list[str] = field(default_factory=list)
    evicted: list[str] = field(default_factory=list)
    error: str | None = None  # set when refresh couldn't even determine a cycle


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def parse_run_cycle_dt(run_cycle: str) -> datetime | None:
    """Parse a ``YYYY-MM-DDThhZ`` run-cycle key into an aware UTC datetime.

    Returns None if the string isn't a valid run-cycle key. Identical for both
    sources (the key format is shared).
    """
    try:
        return datetime.strptime(run_cycle, "%Y-%m-%dT%HZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def parse_http_datetime(value: str | None) -> datetime | None:
    """Parse an RFC-1123 HTTP date (e.g. ``Last-Modified``) to aware UTC."""
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


# ---------------------------------------------------------------------------
# ChartCache
# ---------------------------------------------------------------------------


class ChartCache:
    """Shared on-disk cache + georeferencing for a single chart source."""

    def __init__(
        self,
        *,
        slug: str,
        display_name: str,
        subdir: str,
        extension: str,
        chart_ids: Sequence[str],
        forecast_offsets_h: Mapping[str, int],
        calibrations: Mapping[str, ChartCalibration],
        chart_type_for: Callable[[str], str],
        keep_cycles: int,
        user_agent: str,
        timeout: float = 30.0,
        max_fetch_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self.slug = slug
        self.display_name = display_name
        self.subdir = subdir
        self.extension = extension.lstrip(".")
        self.chart_ids = tuple(chart_ids)
        self.forecast_offsets_h = dict(forecast_offsets_h)
        self.calibrations = dict(calibrations)
        self._chart_type_for = chart_type_for
        self.keep_cycles = keep_cycles
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_fetch_attempts = max_fetch_attempts
        self.retry_backoff_seconds = retry_backoff_seconds

    # -- cache paths --------------------------------------------------------

    def cache_root(self, data_dir: Path) -> Path:
        return data_dir / self.subdir

    def cycle_dir(self, data_dir: Path, run_cycle: str) -> Path:
        return self.cache_root(data_dir) / run_cycle

    def list_cycles(self, data_dir: Path) -> list[str]:
        """Return all cycle subdirs sorted oldest->newest."""
        root = self.cache_root(data_dir)
        if not root.exists():
            return []
        return sorted(p.name for p in root.iterdir() if p.is_dir())

    # -- meta + atomic IO ---------------------------------------------------

    def read_meta(self, cdir: Path) -> dict[str, dict]:
        path = cdir / "meta.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def write_meta(self, cdir: Path, meta: dict[str, dict]) -> None:
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

    def atomic_write_bytes(self, path: Path, data: bytes) -> None:
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

    # -- read-only lookups --------------------------------------------------

    def resolve_chart_path(self, data_dir: Path, run_cycle: str, chart_id: str) -> Path | None:
        """Read-only lookup. Returns None on miss."""
        if chart_id not in self.chart_ids:
            return None
        path = self.cycle_dir(data_dir, run_cycle) / f"{chart_id}.{self.extension}"
        return path if path.exists() else None

    def chart_meta(self, data_dir: Path, run_cycle: str, chart_id: str) -> dict | None:
        """Per-chart metadata (last_modified, etag, ...) or None on miss."""
        return self.read_meta(self.cycle_dir(data_dir, run_cycle)).get(chart_id)

    # -- eviction -----------------------------------------------------------

    def evict_old_cycles(self, data_dir: Path, *, keep: int | None = None) -> list[str]:
        """Delete all but the most recent ``keep`` cycle dirs. Returns evicted names."""
        if keep is None:
            keep = self.keep_cycles
        cycles = self.list_cycles(data_dir)
        if len(cycles) <= keep:
            return []
        to_evict = cycles[: len(cycles) - keep]
        root = self.cache_root(data_dir)
        evicted: list[str] = []
        for name in to_evict:
            try:
                shutil.rmtree(root / name)
                evicted.append(name)
            except OSError:
                logger.warning("Could not evict cycle %s", root / name, exc_info=True)
        if evicted:
            logger.info(
                "Evicted %d old %s chart cycles: %s",
                len(evicted), self.display_name, ", ".join(evicted),
            )
        return evicted

    def evict_cycles_older_than(self, data_dir: Path, *, max_age_hours: float) -> list[str]:
        """Age-based safety wipe. Used as a backstop in the retention loop.

        Cycle age is computed from the cycle's own datetime (parsed from the
        directory name), not from filesystem mtime.
        """
        now = datetime.now(timezone.utc)
        root = self.cache_root(data_dir)
        if not root.exists():
            return []
        evicted: list[str] = []
        for name in self.list_cycles(data_dir):
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

    # -- selection ----------------------------------------------------------

    def chart_type_for(self, chart_id: str) -> str:
        return self._chart_type_for(chart_id)

    def select_default_chart_id(
        self,
        departure_time: datetime,
        run_cycle: str,
        available_ids: set[str] | None = None,
    ) -> str:
        """Pick the chart whose valid time best brackets the flight ETD.

        ETD within ~3h of issuance -> analysis; otherwise the nearest available
        forecast offset (tie-break toward the earlier offset).

        ``available_ids`` constrains the choice to charts that were actually
        fetched, so a default never points at a chart whose bytes were never
        cached (which would 410 at render time). Falls back to ``"ana"`` when no
        forecast charts are available.
        """
        issued = parse_run_cycle_dt(run_cycle)
        if issued is None:
            return "ana"
        delta_hours = (departure_time - issued).total_seconds() / 3600.0
        if delta_hours < 3:
            return "ana"
        forecast_ids = tuple(
            cid
            for cid in self.chart_ids
            if cid != "ana" and (available_ids is None or cid in available_ids)
        )
        if not forecast_ids:
            return "ana"
        return min(
            forecast_ids,
            key=lambda cid: (
                abs(self.forecast_offsets_h[cid] - delta_hours),
                self.forecast_offsets_h[cid],
            ),
        )

    # -- georeferencing -----------------------------------------------------

    def is_calibrated(self, chart_type: str) -> bool:
        cal = self.calibrations.get(chart_type)
        return bool(cal and cal.homography)

    def project(self, lon: float, lat: float, chart_type: str) -> tuple[int, int]:
        """Project WGS84 lon/lat to native pixel coordinates on a chart.

        Composes pyproj's polar-stereographic forward projection with a 2D
        homography. pyproj is imported at call time so the cache module stays
        importable in environments without it.
        """
        cal = self.calibrations.get(chart_type)
        if cal is None:
            raise ValueError(f"Unknown {self.display_name} chart type: {chart_type!r}")
        if not cal.homography:
            raise RuntimeError(
                f"{self.display_name} chart {chart_type!r} is not calibrated yet"
            )

        import pyproj

        proj = pyproj.Proj(**cal.proj)  # type: ignore[arg-type]
        a, b, c, d, e, f, g, h = cal.homography
        x, y = proj(lon, lat)
        denom = g * x + h * y + 1
        px = (a * x + b * y + c) / denom
        py = (d * x + e * y + f) / denom
        return int(px), int(py)

    def build_route_overlay(self, waypoints: list[tuple[str, float, float]]) -> dict:
        """Build the route-overlay JSON consumed by the frontend SVG renderer.

        Args:
            waypoints: ``[(icao, lat, lon), ...]`` in flight order.

        Returns one entry per *calibrated* chart type, each with
        ``native_size`` + pre-projected ``waypoints``. Uncalibrated chart types
        are skipped, so the result is ``{}`` when nothing is calibrated and the
        frontend simply renders the chart without an overlay.
        """
        out: dict[str, dict] = {}
        for chart_type, cal in self.calibrations.items():
            if not cal.homography:
                continue
            projected = [
                {"icao": icao, "lat": lat, "lon": lon,
                 "x": (xy := self.project(lon, lat, chart_type))[0], "y": xy[1]}
                for icao, lat, lon in waypoints
            ]
            out[chart_type] = {
                "native_size": list(cal.native_size),
                "waypoints": projected,
            }
        return out

    # -- fetching -----------------------------------------------------------

    def make_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": self.user_agent})
        return session

    def conditional_headers(self, existing_meta: dict | None) -> dict[str, str]:
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

    def fetch_one(
        self,
        *,
        session: requests.Session,
        chart_id: str,
        url: str,
        existing_meta: dict | None,
        target_path: Path,
        timeout: float | None = None,
    ) -> ChartFetchResult:
        """Conditional GET a single chart with linear-backoff retries.

        On 200: writes bytes atomically to ``target_path``.
        On 304: leaves the existing file alone.
        """
        if timeout is None:
            timeout = self.timeout
        headers = self.conditional_headers(existing_meta)

        resp = None
        last_exc: Exception | None = None
        for attempt in range(1, self.max_fetch_attempts + 1):
            try:
                resp = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
                break
            except requests.RequestException as e:
                last_exc = e
                if attempt < self.max_fetch_attempts:
                    logger.debug(
                        "%s chart fetch attempt %d/%d failed (%s): %s — retrying",
                        self.display_name, attempt, self.max_fetch_attempts, chart_id, e,
                    )
                    time.sleep(self.retry_backoff_seconds * attempt)
        if resp is None:
            logger.warning(
                "%s chart fetch failed after %d attempts (%s): %s",
                self.display_name, self.max_fetch_attempts, chart_id, last_exc,
            )
            return ChartFetchResult(chart_id=chart_id, status="failed", error=str(last_exc))

        if resp.status_code == 304:
            return ChartFetchResult(
                chart_id=chart_id,
                status="unchanged",
                last_modified=parse_http_datetime(resp.headers.get("Last-Modified")),
                etag=resp.headers.get("ETag"),
            )

        if resp.status_code != 200:
            msg = f"HTTP {resp.status_code}"
            logger.warning("%s chart fetch failed (%s): %s", self.display_name, chart_id, msg)
            return ChartFetchResult(chart_id=chart_id, status="failed", error=msg)

        try:
            self.atomic_write_bytes(target_path, resp.content)
        except OSError as e:
            logger.warning("%s chart write failed (%s): %s", self.display_name, chart_id, e)
            return ChartFetchResult(chart_id=chart_id, status="failed", error=str(e))

        return ChartFetchResult(
            chart_id=chart_id,
            status="downloaded",
            last_modified=parse_http_datetime(resp.headers.get("Last-Modified")),
            etag=resp.headers.get("ETag"),
            content_length=len(resp.content),
        )

    def apply_results_to_meta(
        self,
        results: Mapping[str, ChartFetchResult],
        report: RefreshReport,
        new_meta: dict[str, dict],
    ) -> None:
        """Fold per-chart fetch results into ``report`` + ``new_meta`` in place."""
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
