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
# Geometric cloud-top height in metres MSL, matching the legacy height bins.
_CTTH_STOPS: tuple[tuple[float, int, int, int], ...] = (
    (0.0, 175, 185, 195),      # 0–5000 ft MSL
    (1524.0, 150, 165, 200),   # 5000–15000 ft MSL
    (4572.0, 130, 150, 215),   # 15000–25000 ft MSL
    (7620.0, 235, 235, 245),   # 25000–40000 ft MSL
    (12192.0, 255, 255, 255),  # 40000+ ft MSL
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

# Weak returns can include drizzle or non-precipitation echoes. This is a
# visual de-emphasis threshold, not a classifier or safe-routing threshold.
# Detections remain visible; intensity alone cannot establish their cause.
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

    Reflectivity only: the visual threshold does not apply to rain rate or
    cloud tops, and carries no claim about safe routing.
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
    invertible in closed form. Project each cell's corners and translate them
    by its supplied parallax offset. Their bounding rectangle approximates the
    footprint without treating projected metres as latitude/longitude degrees.
    This is a display rasterisation, not an area-conservative cloud mask.
    """
    detected = frame.detected
    if not detected.any():
        return
    all_rows, all_cols = np.nonzero(detected)
    # A full-width CTTH strip can have millions of detections. Keep the global
    # height order, but bound four-corner projection/colour temporaries to a
    # small batch instead of allocating Nx4 arrays for the entire strip.
    order = np.argsort(frame.values[all_rows, all_cols], kind="stable")
    grid = frame.grid
    plane = _plane_for(frame, field)
    batch_pixels = 8192
    for start in range(0, len(order), batch_pixels):
        selected = order[start:start + batch_pixels]
        src_rows, src_cols = all_rows[selected], all_cols[selected]
        corner_cols = src_cols[:, None] + frame.window.col0 + [-0.5, 0.5, 0.5, -0.5]
        corner_rows = src_rows[:, None] + frame.window.row0 + [-0.5, -0.5, 0.5, 0.5]
        lon, lat = grid.colrow_to_lonlat(corner_cols, corner_rows)
        lon = np.asarray(lon) + frame.aux["delta_longitude"][src_rows, src_cols, None]
        lat = np.asarray(lat) + frame.aux["delta_latitude"][src_rows, src_cols, None]
        west, east = lon.min(axis=1), lon.max(axis=1)
        south, north = lat.min(axis=1), lat.max(axis=1)
        tops = frame.values[src_rows, src_cols]
        values = plane[src_rows, src_cols]
        keep = (
            np.isfinite(tops)
            & np.isfinite(lat).all(axis=1)
            & np.isfinite(lon).all(axis=1)
            & (north > bounds.south)
            & (south < bounds.north)
            & (east > bounds.west)
            & (west < bounds.east)
        )
        if not keep.any():
            continue
        values, tops = values[keep], tops[keep]
        lat_span = max(1e-6, bounds.north - bounds.south)
        lon_span = max(1e-6, bounds.east - bounds.west)
        # Include output pixel centres inside the footprint, or its nearest pixel
        # for sub-pixel detections. Clip whole rectangles, not individual writes:
        # an off-image centre can still have a visible footprint, and nothing is
        # piled onto the boundary. No 16px cap that truncates clouds when zoomed in.
        row0 = np.ceil((bounds.north - north[keep]) / lat_span * height - 0.5)
        row1 = np.ceil((bounds.north - south[keep]) / lat_span * height - 0.5)
        col0 = np.ceil((west[keep] - bounds.west) / lon_span * width - 0.5)
        col1 = np.ceil((east[keep] - bounds.west) / lon_span * width - 0.5)
        row0 = np.clip(row0, 0, height - 1).astype(int)
        col0 = np.clip(col0, 0, width - 1).astype(int)
        row1 = np.clip(np.maximum(row1, row0 + 1), 1, height).astype(int)
        col1 = np.clip(np.maximum(col1, col0 + 1), 1, width).astype(int)

        colours = np.empty((len(values), 4), dtype=np.uint8)
        colours[:, :3] = _colourise(values, stops)
        colours[:, 3] = DETECTION_ALPHA
        # A missing temperature of the highest top is unknown, not permission to
        # show a lower cloud's temperature through it.
        colours[~np.isfinite(values)] = NODATA_RGBA
        # Both the batches and their complete footprints are ordered globally
        # low-to-high, so cross-batch overlaps also retain the highest top.
        for i in range(len(values)):
            rgba[row0[i]:row1[i], col0[i]:col1[i]] = colours[i]


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
    """Colour stops for the client's legend, so it cannot drift from the render.

    Accepts a pseudo-source too (``eumetsat_ctth_temp``), because the map's
    legend has to describe whichever quantity is actually being drawn — and a
    temperature ramp labelled in metres would be worse than no legend at all.
    """
    entry = AUX_FIELDS.get(source)
    if entry is not None:
        stops = entry[2]
    else:
        stops = _STOPS_BY_SOURCE.get(source)
    if stops is None:
        return []
    return [
        {"value": threshold, "color": f"#{r:02x}{g:02x}{b:02x}"}
        for threshold, r, g, b in stops
    ]
