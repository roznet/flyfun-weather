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


def test_drawn_colours_are_class_colours_or_blends_just_below_a_floor(dbzh_path):
    """Smoothing may add colours, but only pessimistic ones (§33).

    Every drawn colour is either a class colour or the colour of a value within
    the blend zone *under* the next class floor — never a colour that would
    read as a weaker class than the value reached.
    """
    from weatherbrief.observed.imagery import (
        _DBZ_BLEND,
        _DBZ_STOPS,
        smooth_colour,
    )

    image = _decode(render_overlay(_full_frame(dbzh_path), BOUNDS)[0])
    drawn = {tuple(int(c) for c in row[:3]) for row in image[image[:, :, 3] > 0]}
    drawn.discard(tuple(NODATA_RGBA[:3]))
    allowed = {(r, g, b) for _v, r, g, b in _DBZ_STOPS}
    for floor, *_rgb in _DBZ_STOPS[1:]:
        zone = np.arange(floor - _DBZ_BLEND, floor, 0.5)
        allowed |= {tuple(int(c) for c in np.rint(rgb)) for rgb in smooth_colour(SOURCE_OPERA_DBZH, zone)}
    assert drawn <= allowed, drawn - allowed


def test_a_class_floor_draws_exactly_its_class_colour():
    """The legend and §33 promise one colour per class; smoothing keeps that."""
    from weatherbrief.observed.frames import SOURCE_OPERA_RATE
    from weatherbrief.observed.imagery import (
        _DBZ_STOPS,
        _RATE_STOPS,
        _bin_values,
        smooth_colour,
    )

    for source, stops in ((SOURCE_OPERA_DBZH, _DBZ_STOPS), (SOURCE_OPERA_RATE, _RATE_STOPS)):
        for floor, r, g, b in stops[1:]:
            for value in (floor, floor * 1.0001 + 1e-4):
                rgb = smooth_colour(source, _bin_values(source, np.array([value])))[0]
                assert tuple(int(c) for c in np.rint(rgb)) == (r, g, b), (source, value)


def test_blending_happens_below_a_floor_never_above():
    """Just under a floor is drawn toward the HIGHER class (the safe side)."""
    from weatherbrief.observed.imagery import _DBZ_STOPS, smooth_colour

    below_heavy = smooth_colour(SOURCE_OPERA_DBZH, np.array([40.0]))[0]
    moderate = np.array(_DBZ_STOPS[2][1:], dtype=float)  # 30 dBZ
    heavy = np.array(_DBZ_STOPS[3][1:], dtype=float)  # 41 dBZ
    assert not np.allclose(below_heavy, moderate)
    assert np.linalg.norm(below_heavy - heavy) < np.linalg.norm(moderate - heavy)
    # And a mid-band value is the flat class colour.
    assert np.allclose(smooth_colour(SOURCE_OPERA_DBZH, np.array([34.0]))[0], moderate)


def test_smoothing_never_exceeds_the_measured_maximum():
    """A weighted mean of detected neighbours only: no invented peaks."""
    from weatherbrief.observed.imagery import sample_smooth

    plane = np.array([[20.0, 45.0], [30.0, np.nan]], dtype=np.float32)
    detected = np.array([[True, True], [True, False]])
    rows, cols = np.meshgrid(np.linspace(0, 1, 9), np.linspace(0, 1, 9), indexing="ij")
    values, coverage = sample_smooth(plane, detected, rows, cols)
    finite = values[np.isfinite(values)]
    assert finite.max() <= 45.0 + 1e-9
    assert finite.min() >= 20.0 - 1e-9
    # Exact at pixel centres.
    assert values[0, 8] == pytest.approx(45.0)
    # The empty corner fades the alpha (coverage), it does not drag the value
    # toward an invented low: at the undetected centre there is no value.
    assert coverage[8, 8] == pytest.approx(0.0)
    assert np.isnan(values[8, 8])


def test_overlay_rows_are_web_mercator():
    """Leaflet stretches an imageOverlay linearly in Mercator, not in latitude.

    Plate-carrée rows put the middle of a 43-49°N overlay ~9 km south of where
    the basemap draws that latitude.
    """
    from weatherbrief.observed.imagery import MercatorRaster, inverse_mercator_y, mercator_y

    bounds = OverlayBounds(south=43.0, west=0.0, north=49.0, east=8.0)
    raster = MercatorRaster.for_bounds(bounds, 1600)
    _lons, lats = raster.pixel_lonlat()
    mid = raster.height // 2
    expected = float(
        inverse_mercator_y(
            float(mercator_y(49.0)) - (mid + 0.5) * (float(mercator_y(49.0)) - float(mercator_y(43.0))) / raster.height
        )
    )
    assert lats[mid, 0] == pytest.approx(expected, abs=1e-9)
    plate_carree = 49.0 - (mid + 0.5) * 6.0 / raster.height
    assert (lats[mid, 0] - plate_carree) * 111.0 > 7.0, "rows are still plate-carrée"
    # Round trip: a row's centre latitude maps back onto that row's centre.
    assert raster.rows_for(lats[mid, 0]) == pytest.approx(mid + 0.5)
    # Square pixels in Mercator: width / height == x span / y span.
    x_span = np.radians(8.0)
    y_span = float(mercator_y(49.0) - mercator_y(43.0))
    assert raster.width / raster.height == pytest.approx(x_span / y_span, rel=0.01)


def _contiguous_cloud(frame):
    """The fixture granule with a solid deck: rows 20-60, cols 20-60 cloudy.

    The fixture's own detections are deliberately scattered (they are built for
    the sampler), so any gap test on them would be measuring real clear sky.
    Parallax is zeroed so the deck stays where its pixels are.
    """
    deck = np.zeros(frame.values.shape, dtype=bool)
    deck[20:60, 20:60] = True
    frame.nodata[:] = False
    frame.undetect[:] = ~deck
    frame.values[:] = np.where(deck, 9000.0, np.nan)
    frame.aux["delta_latitude"] = np.zeros(frame.values.shape)
    frame.aux["delta_longitude"] = np.zeros(frame.values.shape)
    return frame


def test_cloud_tops_are_not_stippled(ctth_path):
    """Each detection's block covers its real footprint, so no gap rows.

    The block used to be sized from the NOMINAL grid step; a geostationary
    pixel's footprint is taller than that at European latitudes, which left a
    one-pixel transparent line between rows of cloud.
    """
    from weatherbrief.observed.imagery import MercatorRaster, _source_pixel_block

    frame = _contiguous_cloud(_ctth_frame(ctth_path))
    lon, lat = frame.grid.colrow_to_lonlat(np.array([20, 59]), np.array([20, 59]))
    bounds = OverlayBounds(
        south=float(np.min(lat)) - 0.05, west=float(np.min(lon)) - 0.05,
        north=float(np.max(lat)) + 0.05, east=float(np.max(lon)) + 0.05,
    )
    # A zoom where one source pixel is several output pixels but well under
    # the block cap, so the test measures the footprint, not the cap.
    max_pixels = 500
    block = _source_pixel_block(frame, MercatorRaster.for_bounds(bounds, max_pixels))
    assert 2 <= max(block) < 16
    alpha = _decode(render_overlay(frame, bounds, max_pixels=max_pixels)[0])[:, :, 3]
    painted = alpha == DETECTION_ALPHA
    rows = np.nonzero(painted.any(axis=1))[0]
    cols = np.nonzero(painted.any(axis=0))[0]
    assert rows.size > 10 and cols.size > 10, "deck not painted"
    # The deck is a parallelogram in lat/lon (the geostationary grid is
    # skewed), so test its interior, not its bounding box.
    trim_r = (rows.max() - rows.min()) // 6
    trim_c = (cols.max() - cols.min()) // 6
    inner = painted[rows.min() + trim_r : rows.max() - trim_r, cols.min() + trim_c : cols.max() - trim_c]
    assert inner.all(), f"{(~inner).sum()} transparent pixels inside a solid deck"


def test_radar_png_is_a_palette_image(dbzh_path):
    """Binned paint fits a lossless palette: several times smaller than RGBA."""
    from PIL import Image

    png = render_overlay(_full_frame(dbzh_path), BOUNDS)[0]
    assert Image.open(io.BytesIO(png)).mode == "P"


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
    from weatherbrief.observed.imagery import inverse_mercator_y, mercator_y

    # Rows are Web Mercator (see test_overlay_rows_are_web_mercator).
    y_north = float(mercator_y(bounds.north))
    y_span = y_north - float(mercator_y(bounds.south))

    def lat_of(row):
        return float(inverse_mercator_y(y_north - (row + 0.5) * y_span / height))

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


# --- Aux-field overlays (map layer selector, #574) --------------------------


def test_temperature_overlay_draws_a_different_quantity(ctth_path):
    """The map can colour the CTTH granule by temperature, not just height.

    Worth having on a map precisely because there is no altitude axis there —
    temperature is new information. On the cross-section the opposite is true,
    which is why that one colours by share instead.
    """
    from weatherbrief.observed.imagery import AUX_FIELDS

    frame = _ctth_frame(ctth_path)
    height_png, _ = render_overlay(frame, BOUNDS)
    temp_png, _ = render_overlay(frame, BOUNDS, field="cloud_top_temperature")

    assert temp_png[:8] == b"\x89PNG\r\n\x1a\n"
    assert temp_png != height_png, "temperature render is identical to height"
    # The fixture's two decks differ in temperature (cirrus -50C, stratus +8C),
    # so the render must use more than one colour.
    drawn = {tuple(row[:3]) for row in _decode(temp_png)[_decode(temp_png)[:, :, 3] == DETECTION_ALPHA]}
    assert len(drawn) >= 2

    # And the pseudo-source is wired to that field.
    assert AUX_FIELDS["eumetsat_ctth_temp"][1] == "cloud_top_temperature"


def test_a_missing_aux_field_draws_nothing_rather_than_the_wrong_thing(ctth_path):
    """An older cached frame without the plane must not fall back to height."""
    frame = _ctth_frame(ctth_path)
    frame.aux.pop("cloud_top_temperature", None)
    png, _ = render_overlay(frame, BOUNDS, field="cloud_top_temperature")
    image = _decode(png)
    assert not (image[:, :, 3] == DETECTION_ALPHA).any(), (
        "drew detections from a plane the granule does not carry"
    )


# --- Echo thresholds --------------------------------------------------------


def test_a_detection_below_the_lowest_stop_is_not_painted_black(dbzh_path):
    """The ramp's floor is 5 dBZ and OPERA reports plenty of returns below it.

    `_colourise` used to leave those at the zero-initialised RGB and then paint
    them at full detection alpha, so a quarter of every radar overlay was
    opaque BLACK dots. Measured on a real frame: 39,996 of 151,147 drawn
    pixels.
    """
    from weatherbrief.observed.imagery import _DBZ_STOPS, _colourise

    below = np.array([-30.0, -5.0, 0.0, 4.9])
    rgb = _colourise(below, _DBZ_STOPS)
    assert not (rgb == 0).all(axis=-1).any(), "value below the lowest stop rendered black"
    # It takes the lowest stop's colour, not an invented one.
    lowest = tuple(_DBZ_STOPS[0][1:])
    assert all(tuple(row) == lowest for row in rgb)


def test_light_echo_is_drawn_faintly_not_at_full_strength(dbzh_path):
    """93% of detections in a sampled box were below 20 dBZ.

    That is cloud, drizzle and ground clutter — the same floor the summary
    prose uses, which calls it "returns a pilot would not route around".
    Painting it at full strength made a France that Windy renders dry read as
    widely wet. It is still drawn, because it is still a real detection.
    """
    from weatherbrief.observed.imagery import FAINT_ALPHA

    from weatherbrief.observed.imagery import smooth_alpha

    frame = _full_frame(dbzh_path)
    alpha = _decode(render_overlay(frame, BOUNDS)[0])[:, :, 3]
    assert ((alpha > 0) & (alpha <= FAINT_ALPHA)).any(), "no faint echo drawn at all"
    assert (alpha == DETECTION_ALPHA).any(), "no full-strength echo drawn"
    assert FAINT_ALPHA < DETECTION_ALPHA
    # The rule itself: nothing below the floor is drawn stronger than faint,
    # and the weakest returns fade toward invisible rather than a flat wash.
    below = smooth_alpha(SOURCE_OPERA_DBZH, np.array([5.0, 12.0, 19.9]))
    assert (below <= FAINT_ALPHA).all()
    assert below[0] < below[1] < below[2]


def test_rain_rate_and_cloud_tops_are_not_dimmed(rate_path, ctth_path):
    """The faint floor is reflectivity-specific.

    A rain RATE or a cloud top has no equivalent "not worth routing around"
    line, so dimming them by the same rule would hide real signal.
    """
    from weatherbrief.observed.frames import SOURCE_OPERA_RATE
    from weatherbrief.observed.imagery import FAINT_ALPHA

    rate = _full_frame(rate_path, quantity="RATE", source=SOURCE_OPERA_RATE)
    alpha = _decode(render_overlay(rate, BOUNDS)[0])[:, :, 3]
    assert not (alpha == FAINT_ALPHA).any(), "rain rate was dimmed like reflectivity"
