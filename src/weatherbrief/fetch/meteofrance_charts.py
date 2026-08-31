"""Météo-France TEMSI (SIGWX) chart cache, via the AEROWEB partner server.

Fetches the low-level **TEMSI France** (SFC–FL150) and **TEMSI EUROC**
(FL100–FL450) significant-weather charts and stores them in the shared
cross-briefing on-disk cache. Sibling of :mod:`weatherbrief.fetch.dwd_charts`
and :mod:`weatherbrief.fetch.metoffice_charts`; all three configure a
:class:`~weatherbrief.fetch.chart_cache.ChartCache`.

Access
------
AEROWEB is a *convention-gated* partner API, not an open endpoint. The access
code lives in ``METEOFRANCE_API_CODE``; with it unset this source is simply
off (:func:`enabled` is False and refresh is a no-op), so a dev environment
without the credential behaves like one where the source was never added.

**Redistribution is licensed only to users operating in French airspace, and
only non-commercially.** That is a per-*flight* condition, unlike the per-user
gate on Met Office charts, and :func:`route_licence_allows` is the single
place it is decided. Serving these bytes for a route that never touches France
is a licence breach, so the gate is deliberately fail-closed: no route, no
chart.

Cycle model
-----------
DWD and Met Office publish one *run* carrying many forecast offsets. AEROWEB
publishes no run/offset split at all: it offers a rolling window (two deep, as
observed) of **absolute validities**, three hours apart, which shifts forward
through the day. So this source keys the cache the other way up::

    run_cycle = the chart's valid time     ("2026-08-31T12Z")
    chart_id  = the zone                   ("france" / "euroc")
    offset    = 0, always

Cache layout::

    {data_dir}/meteofrance_charts/
        2026-08-31T12Z/
            france.png     # 1160x827   SFC-FL150
            euroc.png      # 1478x1144  FL100-FL450
            meta.json      # per-chart date_run, fetched_at, ...
        2026-08-31T15Z/
            ...

Successive refreshes accumulate validities rather than replacing a run, which
is why eviction is age-based on the cycle's own time.

Freshness
---------
The image endpoint sends ``Cache-Control: no-store`` and a ``Last-Modified``
regenerated per request, so conditional GETs are worthless here — the server
always answers 200 with fresh bytes. Re-fetch is instead decided from the
XML's own ``<date_run>``: a chart already on disk whose recorded ``date_run``
still matches the manifest is left alone. That is the mechanism AEROWEB added
``date_run``/``date_echeance`` for (doc §12.3) — some products reissue a given
validity from a later model run, and this catches exactly that.

Horizon caveat
--------------
TEMSI is short-range: the observed window runs from roughly one hour behind to
three hours ahead of now. A briefing built the day before a flight will have
**no** TEMSI covering it. :func:`select_cycle_for_time` returns None rather
than silently handing back a chart valid at the wrong time, and callers must
render that as "no chart for this window", never as a stale chart.
"""

from __future__ import annotations

import io
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

from weatherbrief.fetch.chart_cache import (
    ChartCache,
    ChartCalibration,
    ChartFetchResult,  # re-exported for back-compat
    RefreshReport,  # re-exported for back-compat
    parse_run_cycle_dt,  # re-exported for back-compat
)

logger = logging.getLogger(__name__)

AEROWEB_BASE = "https://aviation.meteo.fr"
AEROWEB_URL = f"{AEROWEB_BASE}/FR/aviation/serveur_donnees.jsp"

# Zone slug -> the AEROWEB ZONE parameter it is requested with. The slug is
# also the chart_id and the calibration key.
ZONE_PARAMS: dict[str, str] = {
    "france": "AERO_FRANCE",
    "euroc": "AERO_EUROC",
}

# Ordered for UI tab presentation: the low-level chart first, since it is the
# one most GA flights are actually flown inside.
CHART_IDS: tuple[str, ...] = ("france", "euroc")

# Every chart *is* its cycle's valid time, so no chart carries an offset.
FORECAST_OFFSETS_H: dict[str, int] = {cid: 0 for cid in CHART_IDS}

ZONE_LABELS: dict[str, str] = {
    "france": "TEMSI France (SFC–FL150)",
    "euroc": "TEMSI EUROC (FL100–FL450)",
}

_TIMEOUT_SECONDS = 30
# 3h between validities and a 2-deep window: 16 keeps ~2 days of accumulated
# charts, matching the DWD keep-count in wall-clock terms.
_DEFAULT_KEEP_CYCLES = 16
_USER_AGENT = "flyfun-weather/1.0 (+https://weather.flyfun.aero)"

# Native pixel sizes, used for client-side SVG overlay scaling. Verified
# against the live API 2026-08-31; the frame is pixel-stable across validities
# (a coastline column-scan between the 12Z and 15Z France charts found a 0px
# offset on 51 of 52 sampled columns), so one calibration per zone covers
# every issue.
CHART_NATIVE_SIZE: dict[str, tuple[int, int]] = {
    "france": (1160, 827),
    "euroc": (1478, 1144),
}

# Calibration converting WGS84 lon/lat -> chart-pixel coordinates. ``proj`` is
# the polar-stereographic projection spec (consumed by pyproj); ``homography``
# is an 8-coefficient 2D projective transform.
#
# ``homography=None`` means NOT YET CALIBRATED: the chart still renders, and
# ``build_route_overlay`` simply omits that zone, so the frontend draws the
# image with no route line rather than a wrong one. Fit these with
# :mod:`weatherbrief.fetch.metoffice_calibrate` (the module is source-agnostic
# despite the name) from graticule crossings — both TEMSI zones draw a lon/lat
# graticule, which the DWD and Met Office charts do not, so control points come
# from the chart itself rather than guessed coastline features.
#
# Kept as a plain dict (not ChartCalibration objects) so it stays the literal
# source of truth that ``scripts/dump_chart_calibrations.py`` reads to generate
# the TypeScript projection constants.
_CHART_CALIBRATIONS: dict[str, dict[str, object]] = {
    "france": {
        "proj": {"proj": "stere", "lat_0": 90, "lat_ts": 60, "lon_0": 0},
        "homography": None,
    },
    "euroc": {
        "proj": {"proj": "stere", "lat_0": 90, "lat_ts": 60, "lon_0": 0},
        "homography": None,
    },
}


def _chart_type_for(chart_id: str) -> str:
    """Météo-France: one calibration per zone, and the id *is* the zone."""
    return chart_id


def _pdf_to_png_bytes(payload: bytes) -> bytes:
    """Unwrap AEROWEB's PDF envelope to the single raster it carries.

    Every TEMSI arrives as a one-page A4 PDF (iText) whose only content is one
    indexed image at the chart's native size. Extracting that image is both
    lossless and cheaper than rasterising the page — and it pins the pixel
    dimensions to the source's, which the calibration depends on. Verified
    byte-identical to ``pdfimages -png`` output for both zones.
    """
    import fitz  # PyMuPDF — already a hard dependency (GRAMET/report handling)

    with fitz.open(stream=payload, filetype="pdf") as doc:
        if doc.page_count < 1:
            raise ValueError("AEROWEB PDF has no pages")
        images = doc[0].get_images(full=True)
        if not images:
            raise ValueError("AEROWEB PDF page carries no embedded image")
        extracted = doc.extract_image(images[0][0])

    data = extracted["image"]
    if extracted.get("ext") == "png":
        return data

    # Defensive: the envelope has always held a PNG, but re-encode rather than
    # write a file whose extension lies about its contents.
    from PIL import Image

    buf = io.BytesIO()
    Image.open(io.BytesIO(data)).convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


_cache = ChartCache(
    slug="meteofrance",
    display_name="Météo-France",
    subdir="meteofrance_charts",
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
    content_transform=_pdf_to_png_bytes,
)


# ---------------------------------------------------------------------------
# Access + licence gating
# ---------------------------------------------------------------------------


def access_code() -> str | None:
    """The AEROWEB access code, or None when the source isn't configured."""
    return os.environ.get("METEOFRANCE_API_CODE", "").strip() or None


def enabled() -> bool:
    """Whether this source is configured at all (an access code is present)."""
    return access_code() is not None


def route_licence_allows(route) -> bool:
    """Whether the Météo-France licence permits serving charts for ``route``.

    Redistribution is limited to users operating in French airspace, so the
    route must touch France. Reuses :func:`weatherbrief.airports.route_countries`
    — the same country detection that gates the Météo-France *model* (see
    ``required_country="FR"`` in ``fetch/variables.py``), which samples the
    great circle every 25 nm against timezone polygons and so catches genuine
    overflight, not just French departures and arrivals.

    Fail-closed: anything that stops us proving the route touches France (no
    route, or country detection unavailable) denies the chart. That is the
    opposite of the model gate's fallback, and deliberately so — a missing
    model costs a user some forecast detail, whereas serving these bytes to an
    unlicensed user is a breach of the convention.
    """
    if route is None:
        return False
    try:
        from weatherbrief.airports import route_countries

        return "FR" in route_countries(route)
    except (ImportError, OSError, RuntimeError):
        logger.warning(
            "Country detection unavailable; denying Météo-France charts",
            exc_info=True,
        )
        return False


# ---------------------------------------------------------------------------
# Discovery (Météo-France-specific)
# ---------------------------------------------------------------------------


_ECHEANCE_RE = re.compile(r"[?&]echeance=(\d{14})")


def _valid_time_to_run_cycle(stamp: str) -> str | None:
    """``"20260831120000"`` -> ``"2026-08-31T12Z"``; None if unrepresentable.

    The cache key has hour granularity. Every TEMSI validity observed lands on
    a whole hour; one that didn't could not be stored without lying about its
    valid time, so it is skipped rather than truncated.
    """
    try:
        dt = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if dt.minute or dt.second:
        logger.warning("Skipping TEMSI validity with sub-hour precision: %s", stamp)
        return None
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}T{dt.hour:02d}Z"


class AerowebError(RuntimeError):
    """AEROWEB rejected the request (bad code, or a malformed query)."""


def _parse_cartes_xml(payload: bytes, zone_slug: str) -> dict[str, dict[str, str]]:
    """Parse a ``TYPE_DONNEES=CARTES`` response into ``{run_cycle: {...}}``.

    Each entry carries the absolute ``url`` to fetch and the ``date_run`` used
    as the re-fetch signal. Raises :class:`AerowebError` on an auth rejection
    or an error document, so a bad credential surfaces as a real failure
    rather than an empty (and silently ignored) chart list.
    """
    root = ET.fromstring(payload)
    if root.tag == "acces":
        code = (root.findtext("code") or "").strip()
        raise AerowebError(f"AEROWEB rejected the access code (acces={code!r})")
    if root.tag.upper() == "ERREUR":
        raise AerowebError("AEROWEB returned an error document for a CARTES query")

    out: dict[str, dict[str, str]] = {}
    for carte in root.iter("carte"):
        if (carte.findtext("type") or "").strip().upper() != "TEMSI":
            continue
        lien = (carte.findtext("lien") or "").strip()
        if not lien:
            continue
        match = _ECHEANCE_RE.search(lien)
        if match is None:
            logger.warning("TEMSI link without an echeance parameter: %s", lien[:120])
            continue
        run_cycle = _valid_time_to_run_cycle(match.group(1))
        if run_cycle is None:
            continue
        out[run_cycle] = {
            "url": AEROWEB_BASE + lien if lien.startswith("/") else lien,
            "date_run": (carte.findtext("date_run") or "").strip(),
            "zone": zone_slug,
        }
    return out


def discover_charts(
    *,
    session: requests.Session | None = None,
    timeout: float = _TIMEOUT_SECONDS,
) -> dict[str, dict[str, dict[str, str]]]:
    """Ask AEROWEB what TEMSI validities are currently on offer.

    Returns ``{run_cycle: {zone_slug: {"url", "date_run", "zone"}}}``. One
    request per zone (targeted, ~800 bytes) rather than one
    ``BASE_COMPLETE=oui`` request (1.1 MB covering 42 zones we don't want).

    A zone that fails is logged and skipped; the other zone's charts are still
    returned, so one bad zone can't starve the cache.
    """
    code = access_code()
    if code is None:
        return {}
    own_session = session is None
    session = session or _cache.make_session()
    try:
        merged: dict[str, dict[str, dict[str, str]]] = {}
        for zone_slug, zone_param in ZONE_PARAMS.items():
            params = {
                "ID": code,
                "TYPE_DONNEES": "CARTES",
                "BASE_COMPLETE": "non",
                "VUE_CARTE": "AERO_TEMSI",
                "ZONE": zone_param,
            }
            try:
                resp = session.get(AEROWEB_URL, params=params, timeout=timeout)
                resp.raise_for_status()
                found = _parse_cartes_xml(resp.content, zone_slug)
            except (requests.RequestException, ET.ParseError, AerowebError):
                logger.warning(
                    "Météo-France TEMSI discovery failed for zone %s", zone_slug,
                    exc_info=True,
                )
                continue
            for run_cycle, entry in found.items():
                merged.setdefault(run_cycle, {})[zone_slug] = entry
        return merged
    finally:
        if own_session:
            session.close()


# ---------------------------------------------------------------------------
# Back-compat function surface (delegates to the shared ChartCache)
# ---------------------------------------------------------------------------


def chart_type_for(chart_id: str) -> str:
    """Which calibration a chart-id renders with (the zone slug itself)."""
    return _cache.chart_type_for(chart_id)


def cache_root(data_dir: Path) -> Path:
    return _cache.cache_root(data_dir)


def cycle_dir(data_dir: Path, run_cycle: str) -> Path:
    return _cache.cycle_dir(data_dir, run_cycle)


def list_cycles(data_dir: Path) -> list[str]:
    """Return all cycle subdirs sorted oldest->newest (i.e. by valid time)."""
    return _cache.list_cycles(data_dir)


def lonlat_to_chart_pixel(lon: float, lat: float, chart_type: str) -> tuple[int, int]:
    """Project WGS84 lon/lat to native pixel coordinates on a TEMSI chart."""
    return _cache.project(lon, lat, chart_type)


def build_route_overlay(waypoints: list[tuple[str, float, float]]) -> dict:
    """Build the route-overlay JSON consumed by the frontend SVG renderer.

    Empty until the zones are calibrated — uncalibrated chart types are
    skipped, so the chart renders without a route line rather than with a
    misplaced one.
    """
    return _cache.build_route_overlay(waypoints)


def resolve_chart_path(data_dir: Path, run_cycle: str, chart_id: str) -> Path | None:
    """Read-only lookup. Returns None on miss."""
    return _cache.resolve_chart_path(data_dir, run_cycle, chart_id)


def chart_meta(data_dir: Path, run_cycle: str, chart_id: str) -> dict | None:
    """Per-chart metadata (date_run, fetched_at, ...) or None on miss."""
    return _cache.chart_meta(data_dir, run_cycle, chart_id)


def evict_old_cycles(data_dir: Path, *, keep: int = _DEFAULT_KEEP_CYCLES) -> list[str]:
    """Delete all but the most recent ``keep`` cycle dirs. Returns evicted names."""
    return _cache.evict_old_cycles(data_dir, keep=keep)


def evict_cycles_older_than(data_dir: Path, *, max_age_hours: float) -> list[str]:
    """Age-based safety wipe. Used as a backstop in the retention loop."""
    return _cache.evict_cycles_older_than(data_dir, max_age_hours=max_age_hours)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

# Half the 3h validity spacing: beyond this a chart is closer to describing a
# neighbouring validity than the one asked for.
MAX_VALIDITY_GAP = timedelta(hours=1, minutes=30)


def select_cycle_for_time(
    data_dir: Path,
    target: datetime,
    *,
    chart_id: str | None = None,
    max_gap: timedelta = MAX_VALIDITY_GAP,
) -> str | None:
    """Cached validity closest to ``target``, or None if none is close enough.

    Unlike the DWD/Met Office ``select_default_chart_id`` (which picks a
    forecast offset *within* a run), Météo-France selection picks the *cycle*,
    because here the cycle is the valid time.

    Returns None when the nearest cached validity is further than ``max_gap``
    away. TEMSI's horizon is ~3h, so for most briefing lead times that is the
    normal answer, not an error — callers must show "no chart for this window"
    rather than reaching for the closest chart regardless of age.

    ``chart_id`` restricts the search to cycles where that zone's bytes are
    actually on disk, so a selection never points at a chart that would 410.
    """
    best: tuple[timedelta, str] | None = None
    for run_cycle in list_cycles(data_dir):
        valid = parse_run_cycle_dt(run_cycle)
        if valid is None:
            continue
        if chart_id is not None and resolve_chart_path(data_dir, run_cycle, chart_id) is None:
            continue
        gap = abs(valid - target)
        if gap > max_gap:
            continue
        if best is None or gap < best[0]:
            best = (gap, run_cycle)
    return best[1] if best else None


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


def refresh_charts(
    data_dir: Path,
    *,
    keep_cycles: int = _DEFAULT_KEEP_CYCLES,
    timeout: float = _TIMEOUT_SECONDS,
) -> RefreshReport:
    """Fetch every TEMSI validity AEROWEB currently offers that we lack.

    Strategy:
      1. Discover the offered validities (one CARTES query per zone).
      2. For each (validity, zone), skip when the bytes are already on disk
         *and* the recorded ``date_run`` still matches — conditional GETs are
         useless against this endpoint, so identity comes from the manifest.
      3. Fetch the rest, unwrapping each PDF to its raster on write.
      4. Update meta.json and evict.

    Sequential rather than pooled: this is at most four small downloads
    against a partner API whose fair-use terms we would rather not test.
    """
    report = RefreshReport()

    if not enabled():
        report.error = "METEOFRANCE_API_CODE not set"
        logger.debug("Météo-France charts not configured; skipping refresh")
        return report

    session = _cache.make_session()
    try:
        offered = discover_charts(session=session, timeout=timeout)
        if not offered:
            report.error = "no TEMSI charts offered by AEROWEB"
            logger.warning("Météo-France TEMSI discovery returned nothing")
            return report

        for run_cycle in sorted(offered):
            cdir = _cache.cycle_dir(data_dir, run_cycle)
            meta = _cache.read_meta(cdir)
            new_meta = dict(meta)
            results: dict[str, ChartFetchResult] = {}

            for zone_slug, entry in sorted(offered[run_cycle].items()):
                on_disk = _cache.resolve_chart_path(data_dir, run_cycle, zone_slug)
                prior = meta.get(zone_slug) or {}
                if on_disk is not None and prior.get("date_run") == entry["date_run"]:
                    report.charts_unchanged.append(f"{run_cycle}/{zone_slug}")
                    continue

                result = _cache.fetch_one(
                    session=session,
                    chart_id=zone_slug,
                    url=entry["url"],
                    existing_meta=None,  # conditional GETs are inert here
                    target_path=cdir / f"{zone_slug}.{_cache.extension}",
                    timeout=timeout,
                )
                results[zone_slug] = result
                if result.status == "downloaded":
                    new_meta[zone_slug] = {
                        "date_run": entry["date_run"],
                        "content_length": result.content_length,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
                    report.charts_refreshed.append(f"{run_cycle}/{zone_slug}")
                else:
                    report.charts_failed.append(f"{run_cycle}/{zone_slug}")

            if results:
                _cache.write_meta(cdir, new_meta)

        # Newest offered validity, for callers that want a "current" pointer.
        report.run_cycle = max(offered) if offered else None
        report.evicted = _cache.evict_old_cycles(data_dir, keep=keep_cycles)
        logger.info(
            "Météo-France TEMSI refresh: %d fetched, %d unchanged, %d failed",
            len(report.charts_refreshed), len(report.charts_unchanged),
            len(report.charts_failed),
        )
        return report
    finally:
        session.close()
