"""Render an observed frame as a map overlay.

The map draws the newest frame as a **single** ``imageOverlay`` clipped to the
route corridor: no tile server, no animation, no time slider.  That is a
deliberate ceiling on the feature, not a first cut — a tiled, animated radar
loop is a different product with a different cost, and the question this layer
answers ("is that cell on my route right now?") does not need one.

Two rendering decisions carry meaning rather than taste:

* **``nodata`` is drawn, ``undetect`` is not.**  A pixel the radar never saw
  gets a faint neutral wash so the coverage hole is visible on the map;
  a pixel it saw and found empty is fully transparent.  Leaving both blank
  would show ~half the OPERA grid as clear sky.
* **Nearest-neighbour resampling.**  Interpolating reflectivity invents
  intermediate values between a 45 dBZ core and its 20 dBZ edge; the nearest
  pixel is the measurement, and at overlay resolution it is also sharper.

Output is plate-carrée RGBA PNG, which is what a Leaflet ``imageOverlay``
expects for a lat/lon rectangle.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import numpy as np

from .frames import (
    SOURCE_EUMETSAT_CTTH,
    SOURCE_OPERA_DBZH,
    SOURCE_OPERA_RATE,
    GridFrame,
)

logger = logging.getLogger(__name__)

# Widest overlay we will render.  Beyond this the image is mostly ocean the
# route never touches, and the PNG stops being cheap.
MAX_OVERLAY_PIXELS = 1600

# Colour stops per quantity: (threshold, R, G, B).  A value takes the colour of
# the highest stop it reaches.  Alpha is applied separately.
_DBZ_STOPS: tuple[tuple[float, int, int, int], ...] = (
    (5.0, 90, 160, 220),
    (20.0, 60, 190, 90),
    (35.0, 240, 210, 60),
    (45.0, 240, 140, 40),
    (55.0, 225, 60, 60),
    (65.0, 190, 60, 190),
)
_RATE_STOPS: tuple[tuple[float, int, int, int], ...] = (
    (0.2, 120, 175, 225),
    (1.0, 60, 190, 120),
    (4.0, 240, 210, 60),
    (10.0, 240, 140, 40),
    (30.0, 225, 60, 60),
)
# Cloud-top height in metres, binned to match the payload's FL histogram.
_CTTH_STOPS: tuple[tuple[float, int, int, int], ...] = (
    (0.0, 175, 185, 195),      # FL000-050 low stratus
    (1524.0, 150, 165, 200),   # FL050-150
    (4572.0, 130, 150, 215),   # FL150-250
    (7620.0, 235, 235, 245),   # FL250-400 — cold, bright, Cb/cirrus
    (12192.0, 255, 255, 255),  # FL400+
)

# Cloud-top TEMPERATURE, in kelvin, warmest first.  Mirrors the client's
# enhanced-IR ramp stop for stop (see `IR_TEMP_STOPS` in theme.ts) so the map
# and the cross-section's hover cannot disagree about what a temperature looks
# like.  Warm end is a desaturated blue rather than the conventional grayscale:
# gray is what the forecast cloud bands are.
_CTTH_TEMP_STOPS: tuple[tuple[float, int, int, int], ...] = (
    # ASCENDING in kelvin: `_colourise` applies `values >= threshold` in order,
    # so the last stop a value reaches wins. Written coldest-first, each entry
    # is "the colour for temperatures at or above this". A descending list
    # silently paints every pixel with the final stop — which is exactly what
    # the first version of this table did.
    #
    # The floor is deliberately far below any real cloud top: a value colder
    # than the first threshold matches nothing and renders black.
    (150.00, 163, 36, 58),     # colder than -123C — floor, never reached
    (193.15, 163, 36, 58),     # -80C
    (203.15, 217, 79, 61),     # -70C
    (213.15, 232, 163, 60),    # -60C
    (218.15, 224, 216, 74),    # -55C
    (223.15, 76, 199, 106),    # -50C
    (233.15, 63, 183, 216),    # -40C
    (243.15, 74, 127, 208),    # -30C  conventional ramp ends here
    (258.15, 107, 143, 192),   # -15C
    (273.15, 127, 157, 196),   #   0C
    (288.15, 143, 168, 200),   # +15C — desaturated blue, not the
                               # conventional grayscale: gray is what the
                               # forecast cloud bands are.
)

#: Fields renderable from a frame's `aux` rather than its own `values`, with
#: the ramp each uses.  Keyed by the pseudo-source the API exposes.
AUX_FIELDS: dict[str, tuple[str, str, tuple]] = {
    "eumetsat_ctth_temp": (
        SOURCE_EUMETSAT_CTTH,
        "cloud_top_temperature",
        _CTTH_TEMP_STOPS,
    ),
}

_STOPS_BY_SOURCE = {
    SOURCE_OPERA_DBZH: _DBZ_STOPS,
    SOURCE_OPERA_RATE: _RATE_STOPS,
    SOURCE_EUMETSAT_CTTH: _CTTH_STOPS,
}

# Faint neutral wash marking "the sensor does not look here".  Low enough not
# to fight the basemap, opaque enough to be seen as a deliberate state.
NODATA_RGBA = (120, 120, 128, 46)
DETECTION_ALPHA = 190

# Below this, a radar detection is cloud, drizzle or ground clutter — the same
# floor the summary prose uses (`ECHO_MENTION_DBZ`), which calls it "returns a
# pilot would not route around". It is still a real detection and is still
# drawn, but faintly: painting it at full strength made a France that Windy
# renders dry read as widely wet, because 93% of detections in a sample box
# were below this line.
FAINT_ECHO_DBZ = 20.0
FAINT_ALPHA = 70


@dataclass(frozen=True)
class OverlayBounds:
    """Geographic rectangle an overlay image covers."""

    south: float
    west: float
    north: float
    east: float

    def as_dict(self) -> dict[str, float]:
        return {
            "south": self.south,
            "west": self.west,
            "north": self.north,
            "east": self.east,
        }


def _colourise(values: np.ndarray, stops) -> np.ndarray:
    """Map physical values to RGB by highest reached stop.

    Values below the lowest stop take the lowest stop's colour rather than
    falling through. They used to keep the zero-initialised RGB and then get
    painted at full detection alpha — a quarter of every radar overlay was
    opaque BLACK dots, because OPERA reports plenty of detections below the
    5 dBZ floor of the ramp.
    """
    rgb = np.zeros(values.shape + (3,), dtype=np.uint8)
    if not len(stops):
        return rgb
    lowest = stops[0]
    rgb[:] = (lowest[1], lowest[2], lowest[3])
    for threshold, r, g, b in stops:
        hit = values >= threshold
        rgb[hit] = (r, g, b)
    return rgb


def render_overlay(
    frame: GridFrame,
    bounds: OverlayBounds,
    *,
    max_pixels: int = MAX_OVERLAY_PIXELS,
    field: str | None = None,
) -> tuple[bytes, OverlayBounds]:
    """Render ``frame`` into a plate-carrée RGBA PNG covering ``bounds``.

    Returns the PNG bytes and the bounds actually covered (identical to the
    request — the caller places the image with them).
    """
    from PIL import Image

    lat_span = max(1e-6, bounds.north - bounds.south)
    lon_span = max(1e-6, bounds.east - bounds.west)
    aspect = lon_span / lat_span
    if aspect >= 1:
        width = min(max_pixels, MAX_OVERLAY_PIXELS)
        height = max(1, int(round(width / aspect)))
    else:
        height = min(max_pixels, MAX_OVERLAY_PIXELS)
        width = max(1, int(round(height * aspect)))

    # Pixel centres of the output raster, north-first as image rows run.
    lats = bounds.north - (np.arange(height) + 0.5) * (lat_span / height)
    lons = bounds.west + (np.arange(width) + 0.5) * (lon_span / width)
    lon_mesh, lat_mesh = np.meshgrid(lons, lats)

    cols, rows = _project_to_grid(frame, lon_mesh, lat_mesh)
    local_rows = rows - frame.window.row0
    local_cols = cols - frame.window.col0
    inside = (
        (local_rows >= 0)
        & (local_rows < frame.values.shape[0])
        & (local_cols >= 0)
        & (local_cols < frame.values.shape[1])
    )
    safe_rows = np.clip(local_rows, 0, frame.values.shape[0] - 1)
    safe_cols = np.clip(local_cols, 0, frame.values.shape[1] - 1)

    # `field` renders an auxiliary plane instead of the frame's own values —
    # cloud-top TEMPERATURE rather than height. The detection and coverage
    # masks are unchanged: they describe which pixels the retrieval answered
    # for, which is the same question whichever quantity is being drawn.
    plane = _plane_for(frame, field)
    values = plane[safe_rows, safe_cols]
    detected = frame.detected[safe_rows, safe_cols] & inside
    nodata = (frame.nodata[safe_rows, safe_cols] & inside) | ~inside

    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    stops = _stops_for(frame.source, field)
    has_parallax = "delta_latitude" in frame.aux and "delta_longitude" in frame.aux

    if stops is not None and not has_parallax:
        # Ground-projected product (radar): the pixel is already where it says
        # it is, so a straight gather is correct.  Skipped entirely on a
        # parallax product — the scatter below supersedes it, and colourising
        # the whole raster first would be pure waste.
        rgb = _colourise(np.nan_to_num(values, nan=-9999.0), stops)
        rgba[detected, :3] = rgb[detected]
        rgba[detected, 3] = DETECTION_ALPHA
        _recede_faint_echo(rgba, values, detected, frame.source, field)

    # Coverage holes are drawn; "looked, saw nothing" stays transparent. Both
    # are gathered by nominal position even on a parallax product: neither
    # carries a cloud, so neither is displaced.
    rgba[nodata] = NODATA_RGBA

    if stops is not None and has_parallax:
        # A cloud-top pixel's nominal position is where the satellite's line of
        # sight hits the GROUND, not where the cloud is — up to ~70 km away at
        # European latitudes. Gathering it there would draw the cloud tens of
        # kilometres from the place the sampled annuli say it is, so the map
        # and the numbers in the same briefing would disagree about whether a
        # cell is on the route. Scatter each detection to its own corrected
        # position instead, which is the same correction `sampler.sample`
        # applies before deciding corridor membership.
        _scatter_parallax_detections(frame, rgba, bounds, stops, height, width, field)

    buffer = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), bounds


def _recede_faint_echo(rgba, values, detected, source: str, field: str | None) -> None:
    """Drop the alpha of sub-threshold radar returns.

    Reflectivity only, and only when drawing reflectivity itself: a rain RATE
    or a cloud top has no equivalent "not worth routing around" floor, and
    dimming those would hide real signal.
    """
    if field is not None or source != SOURCE_OPERA_DBZH:
        return
    faint = detected & (values < FAINT_ECHO_DBZ)
    rgba[faint, 3] = FAINT_ALPHA


def _plane_for(frame: GridFrame, field: str | None) -> np.ndarray:
    """The array to colourise: the frame's own values, or an aux plane."""
    if not field:
        return np.asarray(frame.values)
    plane = frame.aux.get(field)
    if plane is None:
        # The granule did not carry it. Better an empty overlay than one drawn
        # from the wrong quantity.
        return np.full(np.asarray(frame.values).shape, np.nan, dtype=np.float32)
    return np.asarray(plane)


def _stops_for(source: str, field: str | None):
    """Ramp for a (source, field) pair."""
    if field:
        for _pseudo, (real, aux_field, stops) in AUX_FIELDS.items():
            if real == source and aux_field == field:
                return stops
        return None
    return _STOPS_BY_SOURCE.get(source)


def _scatter_parallax_detections(
    frame: GridFrame,
    rgba: np.ndarray,
    bounds: OverlayBounds,
    stops,
    height: int,
    width: int,
    field: str | None = None,
) -> None:
    """Paint detected pixels at their parallax-corrected ground position.

    A scatter rather than a gather: the correction is per-pixel and not
    invertible in closed form, so we walk the source pixels and place each one
    where it belongs. Each writes a block sized to its own footprint in output
    pixels, so an overlay finer than the 2 km source grid does not come out
    stippled.
    """
    detected = frame.detected
    if not detected.any():
        return
    src_rows, src_cols = np.nonzero(detected)
    grid = frame.grid
    lon, lat = grid.colrow_to_lonlat(
        src_cols + frame.window.col0, src_rows + frame.window.row0
    )
    lon = np.asarray(lon, dtype=float) + frame.aux["delta_longitude"][src_rows, src_cols]
    lat = np.asarray(lat, dtype=float) + frame.aux["delta_latitude"][src_rows, src_cols]

    lat_span = max(1e-6, bounds.north - bounds.south)
    lon_span = max(1e-6, bounds.east - bounds.west)
    out_rows = np.floor((bounds.north - lat) / lat_span * height).astype(int)
    out_cols = np.floor((lon - bounds.west) / lon_span * width).astype(int)

    values = _plane_for(frame, field)[src_rows, src_cols]
    keep = (
        np.isfinite(values)
        & np.isfinite(lat)
        & np.isfinite(lon)
        & (out_rows >= 0)
        & (out_rows < height)
        & (out_cols >= 0)
        & (out_cols < width)
    )
    if not keep.any():
        return
    out_rows, out_cols, values = out_rows[keep], out_cols[keep], values[keep]

    # Resolve overlaps by VALUE, into a max-buffer, rather than by paint order.
    #
    # Sorting the points and letting the last write win only settles two
    # detections that land on the same *base* pixel.  Each detection also
    # paints a block the size of its source pixel, and parallax displacement
    # is height-dependent — so a low cloud and a high one land different
    # distances apart and their blocks can overlap at different offsets.
    # Painting those in loop order let whichever offset came last win,
    # regardless of which cloud was higher, exactly where it matters most:
    # the edge of a cell. `np.maximum.at` makes the highest top win every
    # overlap by construction.
    best = np.full((height, width), -np.inf, dtype=np.float64)
    block_rows, block_cols = _source_pixel_block(frame, bounds, height, width)
    for dr in range(block_rows):
        rows_d = np.clip(out_rows + dr, 0, height - 1)
        for dc in range(block_cols):
            cols_d = np.clip(out_cols + dc, 0, width - 1)
            np.maximum.at(best, (rows_d, cols_d), values)

    painted = np.isfinite(best)
    if not painted.any():
        return
    rgba[painted, :3] = _colourise(best, stops)[painted]
    rgba[painted, 3] = DETECTION_ALPHA


def _source_pixel_block(
    frame: GridFrame, bounds: OverlayBounds, height: int, width: int
) -> tuple[int, int]:
    """How many output pixels one source pixel covers, at least 1 in each axis."""
    lat_span = max(1e-6, bounds.north - bounds.south)
    lon_span = max(1e-6, bounds.east - bounds.west)
    source_deg_lat = abs(frame.grid.dy) / 1000.0 / 111.0
    source_deg_lon = abs(frame.grid.dx) / 1000.0 / 111.0
    block_rows = int(np.ceil(source_deg_lat / (lat_span / height)))
    block_cols = int(np.ceil(source_deg_lon / (lon_span / width)))
    # Bounded so a pathologically small bbox cannot turn one pixel into a
    # thousand-iteration paint loop.
    return max(1, min(block_rows, 16)), max(1, min(block_cols, 16))


def _project_to_grid(frame: GridFrame, lon_mesh, lat_mesh):
    """Nearest (col, row) in the frame's grid for each output pixel."""
    grid = frame.grid
    x, y = grid.lonlat_to_xy(lon_mesh, lat_mesh)
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    with np.errstate(invalid="ignore"):
        cols = np.rint((x - grid.x0) / grid.dx)
        rows = np.rint((y - grid.y0) / grid.dy)
    cols = np.nan_to_num(cols, nan=-1.0, posinf=-1.0, neginf=-1.0).astype(int)
    rows = np.nan_to_num(rows, nan=-1.0, posinf=-1.0, neginf=-1.0).astype(int)
    return cols, rows


def legend_for(source: str) -> list[dict[str, object]]:
    """Colour stops for the client's legend, so it cannot drift from the render."""
    stops = _STOPS_BY_SOURCE.get(source)
    if stops is None:
        return []
    return [
        {"value": threshold, "color": f"#{r:02x}{g:02x}{b:02x}"}
        for threshold, r, g, b in stops
    ]
