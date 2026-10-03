"""Satellite infrared imagery for the map, proxied from EUMETSAT EUMETView (#652).

What Windy shows as "satellite" is the raw infrared channel: cold cloud tops
white, warm ground dark.  We collect the CTTH *retrieval* instead (heights and
temperatures), which answers the route's numbers but draws as blocks with holes
where no cloud was detected — it cannot make that picture.  The IR channel
itself (MTG FCI level 1c, 10.5 µm, 1 km) is published ready-rendered by
EUMETSAT's own WMS, ``view.eumetsat.int``, whose capabilities declare
``Fees: none`` and ``AccessConstraints: none``.

This layer is **display only**.  Nothing is sampled from it, it carries no
parallax correction (a high top draws up to ~50 km north of the ground point
below it at European latitudes, which is why the sampled numbers stay on the
corrected CTTH), and the briefing never depends on it.

Proxied rather than loaded by the browser directly:

* the site's CSP allows images from the basemap hosts only, and the CSP lives
  in the private deploy config;
* our cache, not EUMETView, absorbs the repeat requests of every map view;
* the tile URL takes the same ``{stamp}/{z}/{x}/{y}`` shape as the radar tiles,
  so the loop (#653) steps both layers the same way.

Times: the WMS ``time`` dimension is ``start/end/PT10M``; a value is the start
of the 10-minute FCI repeat cycle.  Only times we advertise (the last
``RETAINED_FRAMES`` cycles) are proxied, so the endpoint cannot be used to pull
arbitrary history through the server.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

WMS_URL = "https://view.eumetsat.int/geoserver/ows"
# GeoServer's per-layer virtual service: an ~8 KB capabilities document
# instead of ~80 KB for every layer EUMETView publishes.
CAPABILITIES_URL = "https://view.eumetsat.int/geoserver/mtg_fd/ir105_hrfi/ows"
LAYER = "mtg_fd:ir105_hrfi"
# Pinned rather than the layer default, so a change of default on their side
# cannot recolour our map.
STYLE = "mtg_fd:mtg_fd_ir105_hrfi_grayscale"
ATTRIBUTION = "EUMETSAT MTG FCI IR 10.5 µm via EUMETView"
SOURCE_ID = "satellite_ir"
LABEL = "Satellite infrared"

CADENCE = timedelta(minutes=10)
#: Cycles advertised and proxied: 3 h, the radar's own retention, so a loop
#: (#653) can step the two together.
RETAINED_FRAMES = 18
#: Newest cycle older than this is reported stale (EUMETView normally lags
#: ~20-30 min behind real time).
MAX_DISPLAY_AGE = timedelta(minutes=60)

TILE_SIZE = 256
MIN_ZOOM = 3
#: The product is 1 km; z9 is ~200 m per pixel at 45°N, past which the map
#: just scales the z9 tile.
MAX_ZOOM = 9

_TIMEOUT = 15
_CAPS_TTL_SECONDS = 120
_TILE_CACHE_SIZE = 768  # ~15 KB each
#: Concurrent calls to EUMETView across all requests.  Tile requests run on
#: the shared sync threadpool; without a cap, a slow upstream (15 s timeout)
#: or a client scanning tiles could pin threads every other endpoint needs.
_UPSTREAM_CONCURRENCY = 6
#: After an upstream failure, fail fast for this long instead of hitting a
#: struggling EUMETView once per tile.
_FAILURE_BACKOFF_SECONDS = 30.0
#: A cycle this young may have been advertised before its product was
#: complete; its tiles are re-fetched after `YOUNG_TILE_TTL_SECONDS` and sent
#: with a short browser cache (see `is_young`).
YOUNG_CYCLE = timedelta(minutes=30)
YOUNG_TILE_TTL_SECONDS = 300.0

_session = requests.Session()
_upstream = threading.BoundedSemaphore(_UPSTREAM_CONCURRENCY)
_failed_until = 0.0
_caps_lock = threading.Lock()
_caps_refresh_lock = threading.Lock()
_caps_cache: tuple[float, list[datetime]] | None = None
_tile_lock = threading.Lock()
# (stamp, z, x, y) -> (bytes, fetched_at monotonic)
_tile_cache: "OrderedDict[tuple[str, int, int, int], tuple[bytes, float]]" = OrderedDict()
# Per-tile in-flight fetches, so concurrent misses of one tile share a call.
_tile_inflight: dict[tuple[str, int, int, int], threading.Event] = {}


class SatelliteUnavailable(RuntimeError):
    """EUMETView could not be reached or answered something unusable."""


def satellite_ir_enabled() -> bool:
    """On unless ``WB_SATELLITE_IR=0`` (it also needs the observed master gate)."""
    return os.environ.get("WB_SATELLITE_IR", "1").strip().lower() not in ("0", "false", "no")


# --- Times -------------------------------------------------------------------------

_DIMENSION_RE = re.compile(r'<Dimension[^>]*name="time"[^>]*>([^<]+)</Dimension>')


def parse_time_dimension(text: str, *, keep: int = RETAINED_FRAMES) -> list[datetime]:
    """Newest-first cycle times from a WMS ``time`` dimension value.

    Handles both forms GeoServer emits: ``start/end/period`` intervals and
    comma-separated instants (or a mix).  Only ``keep`` newest are returned.
    """
    times: set[datetime] = set()
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        pieces = part.split("/")
        if len(pieces) == 3:
            end = _parse_iso(pieces[1])
            start = _parse_iso(pieces[0])
            step = _parse_period(pieces[2]) or CADENCE
            when = end
            while when >= start and len(times) < keep * 4:
                times.add(when)
                when -= step
        else:
            times.add(_parse_iso(pieces[0]))
    return sorted(times, reverse=True)[:keep]


def _parse_iso(value: str) -> datetime:
    value = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _parse_period(value: str) -> timedelta | None:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value.strip())
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def available_times() -> list[datetime]:
    """The cycles we advertise, newest first.  Cached for a couple of minutes.

    Single-flight: when the cache expires, one caller refreshes it and the
    others keep getting the previous list rather than each fetching
    capabilities.  Raises ``SatelliteUnavailable`` when the capabilities cannot
    be read and nothing is cached.
    """
    now = time.monotonic()
    with _caps_lock:
        cached = _caps_cache
    if cached is not None and now - cached[0] < _CAPS_TTL_SECONDS:
        return list(cached[1])
    if cached is not None and not _caps_refresh_lock.acquire(blocking=False):
        # Someone else is refreshing: a stale list beats a thread parked on
        # EUMETView, and the badge shows the age anyway.
        return list(cached[1])
    if cached is None:
        _caps_refresh_lock.acquire()
    try:
        with _caps_lock:
            fresh = _caps_cache
        if fresh is not None and time.monotonic() - fresh[0] < _CAPS_TTL_SECONDS:
            return list(fresh[1])  # refreshed while we waited
        return _refresh_capabilities(cached)
    finally:
        _caps_refresh_lock.release()


def _refresh_capabilities(cached: tuple[float, list[datetime]] | None) -> list[datetime]:
    global _caps_cache
    try:
        text = _upstream_get(
            CAPABILITIES_URL,
            {"service": "WMS", "request": "GetCapabilities", "version": "1.3.0"},
        ).text
        match = _DIMENSION_RE.search(text)
        if not match:
            raise SatelliteUnavailable("capabilities carry no time dimension")
        times = parse_time_dimension(match.group(1))
        if not times:
            raise SatelliteUnavailable("time dimension is empty")
    except (ValueError, SatelliteUnavailable) as exc:
        if cached is not None:
            logger.warning("EUMETView capabilities failed, serving cached times: %s", exc)
            return list(cached[1])
        raise SatelliteUnavailable(str(exc)) from exc
    with _caps_lock:
        _caps_cache = (time.monotonic(), times)
    return list(times)


def _upstream_get(url: str, params: dict) -> requests.Response:
    """One bounded, fail-fast call to EUMETView."""
    global _failed_until
    if time.monotonic() < _failed_until:
        raise SatelliteUnavailable("EUMETView failing; backing off")
    if not _upstream.acquire(timeout=_TIMEOUT):
        raise SatelliteUnavailable("too many EUMETView requests in flight")
    try:
        response = _session.get(url, params=params, timeout=_TIMEOUT)
        if response.status_code >= 500:
            raise SatelliteUnavailable(f"EUMETView answered {response.status_code}")
        response.raise_for_status()
        return response
    except (requests.RequestException, SatelliteUnavailable) as exc:
        _failed_until = time.monotonic() + _FAILURE_BACKOFF_SECONDS
        if isinstance(exc, SatelliteUnavailable):
            raise
        raise SatelliteUnavailable(str(exc)) from exc
    finally:
        _upstream.release()


def is_young(cycle: datetime, now: datetime | None = None) -> bool:
    """True while a cycle may still be completing on EUMETView's side."""
    now = now or datetime.now(timezone.utc)
    return now - cycle < YOUNG_CYCLE


def stamp(when: datetime) -> str:
    """URL stamp for a cycle, same format as the radar frames."""
    return when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M")


# --- Tiles -------------------------------------------------------------------------


def valid_tile(z: int, x: int, y: int) -> bool:
    if not (MIN_ZOOM <= z <= MAX_ZOOM):
        return False
    n = 2 ** z
    return 0 <= x < n and 0 <= y < n


def tile_bbox_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """(minx, miny, maxx, maxy) of an XYZ tile in EPSG:3857 metres."""
    origin = math.pi * 6378137.0
    size = 2 * origin / (2 ** z)
    minx = -origin + x * size
    maxy = origin - y * size
    return minx, maxy - size, minx + size, maxy


def fetch_tile(cycle: datetime, z: int, x: int, y: int) -> bytes:
    """PNG for one tile of one cycle, from cache or EUMETView."""
    key = (stamp(cycle), z, x, y)
    while True:
        with _tile_lock:
            cached = _tile_cache.get(key)
            if cached is not None and not _expired(cycle, cached[1]):
                _tile_cache.move_to_end(key)
                return cached[0]
            waiting = _tile_inflight.get(key)
            if waiting is None:
                _tile_inflight[key] = threading.Event()
                break
        # Another request is fetching this tile: wait for it, then re-read.
        if not waiting.wait(timeout=_TIMEOUT + 1):
            raise SatelliteUnavailable("tile fetch timed out")
    try:
        body = _fetch_tile_upstream(cycle, z, x, y)
        with _tile_lock:
            _tile_cache[key] = (body, time.monotonic())
            _tile_cache.move_to_end(key)
            while len(_tile_cache) > _TILE_CACHE_SIZE:
                _tile_cache.popitem(last=False)
        return body
    finally:
        with _tile_lock:
            event = _tile_inflight.pop(key, None)
        if event is not None:
            event.set()


def _expired(cycle: datetime, fetched_at: float) -> bool:
    return is_young(cycle) and time.monotonic() - fetched_at > YOUNG_TILE_TTL_SECONDS


def _fetch_tile_upstream(cycle: datetime, z: int, x: int, y: int) -> bytes:
    minx, miny, maxx, maxy = tile_bbox_3857(z, x, y)
    response = _upstream_get(
        WMS_URL,
        {
            "service": "WMS",
            "version": "1.3.0",
            "request": "GetMap",
            "layers": LAYER,
            "styles": STYLE,
            "crs": "EPSG:3857",
            "bbox": f"{minx},{miny},{maxx},{maxy}",
            "width": TILE_SIZE,
            "height": TILE_SIZE,
            "format": "image/png",
            "transparent": "true",
            "time": cycle.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    content_type = response.headers.get("Content-Type", "")
    # A WMS error arrives as HTTP 200 with an XML ServiceException body;
    # caching that as a "tile" would pin a broken image for the cycle.
    if not content_type.startswith("image/"):
        raise SatelliteUnavailable(f"GetMap answered {content_type or '(no type)'}")
    return response.content


def _reset_caches_for_tests() -> None:
    global _caps_cache, _failed_until
    with _caps_lock:
        _caps_cache = None
    with _tile_lock:
        _tile_cache.clear()
        _tile_inflight.clear()
    _failed_until = 0.0
