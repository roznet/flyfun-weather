"""Radar-DPC (Italian civil protection) fill for the OPERA composite's Italy gap (#923).

The OPERA composite carries **no Italian radars**: what it shows over Italy is
the overlap of the French (Corsica), Swiss, Slovenian, Croatian and Maltese
nodes, so coverage stops on circular arcs and the south is ``nodata``.  The
Dipartimento della Protezione Civile publishes its own national mosaic, keyless,
every 5 minutes, as a 1 km GeoTIFF.  This module fills OPERA's ``nodata``
pixels from it.

**Fill, never overlay.**  A DPC value is written only where OPERA has
``nodata``.  Wherever OPERA has a value or an ``undetect``, OPERA stays
authoritative, so nothing outside the gap can change.  DPC's own three states
map onto OPERA's: ``-9999`` (outside DPC coverage) stays ``nodata``, ``-9998``
becomes ``undetect``, anything else is a measurement.

**The fill happens at read time, from a file stored beside the frame.**  The
collector stores the DPC GeoTIFF it paired with an OPERA frame as
``<stamp>.dpc.tif`` in the same source directory, *before* publishing the
frame's sidecar, and :func:`fill_block` is called from the OPERA readers.  So:

* the OPERA ``.h5`` is never rewritten, and the merge is a pure function of
  the two files: replay and the cells archive reproduce it byte for byte;
* every consumer (sampler, map tiles, cells, the verification archive) sees
  the same merged field without knowing DPC exists;
* a frame is never amended after publication.  DPC lands ~7 min after its
  nominal time and OPERA ~4, so at publication the DPC frame for the same
  slot is usually not out yet.  Rather than hold the radar frame back or
  amend it later (tile URLs are ``immutable``), the collector takes the newest
  DPC frame **inside the OPERA product's own window** (DBZH is a 10-min
  rolling maximum, RATE a 15-min product), i.e. normally the slot 5 min
  earlier, and records which one in the sidecar;
* ``WB_OBSERVED_DPC=0`` turns the fill off for every stored frame at once.

The DPC grid is static (Transverse Mercator ``lat_0=42 lon_0=12.5``, 1200 x
1400 at 1 km), and so is OPERA's, so the nearest-neighbour index from an
OPERA pixel to its DPC pixel is computed once per process per OPERA grid.

**The API is fragile.**  It was rebuilt in January 2026 and an earlier
version change broke a large site's Italian layer.  Every failure here is
logged and the OPERA frame is published unfilled; nothing in this module may
stop a radar frame.
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np

from weatherbrief.models.observed import ObservedAttribution

from .frames import _atomic_write, fill_path_for
from .grid import GridSpec, GridWindow

logger = logging.getLogger(__name__)

DPC_API = "https://radar-api.protezionecivile.it"
# The API rejects requests without a browser-style origin.
DPC_ORIGIN = "https://radar.protezionecivile.it"
_REQUEST_TIMEOUT = 10

DPC_NODATA = -9999.0
DPC_UNDETECT = -9998.0

# The published grid, used to validate what the GeoTIFF's own tags say.  A
# file whose geometry differs is refused rather than reprojected on a guess:
# a re-cut grid is exactly how a silent misregistration would arrive.
DPC_PROJ4 = (
    "+proj=tmerc +lat_0=42 +lon_0=12.5 +k=1 +x_0=0 +y_0=0 "
    "+ellps=WGS84 +units=m +no_defs"
)
DPC_NX = 1200
DPC_NY = 1400
DPC_PIXEL_M = 1000.0
# Upper-left *corner* of the upper-left pixel, metres.
DPC_UL_X = -600000.0
DPC_UL_Y = 650000.0

LICENSE_TEXT = "CC BY-SA 4.0"
ATTRIBUTION_TEXT = "Radar-DPC (Dipartimento della Protezione Civile), CC BY-SA 4.0"

# GeoTIFF tag ids (OGC GeoTIFF 1.1).
_TAG_PIXEL_SCALE = 33550
_TAG_TIEPOINT = 33922
_TAG_GEOKEYS = 34735
_KEY_RASTER_TYPE = 1025
_RASTER_PIXEL_IS_POINT = 2

#: OPERA quantity -> default DPC product.  VMI (the column maximum) stands in
#: for DBZH, SRI (surface rain rate) for RATE.  See meteorology-decisions §75.
DEFAULT_PRODUCTS = {"DBZH": "VMI", "RATE": "SRI"}


def dpc_enabled() -> bool:
    """Gate for both the collector and the read-time fill.  Off by default.

    Checked at read time too, so turning it off stops the fill for frames
    already on disk, not just for new ones.
    """
    return os.environ.get("WB_OBSERVED_DPC", "").strip().lower() in ("1", "true", "yes")


def product_for(quantity: str) -> str | None:
    """DPC product that fills an OPERA ``quantity``, or ``None``.

    ``WB_OBSERVED_DPC_DBZH_PRODUCT`` swaps the DBZH stand-in (e.g. ``CAPPI2``)
    without a code change, should VMI's over-reading prove worse than the
    holes a low CAPPI has in the Apennines.
    """
    quantity = quantity.upper()
    if quantity == "DBZH":
        override = os.environ.get("WB_OBSERVED_DPC_DBZH_PRODUCT", "").strip().upper()
        if override:
            return override
    return DEFAULT_PRODUCTS.get(quantity)


#: Where the DPC frame paired with an OPERA frame is stored.
fill_path = fill_path_for


def attribution_text(base: str) -> str:
    """``base`` with the Radar-DPC credit appended (once)."""
    if "Radar-DPC" in (base or ""):
        return base
    return f"{base} · {ATTRIBUTION_TEXT}" if base else ATTRIBUTION_TEXT


def with_attribution(attribution: ObservedAttribution) -> ObservedAttribution:
    return attribution.model_copy(update={"text": attribution_text(attribution.text)})


# --- GeoTIFF ----------------------------------------------------------------


@dataclass(frozen=True)
class DpcFrame:
    """A decoded DPC GeoTIFF: values in physical units plus its grid."""

    values: np.ndarray  # float32, markers kept as-is (-9999 / -9998)
    grid: GridSpec


def expected_grid() -> GridSpec:
    return GridSpec(
        proj4=DPC_PROJ4,
        nx=DPC_NX,
        ny=DPC_NY,
        x0=DPC_UL_X + DPC_PIXEL_M / 2.0,
        y0=DPC_UL_Y - DPC_PIXEL_M / 2.0,
        dx=DPC_PIXEL_M,
        dy=-DPC_PIXEL_M,
    )


def _raster_is_point(geokeys) -> bool:
    """True when the GeoKeyDirectory says ``RasterPixelIsPoint``."""
    if not geokeys or len(geokeys) < 4:
        return False
    count = int(geokeys[3])
    for i in range(count):
        base = 4 + 4 * i
        if base + 3 >= len(geokeys):
            break
        key, location, _n, value = (int(v) for v in geokeys[base : base + 4])
        if key == _KEY_RASTER_TYPE and location == 0:
            return value == _RASTER_PIXEL_IS_POINT
    return False


def decode_tiff(payload: bytes) -> DpcFrame:
    """Decode a DPC GeoTIFF and check its geometry against the published grid.

    The geometry comes from the file's own tie point and pixel scale, with
    the pixel-centre / pixel-corner convention read from its raster-type key
    (``PixelIsArea``, the GeoTIFF default, puts the tie point on the corner).
    Anything more than 1 m away from the published grid raises.
    """
    from PIL import Image

    with Image.open(io.BytesIO(payload)) as image:
        image.load()
        tags = image.tag_v2
        scale = tags.get(_TAG_PIXEL_SCALE)
        tie = tags.get(_TAG_TIEPOINT)
        geokeys = tags.get(_TAG_GEOKEYS)
        values = np.asarray(image, dtype=np.float32)

    ny, nx = values.shape
    expected = expected_grid()
    if (nx, ny) != (expected.nx, expected.ny):
        raise ValueError(f"DPC GeoTIFF is {nx}x{ny}, expected {expected.nx}x{expected.ny}")
    if scale is None or tie is None or len(tie) < 6:
        raise ValueError("DPC GeoTIFF carries no tie point / pixel scale")
    dx, dy = float(scale[0]), float(scale[1])
    i, j, x, y = float(tie[0]), float(tie[1]), float(tie[3]), float(tie[4])
    if _raster_is_point(geokeys):
        x0, y0 = x - i * dx, y + j * dy
    else:
        x0, y0 = x + (0.5 - i) * dx, y - (0.5 - j) * dy
    grid = GridSpec(proj4=DPC_PROJ4, nx=nx, ny=ny, x0=x0, y0=y0, dx=dx, dy=-dy)
    for name in ("x0", "y0", "dx", "dy"):
        if abs(getattr(grid, name) - getattr(expected, name)) > 1.0:
            raise ValueError(
                f"DPC GeoTIFF {name}={getattr(grid, name)} differs from the "
                f"published grid ({getattr(expected, name)})"
            )
    return DpcFrame(values=values, grid=grid)


#: Unreadable fills already logged, so a corrupt file read by every sampler
#: pass warns once rather than on every read (``lru_cache`` keeps no failures).
_UNREADABLE_LOGGED: set[tuple[str, int, int]] = set()


@lru_cache(maxsize=6)
def _load_cached(path: str, mtime_ns: int, size: int) -> DpcFrame:
    return decode_tiff(Path(path).read_bytes())


def load_fill(opera_path: Path | str) -> DpcFrame | None:
    """The DPC frame paired with an OPERA frame, or ``None``.

    ``None`` when the fill is off, no DPC frame was paired, or the stored one
    does not decode.  Never raises: an unreadable fill must not cost the
    OPERA frame.
    """
    if not dpc_enabled():
        return None
    path = fill_path(opera_path)
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key in _UNREADABLE_LOGGED:
        return None
    try:
        return _load_cached(*key)
    except Exception:
        if len(_UNREADABLE_LOGGED) > 256:
            _UNREADABLE_LOGGED.clear()
        _UNREADABLE_LOGGED.add(key)
        logger.warning("Unreadable DPC fill %s", path, exc_info=True)
        return None


# --- Reprojection index ------------------------------------------------------


@lru_cache(maxsize=4)
def fill_index(target: GridSpec, source: GridSpec) -> tuple[GridWindow, np.ndarray]:
    """Nearest ``source`` pixel for every ``target`` pixel inside its footprint.

    Returns the ``target`` window that bounds the source grid and, for each
    pixel in it, the flat index into the source array (``-1`` outside).  Both
    grids are static, so this is built once per process per OPERA grid
    (DBZH 1 km, RATE 2 km); computed in row strips to keep the transient
    small on the droplet.
    """
    # Bound the source footprint in target pixels from a dense edge walk:
    # the corners alone miss the bulge of a projected edge.
    n = 64
    edge_c = np.concatenate([
        np.linspace(0, source.nx - 1, n), np.full(n, source.nx - 1),
        np.linspace(0, source.nx - 1, n), np.zeros(n),
    ])
    edge_r = np.concatenate([
        np.zeros(n), np.linspace(0, source.ny - 1, n),
        np.full(n, source.ny - 1), np.linspace(0, source.ny - 1, n),
    ])
    lon, lat = source.colrow_to_lonlat(edge_c, edge_r)
    tx, ty = target.lonlat_to_xy(np.asarray(lon), np.asarray(lat))
    cols = (np.asarray(tx) - target.x0) / target.dx
    rows = (np.asarray(ty) - target.y0) / target.dy
    col0 = max(0, int(np.floor(np.nanmin(cols))) - 1)
    col1 = min(target.nx, int(np.ceil(np.nanmax(cols))) + 2)
    row0 = max(0, int(np.floor(np.nanmin(rows))) - 1)
    row1 = min(target.ny, int(np.ceil(np.nanmax(rows))) + 2)
    window = GridWindow(row0, max(row0, row1), col0, max(col0, col1))
    index = np.full(window.shape, -1, dtype=np.int32)
    if window.is_empty():
        return window, index

    strip = 128
    for r in range(row0, row1, strip):
        r_end = min(row1, r + strip)
        lon, lat = target.mesh_lonlat(r, r_end, col0, col1)
        sx, sy = source.lonlat_to_xy(lon, lat)
        sc = np.rint((np.asarray(sx) - source.x0) / source.dx)
        sr = np.rint((np.asarray(sy) - source.y0) / source.dy)
        ok = (
            np.isfinite(sc) & np.isfinite(sr)
            & (sc >= 0) & (sc < source.nx) & (sr >= 0) & (sr < source.ny)
        )
        flat = np.full(sc.shape, -1, dtype=np.int32)
        flat[ok] = (sr[ok] * source.nx + sc[ok]).astype(np.int32)
        index[r - row0 : r_end - row0, :] = flat
    return window, index


def fill_block(
    fill: DpcFrame,
    quantity: str,
    grid: GridSpec,
    row0: int,
    col0: int,
    values: np.ndarray,
    nodata: np.ndarray,
    undetect: np.ndarray,
) -> int:
    """Fill OPERA ``nodata`` pixels of one decoded block, in place.

    ``values``/``nodata``/``undetect`` are the block ``[row0:, col0:]`` of the
    OPERA ``grid`` as the readers decode it (``values`` NaN outside
    detections).  Returns the number of pixels that changed state.
    """
    window, index = fill_index(grid, fill.grid)
    rows, cols = values.shape
    r0, r1 = max(row0, window.row0), min(row0 + rows, window.row1)
    c0, c1 = max(col0, window.col0), min(col0 + cols, window.col1)
    if r1 <= r0 or c1 <= c0:
        return 0

    sub_index = index[r0 - window.row0 : r1 - window.row0, c0 - window.col0 : c1 - window.col0]
    block = np.s_[r0 - row0 : r1 - row0, c0 - col0 : c1 - col0]
    # Basic slices are views: the boolean-mask writes below land in the
    # caller's arrays.
    v_view, n_view, u_view = values[block], nodata[block], undetect[block]

    target = n_view & (sub_index >= 0)
    if not target.any():
        return 0
    dpc = fill.values.ravel()[sub_index[target]]
    # The markers are exact in float32: compare, don't tolerance-match.
    covered = np.isfinite(dpc) & (dpc != DPC_NODATA)
    empty = covered & (dpc == DPC_UNDETECT)
    if quantity.upper() == "RATE":
        # A zero rain rate is the radar having looked and found nothing.
        empty |= covered & (dpc <= 0.0)
    measured = covered & ~empty

    v_view[target] = np.where(measured, dpc, np.nan).astype(values.dtype)
    n_view[target] = ~covered
    u_view[target] = empty
    return int(covered.sum())


# --- Collection --------------------------------------------------------------


def _headers() -> dict[str, str]:
    return {"origin": DPC_ORIGIN, "referer": DPC_ORIGIN + "/"}


def latest_product_time(product: str, session) -> datetime | None:
    """Time of the newest published frame of ``product`` (``findLastProductByType``)."""
    response = session.get(
        f"{DPC_API}/findLastProductByType",
        params={"type": product},
        headers=_headers(),
        timeout=_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    products = (response.json() or {}).get("lastProducts") or []
    times = [p.get("time") for p in products if isinstance(p, dict) and p.get("time")]
    if not times:
        return None
    return datetime.fromtimestamp(max(int(t) for t in times) / 1000.0, tz=timezone.utc)


def _signed_url(body) -> str | None:
    if isinstance(body, str):
        text = body.strip().strip('"')
        return text if text.startswith("http") else None
    if isinstance(body, dict):
        for value in body.values():
            found = _signed_url(value)
            if found:
                return found
    return None


def download_product(product: str, when: datetime, session) -> bytes:
    """Bytes of one DPC frame: ``downloadProduct`` signs an S3 URL, then fetch it."""
    response = session.post(
        f"{DPC_API}/downloadProduct",
        json={"productType": product, "productDate": int(when.timestamp() * 1000)},
        headers=_headers(),
        timeout=_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    try:
        body = response.json()
    except ValueError:
        body = response.text
    url = _signed_url(body)
    if not url:
        raise ValueError(f"downloadProduct returned no URL: {str(body)[:200]}")
    data = session.get(url, timeout=_REQUEST_TIMEOUT)
    data.raise_for_status()
    return data.content


def pick_slot(opera_time: datetime, latest: datetime, window: timedelta) -> datetime | None:
    """DPC slot to pair with an OPERA frame, or ``None`` if none fits.

    The newest 5-min DPC slot at or before the OPERA valid time, provided it
    lies inside the OPERA product's own accumulation window: a DPC frame
    older than that describes weather OPERA's own pixels never saw.
    """
    candidate = min(latest, opera_time).astimezone(timezone.utc).replace(second=0, microsecond=0)
    candidate -= timedelta(minutes=candidate.minute % 5)
    if opera_time - candidate > window:
        return None
    return candidate


@dataclass
class DpcTick:
    """DPC state shared by the frames of one collector tick.

    The newest product time is asked once per tick, not once per OPERA frame,
    and the first failure that looks like an outage skips DPC for the rest of
    the tick: three 10 s calls per frame against a dead API would otherwise
    hold back every later OPERA frame (the cells loop's tight radar poll,
    #666, shares this path).
    """

    latest: dict[str, datetime | None] = field(default_factory=dict)
    down: bool = False
    #: Why the last ``collect_fill`` call stored nothing because something
    #: failed (``None`` when it filled, or when there was simply no slot).
    last_error: str | None = None


def collect_fill(
    opera_path: Path,
    quantity: str,
    opera_time: datetime,
    window_minutes: float,
    session,
    tick: DpcTick | None = None,
) -> dict | None:
    """Fetch and store the DPC frame for one OPERA frame; sidecar metadata or ``None``.

    Called by the collector between the OPERA payload and its sidecar, so a
    published frame's fill never changes afterwards.  Fails soft: every error
    is logged and returns ``None`` (the OPERA frame is published unfilled).
    """
    product = product_for(quantity)
    if product is None:
        return None
    tick = tick if tick is not None else DpcTick()
    tick.last_error = None
    if tick.down:
        tick.last_error = "skipped: Radar-DPC failed earlier in this tick"
        return None
    window = timedelta(minutes=max(window_minutes, 5.0))
    latest = slot = None
    try:
        first_ask = product not in tick.latest
        if first_ask:
            tick.latest[product] = latest_product_time(product, session)
        latest = tick.latest[product]
        if latest is None:
            if first_ask:
                logger.warning("Radar-DPC %s: no product listed", product)
            return None
        slot = pick_slot(opera_time, latest, window)
        if slot is None:
            logger.info(
                "Radar-DPC %s: newest %s is outside OPERA %s's window",
                product, latest.isoformat(), opera_time.isoformat(),
            )
            return None
        payload = download_product(product, slot, session)
        decode_tiff(payload)  # refuse a file we could not use before storing it
    except Exception as exc:
        if slot is not None and latest is not None and latest - slot > window:
            # A backlog catch-up asks the API's history, which may simply not
            # serve old slots: not an outage, so neither a warning nor a trip.
            logger.info(
                "Radar-DPC %s: backlog slot %s (OPERA %s) not fetched: %s",
                product, slot.isoformat(), opera_time.isoformat(), exc,
            )
            tick.last_error = f"backlog slot not served: {exc}"[:200]
        else:
            tick.down = True
            tick.last_error = f"{type(exc).__name__}: {exc}"[:200]
            logger.warning(
                "Radar-DPC %s fill for %s failed, skipping DPC for the rest of this tick: %s",
                product, opera_time, exc,
            )
        return None
    _atomic_write(fill_path(opera_path), payload)
    return {
        "source": "Radar-DPC",
        "product": product,
        "valid_time": slot.isoformat(),
        "offset_minutes": round((opera_time - slot).total_seconds() / 60.0, 1),
        "file": fill_path(opera_path).name,
        "bytes": len(payload),
        "license": LICENSE_TEXT,
    }
