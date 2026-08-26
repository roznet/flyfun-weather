"""Overlay rendering: what gets drawn, what stays transparent, what it costs."""

from __future__ import annotations

import io

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


# --- Parallax in the overlay -----------------------------------------------


def _ctth_frame(path):
    import netCDF4

    with netCDF4.Dataset(str(path)) as dataset:
        grid = ctth.read_grid(dataset)
    return ctth.read_window(
        path,
        GridWindow(0, grid.ny, 0, grid.nx, full_width=True),
        source=SOURCE_EUMETSAT_CTTH,
    )


# The fixture station, and a box wide enough to hold both its true position and
# the uncorrected position 0.5° north.
STATION_LAT = 50.517
TALL_BOUNDS = OverlayBounds(south=50.0, west=1.0, north=51.4, east=2.3)
# The FL250-400 stop, i.e. the fixture's FL350 cirrus.
CIRRUS_RGB = (235, 235, 245)


def _painted_latitudes(png: bytes, bounds: OverlayBounds, rgb) -> tuple[float, float]:
    """(southernmost, northernmost) latitude painted in ``rgb``."""
    image = _decode(png)
    height = image.shape[0]
    match = np.all(image[:, :, :3] == np.array(rgb, dtype=np.uint8), axis=-1)
    match &= image[:, :, 3] == DETECTION_ALPHA
    rows = np.nonzero(match.any(axis=1))[0]
    assert rows.size, "expected some cirrus to be painted"
    span = bounds.north - bounds.south

    def lat_of(row):
        return bounds.north - (row + 0.5) * span / height

    return lat_of(rows.max()), lat_of(rows.min())


def test_overlay_draws_cloud_tops_where_the_sampler_says_they_are(ctth_path):
    """The map and the numbers must agree about where a cloud is.

    The overlay used to gather each output pixel from its NOMINAL source pixel,
    which for a cloud-top product is where the satellite's line of sight hits
    the ground — not where the cloud is. The sampler corrects for that before
    deciding corridor membership; the overlay did not, so the same briefing
    could show a cell ~60 km from the position its own annuli reported.
    """
    frame = _ctth_frame(ctth_path)
    south, north = _painted_latitudes(
        render_overlay(frame, TALL_BOUNDS)[0], TALL_BOUNDS, CIRRUS_RGB
    )
    assert south <= STATION_LAT <= north, (
        f"cirrus painted at {south:.3f}..{north:.3f}, which does not cover the "
        f"station at {STATION_LAT} — the overlay is not applying parallax"
    )


def test_dropping_parallax_moves_the_overlay_cloud_far_north(ctth_path):
    """Pin the size of the error, so the fix cannot be quietly reverted."""
    frame = _ctth_frame(ctth_path)
    frame.aux.pop("delta_latitude")
    frame.aux.pop("delta_longitude")
    south, _north = _painted_latitudes(
        render_overlay(frame, TALL_BOUNDS)[0], TALL_BOUNDS, CIRRUS_RGB
    )
    displacement_km = (south - STATION_LAT) * 111.0
    assert displacement_km > 40, (
        f"expected the uncorrected overlay to sit far north of the station; "
        f"got {displacement_km:.0f} km"
    )


def test_radar_overlay_is_unaffected_by_the_parallax_path(dbzh_path):
    """A ground-projected product has nothing to correct and must not move."""
    frame = _full_frame(dbzh_path)
    assert "delta_latitude" not in frame.aux
    image = _decode(render_overlay(frame, BOUNDS)[0])
    assert (image[:, :, 3] == DETECTION_ALPHA).any()


def test_parallax_pad_grows_with_latitude():
    """The 75 km figure is a 50°N-on-the-meridian measurement, not a constant."""
    assert ctth.parallax_pad_km([45.0], [0.0]) == ctth.PARALLAX_PAD_KM
    assert ctth.parallax_pad_km([50.0], [0.0]) == ctth.PARALLAX_PAD_KM
    # A Scandinavian route needs materially more, or its high cloud is
    # truncated with no error — just missing cirrus.
    assert ctth.parallax_pad_km([65.0], [0.0]) > 2 * ctth.PARALLAX_PAD_KM
    # And it is clamped rather than running away at the limb.
    assert ctth.parallax_pad_km([85.0], [0.0]) == ctth.parallax_pad_km([70.0], [0.0])


def test_parallax_pad_grows_with_longitude_too():
    """Zenith angle depends on distance from the sub-satellite *point*.

    A latitude-only pad under-reads everywhere off the 0° meridian, which is
    most of Europe.  These are real airfields: at each one the true viewing
    geometry is more oblique than its latitude alone implies, so a
    latitude-only pad would silently truncate the high-cloud tail.
    """
    for lat, lon in [(52.2, 21.0), (56.9, 24.0), (60.3, 25.0)]:  # EPWA, EVRA, EFHK
        assert ctth.parallax_pad_km([lat], [lon]) > ctth.parallax_pad_km([lat], [0.0])

    # West of the meridian is symmetric — the angle depends on |Δlon|.
    assert ctth.parallax_pad_km([52.0], [-25.0]) == pytest.approx(
        ctth.parallax_pad_km([52.0], [25.0])
    )

    # The pad is taken from the worst point in the set, not the first or last.
    worst_alone = ctth.parallax_pad_km([60.3], [25.0])
    with_mild_neighbours = ctth.parallax_pad_km([43.5, 60.3, 45.0], [7.0, 25.0, 2.0])
    assert with_mild_neighbours == pytest.approx(worst_alone)


def test_sub_satellite_angle_exceeds_latitude_off_the_meridian():
    """Pin the geometry the pad scaling rests on."""
    # On the meridian the angle is exactly the latitude.
    assert ctth.sub_satellite_angle_deg(50.0, 0.0) == pytest.approx(50.0)
    # Off it, always more — cos(psi) = cos(lat)·cos(dlon).
    assert ctth.sub_satellite_angle_deg(50.0, 25.0) > 50.0
    # And on the equator the angle is just the longitude offset.
    assert ctth.sub_satellite_angle_deg(0.0, 30.0) == pytest.approx(30.0)
