"""Frame-reader decoding: geolocation, three-state masks, attribution."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from weatherbrief.observed import ctth, lightning, opera
from weatherbrief.observed.grid import GridWindow, compute_window

from .conftest import STATION


def test_opera_grid_comes_from_the_file(dbzh_path):
    """Geolocation is read, never assumed: the OPERA domain has been re-cut."""
    grid = opera.read_grid(dbzh_path)
    assert "+proj=laea" in grid.proj4
    assert grid.nx == grid.ny == 160
    assert grid.dx == pytest.approx(2000.0)
    # Stored north-first, so the row step is negative.
    assert grid.dy == pytest.approx(-2000.0)


def test_opera_pixel_centres_are_half_a_pixel_inside_the_corner(dbzh_path):
    """ODIM corners are outer edges; the centre of (0,0) sits inside them.

    A whole-pixel error here is a kilometre of displacement — invisible in a
    smoke test and material at the 5 NM annulus.
    """
    import h5py

    grid = opera.read_grid(dbzh_path)
    with h5py.File(str(dbzh_path), "r") as handle:
        ul_lon = float(handle["where"].attrs["UL_lon"])
        ul_lat = float(handle["where"].attrs["UL_lat"])
    corner_x, corner_y = grid.lonlat_to_xy(ul_lon, ul_lat)
    assert grid.x0 - corner_x == pytest.approx(1000.0)
    assert corner_y - grid.y0 == pytest.approx(1000.0)


def test_opera_round_trips_a_known_position(dbzh_path):
    grid = opera.read_grid(dbzh_path)
    col, row = grid.lonlat_to_colrow(STATION.lon, STATION.lat)
    lon, lat = grid.colrow_to_lonlat(np.array([col]), np.array([row]))
    assert float(lon[0]) == pytest.approx(STATION.lon, abs=1e-6)
    assert float(lat[0]) == pytest.approx(STATION.lat, abs=1e-6)


def test_opera_decodes_the_three_states_separately(dbzh_path):
    grid = opera.read_grid(dbzh_path)
    window = GridWindow(0, grid.ny, 0, grid.nx)
    frame = opera.read_window(dbzh_path, "DBZH", window, source="opera_dbzh", units="dBZ")

    assert frame.nodata.any()
    assert frame.undetect.any()
    assert frame.detected.any()
    # The three states partition the grid exactly.
    assert int(frame.nodata.sum() + frame.undetect.sum() + frame.detected.sum()) == frame.values.size
    assert not (frame.nodata & frame.undetect).any()
    # Values exist only where something was detected.
    assert np.isnan(frame.values[frame.nodata]).all()
    assert np.isnan(frame.values[frame.undetect]).all()
    assert np.isfinite(frame.values[frame.detected]).all()


def test_opera_applies_gain_and_offset(dbzh_path):
    grid = opera.read_grid(dbzh_path)
    frame = opera.read_window(
        dbzh_path, "DBZH", GridWindow(0, grid.ny, 0, grid.nx),
        source="opera_dbzh", units="dBZ",
    )
    detected = frame.values[frame.detected]
    assert detected.max() == pytest.approx(45.0, abs=0.5)
    assert detected.min() >= -32.0


def test_opera_valid_time_is_the_end_of_the_accumulation(dbzh_path):
    """A rolling maximum is valid at the end of its window, not the start."""
    grid = opera.read_grid(dbzh_path)
    frame = opera.read_window(
        dbzh_path, "DBZH", GridWindow(0, grid.ny, 0, grid.nx),
        source="opera_dbzh", units="dBZ",
    )
    assert frame.valid_time == datetime(2026, 8, 25, 14, 5, tzinfo=timezone.utc)
    assert frame.window_minutes == pytest.approx(10.0)


def test_opera_window_limits_what_is_read(dbzh_path):
    grid = opera.read_grid(dbzh_path)
    window = compute_window(grid, [STATION.lat], [STATION.lon], radius_km=10.0)
    frame = opera.read_window(dbzh_path, "DBZH", window, source="opera_dbzh", units="dBZ")
    assert frame.values.shape == window.shape
    assert frame.values.size < grid.nx * grid.ny


def test_opera_attribution_is_read_from_the_frame(dbzh_path):
    """The producer varies per frame — one composite was Météo-France's."""
    frame = opera.read_window(
        dbzh_path, "DBZH", GridWindow(0, 10, 0, 10), source="opera_dbzh", units="dBZ"
    )
    attribution = frame.attribution
    assert "MeteoFrance" in (attribution.producer or "")
    assert "OPERA" in attribution.text
    assert attribution.license
    assert attribution.url


def test_opera_rejects_an_absent_quantity(rate_path):
    with pytest.raises(KeyError, match="DBZH"):
        opera.read_window(
            rate_path, "DBZH", GridWindow(0, 10, 0, 10), source="x", units="dBZ"
        )


def test_opera_rate_decodes_in_mm_per_hour(rate_path):
    grid = opera.read_grid(rate_path)
    frame = opera.read_window(
        rate_path, "RATE", GridWindow(0, grid.ny, 0, grid.nx),
        source="opera_rate", units="mm/h",
    )
    detected = frame.values[frame.detected]
    assert detected.max() == pytest.approx(12.0, abs=0.2)
    assert (detected > 0).all()


def test_opera_key_is_deterministic():
    """No listing: the collector computes the key and fetches it."""
    key = opera.opera_key(datetime(2026, 8, 25, 14, 5, tzinfo=timezone.utc), "DBZH")
    assert key == "2026/08/25/OPERA/COMP/OPERA@20260825T1405@0@DBZH.h5"


# --- CTTH ------------------------------------------------------------------


def test_ctth_grid_converts_scan_angles_to_metres(ctth_path):
    import netCDF4

    with netCDF4.Dataset(str(ctth_path)) as dataset:
        grid = ctth.read_grid(dataset)
    assert "+proj=geos" in grid.proj4
    assert grid.dx == pytest.approx(2000.0, abs=1.0)
    # MTG stores y increasing northward.
    assert grid.dy > 0


def test_ctth_no_cloud_is_undetect_not_nodata(ctth_path):
    """Only explicit cloud-free status is a positive observation of clear sky."""
    import netCDF4

    with netCDF4.Dataset(str(ctth_path)) as dataset:
        grid = ctth.read_grid(dataset)
    frame = ctth.read_window(
        ctth_path, GridWindow(0, grid.ny, 0, grid.nx, full_width=True),
        source="eumetsat_ctth",
    )
    status = frame.aux["quality_status"]
    clear = status == 1
    assert clear.any()
    assert frame.undetect[clear].all()
    assert not frame.nodata[clear].any()
    # Failed pixels include method 0 as well as fill, and are all nodata.
    failed = status == 0
    assert failed.any()
    assert frame.nodata[failed].all()


def test_ctth_keeps_the_parallax_fields(ctth_path):
    import netCDF4

    with netCDF4.Dataset(str(ctth_path)) as dataset:
        grid = ctth.read_grid(dataset)
    frame = ctth.read_window(
        ctth_path, GridWindow(0, grid.ny, 0, grid.nx, full_width=True),
        source="eumetsat_ctth",
    )
    dlat = frame.aux["delta_latitude"]
    assert dlat.min() == pytest.approx(-0.5, abs=0.011)
    # Displacement is southward at northern latitudes — never positive.
    assert dlat.max() <= 0.011


def test_ctth_full_width_read_ignores_the_column_range(ctth_path):
    """Chunks are full-width strips, so trimming columns buys nothing."""
    import netCDF4

    with netCDF4.Dataset(str(ctth_path)) as dataset:
        grid = ctth.read_grid(dataset)
    frame = ctth.read_window(
        ctth_path, GridWindow(2, 20, 5, 9, full_width=True), source="eumetsat_ctth"
    )
    assert frame.values.shape == (18, grid.nx)
    assert frame.window.col0 == 0 and frame.window.col1 == grid.nx


def test_metres_to_fl():
    assert ctth.metres_to_fl(0.0) == pytest.approx(0.0)
    assert float(ctth.metres_to_fl(10668.0)) == pytest.approx(350.0, abs=0.1)


def test_ctth_attribution(ctth_path):
    import netCDF4

    with netCDF4.Dataset(str(ctth_path)) as dataset:
        grid = ctth.read_grid(dataset)
    frame = ctth.read_window(
        ctth_path, GridWindow(0, 10, 0, grid.nx, full_width=True), source="eumetsat_ctth"
    )
    assert frame.attribution.producer == "EUMETSAT"
    assert "EUMETSAT" in frame.attribution.text


# --- Lightning -------------------------------------------------------------


def test_li_reads_flash_positions_and_times(li_path):
    frame = lightning.read_flashes(li_path, source="eumetsat_li", window_minutes=10.0)
    assert frame.lats.size == 13
    assert frame.lons.size == 13
    assert frame.times.size == 13
    assert frame.valid_time == datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc)


def test_li_raises_rather_than_reporting_a_quiet_zero(tmp_path):
    """A granule with no recognised position variables must be loud.

    Silently reporting "no lightning" over a thunderstorm is the one failure
    mode here worth crashing for.
    """
    import netCDF4

    path = tmp_path / "empty.nc"
    with netCDF4.Dataset(str(path), "w", format="NETCDF4") as dataset:
        dataset.sensing_end_time = "2026-08-25T14:00:00Z"
        dataset.createDimension("n", 1)
        dataset.createVariable("radiance", "f4", ("n",))
    with pytest.raises(KeyError, match="flash position"):
        lightning.read_flashes(path, source="eumetsat_li", window_minutes=10.0)
