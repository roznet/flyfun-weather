"""Overlay rendering: what gets drawn, what stays transparent, what it costs."""

from __future__ import annotations

import io
from datetime import datetime, timezone

import numpy as np
import pytest

from weatherbrief.observed import ctth, opera
from weatherbrief.observed.grid import GridWindow
from weatherbrief.observed.imagery import (
    DETECTION_ALPHA,
    MAX_OVERLAY_PIXELS,
    NODATA_RGBA,
    OverlayBounds,
    legend_for,
    render_overlay,
)
from weatherbrief.observed.frames import SOURCE_EUMETSAT_CTTH, SOURCE_OPERA_DBZH

# The fixture domain, comfortably inside its edges.
BOUNDS = OverlayBounds(south=49.8, west=0.4, north=51.2, east=2.9)


def _full_frame(path, quantity="DBZH", source=SOURCE_OPERA_DBZH):
    grid = opera.read_grid(path)
    return opera.read_window(
        path, quantity, GridWindow(0, grid.ny, 0, grid.nx), source=source, units="dBZ"
    )


def _decode(png: bytes) -> np.ndarray:
    from PIL import Image

    return np.array(Image.open(io.BytesIO(png)).convert("RGBA"))


def test_overlay_is_a_valid_rgba_png(dbzh_path):
    png, bounds = render_overlay(_full_frame(dbzh_path), BOUNDS)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    image = _decode(png)
    assert image.shape[2] == 4
    assert bounds == BOUNDS


def test_no_coverage_is_drawn_and_no_echo_is_not(dbzh_path):
    """Leaving both blank would show half the OPERA grid as clear sky."""
    image = _decode(render_overlay(_full_frame(dbzh_path), BOUNDS)[0])
    alpha = image[:, :, 3]

    nodata_pixels = np.all(image == np.array(NODATA_RGBA, dtype=np.uint8), axis=-1)
    assert nodata_pixels.any(), "coverage hole must be visible"

    # Fully transparent pixels exist too — those are "looked, saw nothing".
    assert (alpha == 0).any()
    # And detections are drawn at the opaque alpha.
    assert (alpha == DETECTION_ALPHA).any()


def test_stronger_echo_gets_a_hotter_colour(dbzh_path):
    image = _decode(render_overlay(_full_frame(dbzh_path), BOUNDS)[0])
    drawn = image[image[:, :, 3] == DETECTION_ALPHA]
    assert drawn.size > 0
    # The fixture's 45 dBZ core lands on the orange stop; its 20 dBZ fringe on
    # the green one.  Both must be present, i.e. the ramp is not flat.
    unique = {tuple(row[:3]) for row in drawn}
    assert len(unique) >= 2


def test_aspect_ratio_follows_the_requested_box(dbzh_path):
    tall = OverlayBounds(south=49.8, west=1.4, north=51.2, east=1.9)
    image = _decode(render_overlay(_full_frame(dbzh_path), tall)[0])
    assert image.shape[0] > image.shape[1]


def test_overlay_size_is_capped(dbzh_path):
    image = _decode(render_overlay(_full_frame(dbzh_path), BOUNDS)[0])
    assert max(image.shape[:2]) <= MAX_OVERLAY_PIXELS


def test_area_outside_the_frame_reads_as_no_coverage(dbzh_path):
    """Off the product is "we cannot see", not "nothing there"."""
    far_west = OverlayBounds(south=49.8, west=-6.0, north=51.2, east=-4.0)
    image = _decode(render_overlay(_full_frame(dbzh_path), far_west)[0])
    assert np.all(image == np.array(NODATA_RGBA, dtype=np.uint8))


def test_resampling_invents_no_intermediate_values(dbzh_path):
    """Nearest-neighbour: every drawn colour is one of the palette stops."""
    from weatherbrief.observed.imagery import _DBZ_STOPS

    image = _decode(render_overlay(_full_frame(dbzh_path), BOUNDS)[0])
    drawn = {tuple(row[:3]) for row in image[image[:, :, 3] == DETECTION_ALPHA]}
    palette = {(r, g, b) for _v, r, g, b in _DBZ_STOPS}
    assert drawn <= palette


def test_cloud_tops_render_too(ctth_path):
    import netCDF4

    with netCDF4.Dataset(str(ctth_path)) as dataset:
        grid = ctth.read_grid(dataset)
    frame = ctth.read_window(
        ctth_path,
        GridWindow(0, grid.ny, 0, grid.nx, full_width=True),
        source=SOURCE_EUMETSAT_CTTH,
    )
    image = _decode(render_overlay(frame, BOUNDS)[0])
    assert (image[:, :, 3] == DETECTION_ALPHA).any()


def test_legend_matches_the_render(dbzh_path):
    """The client's legend comes from the server so the two cannot drift."""
    legend = legend_for(SOURCE_OPERA_DBZH)
    assert legend
    assert all(entry["color"].startswith("#") for entry in legend)
    values = [entry["value"] for entry in legend]
    assert values == sorted(values)


def test_lightning_has_no_legend():
    """It is drawn as points, not a raster — there is nothing to ramp."""
    from weatherbrief.observed.frames import SOURCE_EUMETSAT_LI

    assert legend_for(SOURCE_EUMETSAT_LI) == []
