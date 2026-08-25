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

_STOPS_BY_SOURCE = {
    SOURCE_OPERA_DBZH: _DBZ_STOPS,
    SOURCE_OPERA_RATE: _RATE_STOPS,
    SOURCE_EUMETSAT_CTTH: _CTTH_STOPS,
}

# Faint neutral wash marking "the sensor does not look here".  Low enough not
# to fight the basemap, opaque enough to be seen as a deliberate state.
NODATA_RGBA = (120, 120, 128, 46)
DETECTION_ALPHA = 190


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
    """Map physical values to RGB by highest reached stop."""
    rgb = np.zeros(values.shape + (3,), dtype=np.uint8)
    for threshold, r, g, b in stops:
        hit = values >= threshold
        rgb[hit] = (r, g, b)
    return rgb


def render_overlay(
    frame: GridFrame,
    bounds: OverlayBounds,
    *,
    max_pixels: int = MAX_OVERLAY_PIXELS,
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

    values = np.asarray(frame.values)[safe_rows, safe_cols]
    detected = frame.detected[safe_rows, safe_cols] & inside
    nodata = (frame.nodata[safe_rows, safe_cols] & inside) | ~inside

    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    stops = _STOPS_BY_SOURCE.get(frame.source)
    if stops is not None:
        rgb = _colourise(np.nan_to_num(values, nan=-9999.0), stops)
        rgba[detected, :3] = rgb[detected]
        rgba[detected, 3] = DETECTION_ALPHA
    # Coverage holes are drawn; "looked, saw nothing" stays transparent.
    rgba[nodata] = NODATA_RGBA

    buffer = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), bounds


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
