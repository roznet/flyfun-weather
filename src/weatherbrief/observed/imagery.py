"""Render an observed frame as a map image.

Two consumers share the rendering here:

* the corridor overlay (``render_overlay``): one PNG over the route's box,
  placed by the map as a Leaflet ``imageOverlay``;
* the Europe-wide tiles (``observed.tiles``), which project each radar frame
  once per frame into a Web Mercator canvas and slice it on request.

Both paint through the same functions, so a pixel cannot look different on the
corridor overlay and on a tile.

Rendering decisions that carry meaning rather than taste:

* **``nodata`` is drawn, ``undetect`` is not.**  A pixel the radar never saw
  gets a faint neutral wash so the coverage hole is visible on the map;
  a pixel it saw and found empty is fully transparent.  Leaving both blank
  would show ~half the OPERA grid as clear sky.
* **Rows are Web Mercator, not plate-carrée.**  Leaflet places an
  ``imageOverlay`` by its corners and stretches it linearly in *Mercator*
  pixels.  A plate-carrée image was therefore drawn too far south in its
  middle rows — ~9 km across a 43–49°N corridor, ~26 km across 42–52°N —
  after the cloud-top path went to some trouble to correct a ~50 km parallax.
* **Radar is smoothed, but anchored on the VIP class colours**
  (meteorology-decisions §33).  Values are interpolated bilinearly between
  *detected* source-pixel centres only, so the result never exceeds the
  measured maximum and never borrows from an empty or unseen pixel; a class
  floor still draws that class's own colour, and the blend toward a class
  happens *below* its floor, so smoothing can only make an echo look one notch
  worse, never softer.  Light returns fade in rather than painting a wash
  over half the country.
* **Cloud tops are not smoothed.**  Their stops are picked by nearest value
  (``designs/current-conditions.md``): a blended colour would claim a
  precision the 2 km retrieval does not have.
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
from .intensity import DBZ_BANDS, RATE_BANDS, EchoIntensity

logger = logging.getLogger(__name__)

# Widest overlay we will render.  Beyond this the image is mostly ocean the
# route never touches, and the PNG stops being cheap.
MAX_OVERLAY_PIXELS = 1600

# Colour stops per quantity: (threshold, R, G, B).  A value takes the colour of
# the highest stop it reaches.  Alpha is applied separately.
#
# The reflectivity and rain-rate ramps are BUILT FROM THE SAME INTENSITY BANDS
# (``observed.intensity``) rather than written out twice, so one colour means
# one class on both.  Before this they were independent tables and disagreed:
# the 45 dBZ orange was ~24 mm/h under Marshall-Palmer, well past the rate
# ramp's own 10 mm/h orange, so the same cell drew two different colours
# depending on which layer a pilot had switched on.
_INTENSITY_RGB: dict[EchoIntensity, tuple[int, int, int]] = {
    EchoIntensity.LIGHT: (60, 190, 90),
    EchoIntensity.MODERATE: (240, 210, 60),
    EchoIntensity.HEAVY: (240, 140, 40),
    EchoIntensity.VERY_HEAVY: (225, 60, 60),
    EchoIntensity.EXTREME: (190, 60, 190),
}

# Detections the published scale does not name (below VIP 1 / below 0.5 mm/h)
# are still real measurements and are still drawn, in a blue that reads as
# "present, unclassified" rather than as the bottom of the intensity ramp.
_BELOW_SCALE_DBZ = (5.0, 90, 160, 220)
_BELOW_SCALE_RATE = (0.2, 120, 175, 225)


def _stops_from_bands(
    bands: tuple[tuple[float, EchoIntensity], ...],
    below_scale: tuple[float, int, int, int],
) -> tuple[tuple[float, int, int, int], ...]:
    """Colour stops for an intensity ladder, floor first."""
    return (below_scale,) + tuple(
        (floor,) + _INTENSITY_RGB[intensity] for floor, intensity in bands
    )


_DBZ_STOPS = _stops_from_bands(DBZ_BANDS, _BELOW_SCALE_DBZ)
_RATE_STOPS = _stops_from_bands(RATE_BANDS, _BELOW_SCALE_RATE)
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
#
# Deliberately NOT lowered to the VIP-1 floor of 18 when the ramp moved there.
# That 20 was measured against real frames and fixed a visible bug; the 18-20
# sliver draws in the light-echo green but faintly, which is honest on both
# counts (it IS VIP 1, and it IS not worth routing around).
FAINT_ECHO_DBZ = 20.0
FAINT_ALPHA = 70

# --- Smoothed radar ------------------------------------------------------------
#
# Reflectivity alpha by value, as (dBZ, alpha) knots, interpolated.  The faint
# floor above still holds — nothing below FAINT_ECHO_DBZ is drawn stronger than
# FAINT_ALPHA — but instead of two flat levels the weakest returns fade toward
# near-invisible, which is what stops sub-VIP drizzle and clutter washing a
# whole country pale blue (the 2026-10-03 Windy comparison, issue #652).  Full
# strength is reached a few dBZ above the floor so a VIP-1 cell's edge does
# not step from faint to opaque in one pixel.
_DBZ_ALPHA_KNOTS: tuple[tuple[float, float], ...] = (
    (5.0, 0.0),
    (14.0, 25.0),
    (FAINT_ECHO_DBZ, float(FAINT_ALPHA)),
    (FAINT_ECHO_DBZ + 3.0, float(DETECTION_ALPHA)),
)

# How far BELOW a class floor the colour starts blending toward that class.
# Blending below the floor, never above it, is what keeps §33 true under
# smoothing: a value at or past a floor always draws its own class colour, and
# the only intermediate colours belong to values just under the next class —
# drawn one notch worse, which is the safe direction to be wrong in.
_DBZ_BLEND = 3.0          # dBZ, linear
_RATE_BLEND_DECADES = 0.15  # mm/h, in log10 (a factor of ~1.4)

# Sources drawn smoothed.  Ground-projected radar only: cloud tops keep
# nearest-value stepping (see the module docstring).
SMOOTH_SOURCES = (SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE)

# Edge feather, in two steps rather than a continuum: below `_COVERAGE_MIN` of
# the interpolation weight on detected pixels a pixel is not drawn, up to
# `_COVERAGE_FULL` it is drawn at half alpha, above that at full.  The drawn
# edge follows the bilinear contour of the detection mask (smooth, not a
# staircase); two levels rather than a continuum keep the image within a
# 256-entry palette (see `_VALUE_BIN`).
_COVERAGE_MIN = 0.15
_COVERAGE_FULL = 0.4

# Smoothed values are binned before painting, colour taken at each bin's LOWER
# edge.  Bins are aligned on the integer class floors, so a value at or past a
# floor still draws exactly its class colour, and a value in a blend zone is
# drawn at the (pessimistic) colour of the bin's start.  Binning is what keeps
# a smoothed radar image to a few hundred distinct RGBA values, which lets it
# ship as a lossless palette PNG — ~5x smaller than RGBA, which matters for a
# tile set and more again once frames are looped (#653).
_VALUE_BIN = {SOURCE_OPERA_DBZH: 0.5, SOURCE_OPERA_RATE: 0.025}  # dBZ; log10 mm/h


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


# --- Web Mercator geometry -------------------------------------------------------

#: Latitude limit of the Web Mercator square.
MERCATOR_MAX_LAT = 85.05112878


def mercator_y(lat) -> np.ndarray:
    """Unitless Web Mercator ordinate (radians of the projected sphere)."""
    lat = np.clip(np.asarray(lat, dtype=float), -MERCATOR_MAX_LAT, MERCATOR_MAX_LAT)
    return np.log(np.tan(np.pi / 4.0 + np.radians(lat) / 2.0))


def inverse_mercator_y(y) -> np.ndarray:
    return np.degrees(2.0 * np.arctan(np.exp(np.asarray(y, dtype=float))) - np.pi / 2.0)


@dataclass(frozen=True)
class MercatorRaster:
    """An output raster whose rows are evenly spaced in Web Mercator.

    That is the geometry a Leaflet ``imageOverlay`` (and every XYZ tile) is
    drawn in, so a pixel lands where the basemap says that place is.
    """

    bounds: OverlayBounds
    width: int
    height: int

    @classmethod
    def for_bounds(cls, bounds: OverlayBounds, max_pixels: int) -> "MercatorRaster":
        x_span = np.radians(max(1e-6, bounds.east - bounds.west))
        y_span = max(1e-9, float(mercator_y(bounds.north) - mercator_y(bounds.south)))
        aspect = x_span / y_span  # square pixels in Mercator
        cap = min(max_pixels, MAX_OVERLAY_PIXELS)
        if aspect >= 1:
            width = cap
            height = max(1, int(round(width / aspect)))
        else:
            height = cap
            width = max(1, int(round(height * aspect)))
        return cls(bounds, width, height)

    def pixel_lonlat(self) -> tuple[np.ndarray, np.ndarray]:
        """(lon, lat) meshes of every output pixel centre, north row first."""
        b = self.bounds
        y_north = float(mercator_y(b.north))
        y_south = float(mercator_y(b.south))
        ys = y_north - (np.arange(self.height) + 0.5) * ((y_north - y_south) / self.height)
        lats = inverse_mercator_y(ys)
        lons = b.west + (np.arange(self.width) + 0.5) * ((b.east - b.west) / self.width)
        return np.meshgrid(lons, lats)

    def rows_for(self, lat) -> np.ndarray:
        """Fractional output row (0 = north edge) for a latitude."""
        y_north = float(mercator_y(self.bounds.north))
        y_south = float(mercator_y(self.bounds.south))
        return (y_north - mercator_y(lat)) / (y_north - y_south) * self.height

    def cols_for(self, lon) -> np.ndarray:
        b = self.bounds
        return (np.asarray(lon, dtype=float) - b.west) / max(1e-9, b.east - b.west) * self.width


# --- Painting --------------------------------------------------------------------


def _ramp_knots(stops, blend: float) -> tuple[np.ndarray, np.ndarray]:
    """Interpolation knots for a class-anchored continuous ramp.

    Each class floor gets two knots: ``floor - blend`` still in the previous
    class colour, ``floor`` in its own.  Between floors the colour is flat.
    """
    xs: list[float] = [stops[0][0]]
    colours: list[tuple[int, int, int]] = [tuple(stops[0][1:])]
    for (prev_floor, *prev_rgb), (floor, *rgb) in zip(stops, stops[1:]):
        start = max(prev_floor, floor - blend)
        if start > xs[-1]:
            xs.append(start)
            colours.append(tuple(prev_rgb))
        xs.append(floor)
        colours.append(tuple(rgb))
    return np.asarray(xs, dtype=float), np.asarray(colours, dtype=float)


_DBZ_KNOTS = _ramp_knots(_DBZ_STOPS, _DBZ_BLEND)
_RATE_KNOTS = _ramp_knots(
    tuple((float(np.log10(v)), r, g, b) for v, r, g, b in _RATE_STOPS),
    _RATE_BLEND_DECADES,
)


def smooth_colour(source: str, values: np.ndarray) -> np.ndarray:
    """RGB (float) for smoothed radar values, anchored on the class colours."""
    if source == SOURCE_OPERA_RATE:
        xs, colours = _RATE_KNOTS
        with np.errstate(divide="ignore", invalid="ignore"):
            x = np.log10(np.maximum(values, 1e-6))
    else:
        xs, colours = _DBZ_KNOTS
        x = values
    rgb = np.empty(np.shape(values) + (3,), dtype=float)
    for channel in range(3):
        rgb[..., channel] = np.interp(x, xs, colours[:, channel])
    return rgb


def smooth_alpha(source: str, values: np.ndarray) -> np.ndarray:
    """Alpha for smoothed radar values, before the edge feather."""
    if source == SOURCE_OPERA_DBZH:
        knots = np.asarray(_DBZ_ALPHA_KNOTS, dtype=float)
        return np.interp(values, knots[:, 0], knots[:, 1])
    # A rain rate has no "not worth routing around" floor: every detection is
    # drawn at full strength (see `_recede_faint_echo`).
    return np.full(np.shape(values), float(DETECTION_ALPHA))


def paint_smooth(
    source: str,
    values: np.ndarray,
    coverage: np.ndarray,
    nodata: np.ndarray,
) -> np.ndarray:
    """RGBA for a smoothed radar field.

    ``values`` is the detection-weighted interpolated value (NaN where no
    detected source pixel contributes), ``coverage`` the detected share of the
    interpolation weight (0..1), ``nodata`` the nearest-pixel coverage hole.
    Shared by the corridor overlay and the tile renderer.
    """
    rgba = np.zeros(values.shape + (4,), dtype=np.uint8)
    painted = np.isfinite(values) & (coverage >= _COVERAGE_MIN)
    if painted.any():
        v = _bin_values(source, values[painted])
        feather = np.where(coverage[painted] >= _COVERAGE_FULL, 1.0, 0.5)
        rgba[painted, :3] = np.rint(smooth_colour(source, v)).astype(np.uint8)
        rgba[painted, 3] = np.rint(smooth_alpha(source, v) * feather).astype(np.uint8)
    # As on the stepped path: the coverage hole wins, since a value bled in
    # from a neighbouring pixel must not paint over "the radar cannot see here".
    rgba[nodata] = NODATA_RGBA
    return rgba


def sample_smooth(
    plane: np.ndarray,
    detected: np.ndarray,
    rows: np.ndarray,
    cols: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Bilinear sample of ``plane`` at fractional (row, col), detections only.

    A normalised convolution: each of the four neighbours contributes its
    bilinear weight only if it is a detection.  So the result is a weighted mean
    of measured values — it can never exceed the measured maximum, and an empty
    or unseen neighbour fades the *alpha* (via ``coverage``) instead of pulling
    the value down to an invented one.  Exact at source pixel centres.

    Returns ``(values, coverage)``; ``values`` is NaN where coverage is 0.
    """
    r0 = np.floor(rows).astype(np.int64)
    c0 = np.floor(cols).astype(np.int64)
    fr = rows - r0
    fc = cols - c0
    n_rows, n_cols = plane.shape
    acc = np.zeros(rows.shape, dtype=np.float64)
    weight = np.zeros(rows.shape, dtype=np.float64)
    for dr, dc, w in (
        (0, 0, (1 - fr) * (1 - fc)),
        (0, 1, (1 - fr) * fc),
        (1, 0, fr * (1 - fc)),
        (1, 1, fr * fc),
    ):
        rr = r0 + dr
        cc = c0 + dc
        ok = (rr >= 0) & (rr < n_rows) & (cc >= 0) & (cc < n_cols)
        rr = np.clip(rr, 0, n_rows - 1)
        cc = np.clip(cc, 0, n_cols - 1)
        hit = ok & detected[rr, cc]
        w_hit = np.where(hit, w, 0.0)
        acc += w_hit * np.nan_to_num(plane[rr, cc].astype(np.float64), nan=0.0)
        weight += w_hit
    with np.errstate(invalid="ignore", divide="ignore"):
        values = np.where(weight > 0, acc / weight, np.nan)
    return values, weight


def render_overlay(
    frame: GridFrame,
    bounds: OverlayBounds,
    *,
    max_pixels: int = MAX_OVERLAY_PIXELS,
    field: str | None = None,
) -> tuple[bytes, OverlayBounds]:
    """Render ``frame`` into a Web Mercator RGBA PNG covering ``bounds``.

    Returns the PNG bytes and the bounds actually covered (identical to the
    request — the caller places the image with them).
    """
    raster = MercatorRaster.for_bounds(bounds, max_pixels)
    height, width = raster.height, raster.width
    lon_mesh, lat_mesh = raster.pixel_lonlat()

    cols_f, rows_f = _project_to_grid_frac(frame, lon_mesh, lat_mesh)
    local_rows_f = rows_f - frame.window.row0
    local_cols_f = cols_f - frame.window.col0
    local_rows = np.rint(local_rows_f).astype(int)
    local_cols = np.rint(local_cols_f).astype(int)
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
    nodata = (frame.nodata[safe_rows, safe_cols] & inside) | ~inside
    stops = _stops_for(frame.source, field)
    has_parallax = "delta_latitude" in frame.aux and "delta_longitude" in frame.aux

    if field is None and frame.source in SMOOTH_SOURCES and not has_parallax:
        values, coverage = sample_smooth(plane, frame.detected, local_rows_f, local_cols_f)
        rgba = paint_smooth(frame.source, values, coverage, nodata)
        return encode_png(rgba), bounds

    values = plane[safe_rows, safe_cols]
    detected = frame.detected[safe_rows, safe_cols] & inside
    rgba = np.zeros((height, width, 4), dtype=np.uint8)

    if stops is not None and not has_parallax:
        # Ground-projected, unsmoothed: the pixel is already where it says it
        # is, so a straight nearest gather is correct.  Skipped entirely on a
        # parallax product — the scatter below supersedes it.
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
        _scatter_parallax_detections(frame, rgba, raster, stops, field)

    return encode_png(rgba), bounds


def _bin_values(source: str, values: np.ndarray) -> np.ndarray:
    """Snap smoothed values to the lower edge of their bin (see `_VALUE_BIN`)."""
    step = _VALUE_BIN.get(source)
    if not step:
        return values
    if source == SOURCE_OPERA_RATE:
        with np.errstate(divide="ignore", invalid="ignore"):
            logs = np.log10(np.maximum(values, 1e-6))
        # The epsilon keeps an exact boundary (log10(10) == 1.0) in its own
        # bin despite float rounding in the division.
        binned = np.power(10.0, np.floor(logs / step + 1e-9) * step)
        floors = np.asarray([v for v, *_rgb in _RATE_STOPS], dtype=float)
    else:
        binned = np.floor(values / step + 1e-9) * step
        floors = np.asarray([v for v, *_rgb in _DBZ_STOPS], dtype=float)
    # Never bin a value back below a class floor it has reached: a log bin
    # grid does not line up with 0.5 / 2.5 / 30 mm/h, and a 0.5 mm/h echo
    # must still draw the light colour, not the blend just under it.
    reached = floors[np.clip(np.searchsorted(floors, values, side="right") - 1, 0, None)]
    reached = np.where(values >= floors[0], reached, -np.inf)
    return np.maximum(binned, reached)


def encode_png(rgba: np.ndarray) -> bytes:
    """PNG bytes for an RGBA raster, as a palette image when it fits in one.

    Lossless either way: a palette is used only when the raster has at most 256
    distinct RGBA values, which the binned radar paint and every stepped ramp
    do.  The palette form is several times smaller.
    """
    from PIL import Image

    buffer = io.BytesIO()
    flat = np.ascontiguousarray(rgba).view(np.uint32).reshape(rgba.shape[:2])
    colours, index = np.unique(flat, return_inverse=True)
    if colours.size <= 256:
        palette = colours.view(np.uint8).reshape(-1, 4)
        image = Image.fromarray(index.reshape(flat.shape).astype(np.uint8), mode="P")
        image.putpalette(palette[:, :3].tobytes(), rawmode="RGB")
        image.save(
            buffer,
            format="PNG",
            transparency=palette[:, 3].tobytes(),
            compress_level=9,
        )
    else:
        Image.fromarray(rgba, mode="RGBA").save(buffer, format="PNG", compress_level=9)
    return buffer.getvalue()


def _recede_faint_echo(rgba, values, detected, source: str, field: str | None) -> None:
    """Drop the alpha of sub-threshold radar returns on the stepped path.

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
    raster: MercatorRaster,
    stops,
    field: str | None = None,
) -> None:
    """Paint detected pixels at their parallax-corrected ground position.

    A scatter rather than a gather: the correction is per-pixel and not
    invertible in closed form, so we walk the source pixels and place each one
    where it belongs. Each writes a block sized to its own footprint in output
    pixels, so an overlay finer than the source grid does not come out
    stippled.
    """
    detected = frame.detected
    if not detected.any():
        return
    height, width = raster.height, raster.width
    src_rows, src_cols = np.nonzero(detected)
    grid = frame.grid
    lon, lat = grid.colrow_to_lonlat(
        src_cols + frame.window.col0, src_rows + frame.window.row0
    )
    lon = np.asarray(lon, dtype=float) + frame.aux["delta_longitude"][src_rows, src_cols]
    lat = np.asarray(lat, dtype=float) + frame.aux["delta_latitude"][src_rows, src_cols]

    with np.errstate(invalid="ignore"):
        out_rows_f = raster.rows_for(lat)
        out_cols_f = raster.cols_for(lon)
    out_rows_f = np.nan_to_num(out_rows_f, nan=-1.0, posinf=-1.0, neginf=-1.0)
    out_cols_f = np.nan_to_num(out_cols_f, nan=-1.0, posinf=-1.0, neginf=-1.0)
    out_rows = np.floor(out_rows_f).astype(int)
    out_cols = np.floor(out_cols_f).astype(int)

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
    block_rows, block_cols = _source_pixel_block(frame, raster)
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
    # Centre the block on the detection rather than hanging it south-east of
    # it, so enlarging the block does not shift the cloud.
    row_offset = (block_rows - 1) // 2
    col_offset = (block_cols - 1) // 2
    for dr in range(block_rows):
        rows_d = np.clip(out_rows + dr - row_offset, 0, height - 1)
        for dc in range(block_cols):
            cols_d = np.clip(out_cols + dc - col_offset, 0, width - 1)
            np.maximum.at(best, (rows_d, cols_d), values)

    painted = np.isfinite(best)
    if not painted.any():
        return
    rgba[painted, :3] = _colourise(best, stops)[painted]
    rgba[painted, 3] = DETECTION_ALPHA


#: Hard cap on the scatter block, so a pathologically small box cannot turn one
#: source pixel into a thousand-iteration paint loop.
_MAX_BLOCK = 16


def _source_pixel_block(frame: GridFrame, raster: MercatorRaster) -> tuple[int, int]:
    """How many output pixels one source pixel covers, at least 1 in each axis.

    Measured from the frame's own geometry at a lattice of points across the
    window — the distance, in OUTPUT pixels, between a source pixel and its
    neighbours — and the largest value kept.  The nominal grid step is not
    enough: a geostationary pixel's ground footprint stretches with distance
    from the sub-satellite point (~1.5x north-south at 45-50°N for MTG), and
    sizing blocks from the nominal 2 km left one-pixel gaps between rows — the
    overlay came out as a stipple grid.
    """
    rows_n, cols_n = frame.values.shape
    if rows_n < 2 or cols_n < 2:
        return 1, 1
    lattice_r = np.linspace(0, rows_n - 2, num=min(rows_n - 1, 9)).astype(int)
    lattice_c = np.linspace(0, cols_n - 2, num=min(cols_n - 1, 9)).astype(int)
    rr, cc = np.meshgrid(lattice_r + frame.window.row0, lattice_c + frame.window.col0)
    rr = rr.ravel()
    cc = cc.ravel()
    grid = frame.grid
    with np.errstate(invalid="ignore"):
        lon0, lat0 = grid.colrow_to_lonlat(cc, rr)
        lon_c, lat_c = grid.colrow_to_lonlat(cc + 1, rr)
        lon_r, lat_r = grid.colrow_to_lonlat(cc, rr + 1)
        row0 = raster.rows_for(np.asarray(lat0, float))
        col0 = raster.cols_for(np.asarray(lon0, float))
        spans_r = np.maximum(
            np.abs(raster.rows_for(np.asarray(lat_c, float)) - row0),
            np.abs(raster.rows_for(np.asarray(lat_r, float)) - row0),
        )
        spans_c = np.maximum(
            np.abs(raster.cols_for(np.asarray(lon_c, float)) - col0),
            np.abs(raster.cols_for(np.asarray(lon_r, float)) - col0),
        )
    spans_r = spans_r[np.isfinite(spans_r)]
    spans_c = spans_c[np.isfinite(spans_c)]
    if not spans_r.size or not spans_c.size:
        return 1, 1
    block_rows = int(np.ceil(spans_r.max()))
    block_cols = int(np.ceil(spans_c.max()))
    return max(1, min(block_rows, _MAX_BLOCK)), max(1, min(block_cols, _MAX_BLOCK))


def _project_to_grid_frac(frame: GridFrame, lon_mesh, lat_mesh):
    """Fractional (col, row) in the frame's full grid for each output pixel.

    Integers are source pixel centres.  Off-projection points come back as
    -1e9, which every caller treats as outside the frame.
    """
    grid = frame.grid
    x, y = grid.lonlat_to_xy(lon_mesh, lat_mesh)
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    with np.errstate(invalid="ignore"):
        cols = (x - grid.x0) / grid.dx
        rows = (y - grid.y0) / grid.dy
    cols = np.nan_to_num(cols, nan=-1e9, posinf=-1e9, neginf=-1e9)
    rows = np.nan_to_num(rows, nan=-1e9, posinf=-1e9, neginf=-1e9)
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
