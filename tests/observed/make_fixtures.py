#!/usr/bin/env python3
"""Regenerate the small observed-conditions fixtures under ``tests/observed/data``.

The real products are 4–95 MB per frame and none of them may be redistributed
from this repository, so the committed fixtures are *synthetic granules with
real structure*: the same ODIM group layout, the same MTG geostationary
projection variable, the same fill/scale conventions, cut down to a few
hundred pixels.  Every attribute this codebase reads is present and has the
shape the real file gives it.

Two scenes are deliberately constructed rather than random:

* the radar composite has a hard-edged no-coverage block over its western
  half, so a station near the boundary exercises the ``nodata`` vs
  ``undetect`` split that 49.4% of the real OPERA grid depends on;
* the CTTH granule places its only cirrus **north** of the target station with
  ``delta_latitude`` set so the corrected ground position lands on it.  That
  makes parallax load-bearing: drop the correction and the station's cloud
  tops vanish, which is what ``test_sampler.py`` asserts.

Run: ``python tests/observed/make_fixtures.py``
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

DATA_DIR = Path(__file__).parent / "data"

# A slice of the real OPERA ODYSSEY projection definition.
OPERA_PROJ4 = (
    "+proj=laea +lat_0=55.0 +lon_0=10.0 +x_0=1950000.0 +y_0=-2100000.0 "
    "+units=m +ellps=WGS84 +no_defs"
)
OPERA_SCALE = 2000.0
OPERA_SIZE = 160  # 320 km square — enough for a 20 NM disc plus margin

# Station the fixtures are built around: LFAT (Le Touquet), one of the
# waypoints from the 2026-05-04 reference flight.
STATION_LAT = 50.517
STATION_LON = 1.627

MTG_HEIGHT = 35786400.0
MTG_SEMI_MAJOR = 6378137.0
MTG_SEMI_MINOR = 6356752.0
MTG_PIXEL_M = 2000.0
CTTH_ROWS = 120  # ~240 km N-S: covers the ~72 km parallax reach with margin
CTTH_COLS = 80


def _opera_grid():
    from pyproj import CRS, Transformer

    crs = CRS.from_proj4(OPERA_PROJ4)
    fwd = Transformer.from_crs(CRS.from_epsg(4326), crs, always_xy=True)
    inv = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
    cx, cy = fwd.transform(STATION_LON, STATION_LAT)
    half = OPERA_SIZE * OPERA_SCALE / 2.0
    # ODIM corner coordinates are the OUTER edges of the corner pixels.
    ul_x, ul_y = cx - half, cy + half
    corners = {
        "UL": (ul_x, ul_y),
        "UR": (ul_x + OPERA_SIZE * OPERA_SCALE, ul_y),
        "LL": (ul_x, ul_y - OPERA_SIZE * OPERA_SCALE),
        "LR": (ul_x + OPERA_SIZE * OPERA_SCALE, ul_y - OPERA_SIZE * OPERA_SCALE),
    }
    return {k: inv.transform(x, y) for k, (x, y) in corners.items()}, (cx, cy)


def write_opera(path: Path, quantity: str) -> None:
    import h5py

    corners, _centre = _opera_grid()

    # uint8 payload: 255 = nodata (no radar coverage), 0 = undetect (looked,
    # nothing there), everything else a real measurement.
    raw = np.zeros((OPERA_SIZE, OPERA_SIZE), dtype=np.uint8)
    raw[:, : OPERA_SIZE // 2] = 255  # western half has no radar coverage

    # An echo core just east of the station, decaying outward.
    rows, cols = np.mgrid[0:OPERA_SIZE, 0:OPERA_SIZE]
    centre_row, centre_col = OPERA_SIZE // 2, OPERA_SIZE // 2 + 4
    dist_px = np.hypot(rows - centre_row, cols - centre_col)
    if quantity == "DBZH":
        gain, offset = 0.5, -32.0
        physical = np.clip(45.0 - dist_px * 2.0, -31.5, 60.0)
    else:  # RATE
        gain, offset = 0.1, 0.0
        physical = np.clip(12.0 - dist_px * 0.8, 0.1, 40.0)
    encoded = np.clip(np.round((physical - offset) / gain), 1, 254).astype(np.uint8)
    # Wide enough that the fringe decays BELOW 20 dBZ (45 - 14*2 = 17), so the
    # fixture contains the drizzle/clutter band that dominates real frames —
    # 93% of detections in a sampled box were under 20 dBZ — and the rendering
    # rules that treat it differently are actually exercised.
    echo = dist_px <= 14
    raw = np.where(echo & (raw != 255), encoded, raw)

    with h5py.File(str(path), "w") as handle:
        what = handle.create_group("what")
        what.attrs["object"] = np.bytes_(b"COMP")
        what.attrs["version"] = np.bytes_(b"H5rad 2.2")
        what.attrs["date"] = np.bytes_(b"20260825")
        what.attrs["time"] = np.bytes_(b"140500")
        what.attrs["source"] = np.bytes_(b"ORG:247,CMT:MeteoFrance")

        where = handle.create_group("where")
        where.attrs["projdef"] = np.bytes_(OPERA_PROJ4.encode())
        where.attrs["xsize"] = np.int64(OPERA_SIZE)
        where.attrs["ysize"] = np.int64(OPERA_SIZE)
        where.attrs["xscale"] = np.float64(OPERA_SCALE)
        where.attrs["yscale"] = np.float64(OPERA_SCALE)
        for corner, (lon, lat) in corners.items():
            where.attrs[f"{corner}_lon"] = np.float64(lon)
            where.attrs[f"{corner}_lat"] = np.float64(lat)

        how = handle.create_group("how")
        how.attrs["nodes"] = np.bytes_(b"'frtro','frbol','uklew'")
        how.attrs["system"] = np.bytes_(b"OPERA ODYSSEY")
        how.attrs["license"] = np.bytes_(
            b"EUMETNET OPERA data policy - non-commercial use"
        )
        how.attrs["reference"] = np.bytes_(b"https://www.eumetnet.eu/activities/observations-programme/current-activities/opera/")

        dataset = handle.create_group("dataset1")
        dwhat = dataset.create_group("what")
        dwhat.attrs["product"] = np.bytes_(b"COMP")
        dwhat.attrs["startdate"] = np.bytes_(b"20260825")
        dwhat.attrs["starttime"] = np.bytes_(b"135500")
        dwhat.attrs["enddate"] = np.bytes_(b"20260825")
        dwhat.attrs["endtime"] = np.bytes_(b"140500")

        data = dataset.create_group("data1")
        dgroup = data.create_group("what")
        dgroup.attrs["quantity"] = np.bytes_(quantity.encode())
        dgroup.attrs["gain"] = np.float64(gain)
        dgroup.attrs["offset"] = np.float64(offset)
        dgroup.attrs["nodata"] = np.float64(255.0)
        dgroup.attrs["undetect"] = np.float64(0.0)
        data.create_dataset("data", data=raw, compression="gzip")


def write_ctth(path: Path) -> None:
    import netCDF4
    from pyproj import CRS, Transformer

    proj4 = (
        f"+proj=geos +lon_0=0 +h={MTG_HEIGHT} +a={MTG_SEMI_MAJOR} "
        f"+b={MTG_SEMI_MINOR} +sweep=y +units=m +no_defs"
    )
    crs = CRS.from_proj4(proj4)
    fwd = Transformer.from_crs(CRS.from_epsg(4326), crs, always_xy=True)
    inv = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
    cx, cy = fwd.transform(STATION_LON, STATION_LAT)

    # y increases northward, as it does in the real granule.
    x = cx + (np.arange(CTTH_COLS) - CTTH_COLS // 2) * MTG_PIXEL_M
    y = cy + (np.arange(CTTH_ROWS) - CTTH_ROWS // 4) * MTG_PIXEL_M
    xx, yy = np.meshgrid(x, y)
    lon, lat = inv.transform(xx, yy)

    height = np.full((CTTH_ROWS, CTTH_COLS), np.nan, dtype=np.float32)
    quality = np.zeros((CTTH_ROWS, CTTH_COLS), dtype=np.int8)  # 0 = no cloud
    dlat = np.zeros((CTTH_ROWS, CTTH_COLS), dtype=np.float32)
    dlon = np.zeros((CTTH_ROWS, CTTH_COLS), dtype=np.float32)
    # The three optional planes. Values are chosen so the two decks are
    # distinguishable in a test: the cirrus is COLD and SEMI-TRANSPARENT, the
    # stratus is warm and solid. That contrast is the point of carrying
    # opacity at all — height alone renders both identically.
    temperature = np.full((CTTH_ROWS, CTTH_COLS), np.nan, dtype=np.float32)
    cloudiness = np.full((CTTH_ROWS, CTTH_COLS), np.nan, dtype=np.float32)
    aviation = np.full((CTTH_ROWS, CTTH_COLS), np.nan, dtype=np.float32)

    # Cirrus at FL350 whose imagery position sits 0.5° NORTH of the station:
    # the satellite's line of sight to it strikes the ground north of where
    # the cloud actually is, exactly as the real product behaves at 50°N.
    displaced_lat = STATION_LAT + 0.5
    cirrus = (np.abs(lat - displaced_lat) < 0.06) & (np.abs(lon - STATION_LON) < 0.25)
    height[cirrus] = 10668.0  # FL350
    quality[cirrus] = 6  # opaque IR, cold cloud
    dlat[cirrus] = -0.5
    dlon[cirrus] = 0.0
    temperature[cirrus] = 223.15  # -50C
    cloudiness[cirrus] = 0.35     # thin: you can see through this
    aviation[cirrus] = 34.0       # FL/10 -> FL340, below the geometric FL350

    # Low stratus sitting directly over the station: barely displaced, so it
    # is found with or without the correction.
    stratus = (np.abs(lat - (STATION_LAT + 0.03)) < 0.04) & (
        np.abs(lon - STATION_LON) < 0.12
    )
    stratus &= ~cirrus
    height[stratus] = 1219.0  # FL040
    quality[stratus] = 1
    dlat[stratus] = -0.03
    temperature[stratus] = 281.15  # +8C
    cloudiness[stratus] = 0.98     # solid
    aviation[stratus] = 4.0        # FL040

    # A strip with no retrieval at all (off-swath / failed) in the far south.
    failed = lat < STATION_LAT - 0.35
    height[failed] = np.nan
    quality[failed] = -128  # fill

    with netCDF4.Dataset(str(path), "w", format="NETCDF4") as ds:
        ds.institution = "EUMETSAT"
        ds.license = "EUMETSAT Data Policy - free and open access"
        ds.references = "https://navigator.eumetsat.int/product/EO:EUM:DAT:0681"
        ds.sensing_end_time = "2026-08-25T14:00:00Z"
        ds.createDimension("y", CTTH_ROWS)
        ds.createDimension("x", CTTH_COLS)

        proj_var = ds.createVariable("mtg_geos_projection", "i4")
        proj_var.perspective_point_height = MTG_HEIGHT
        proj_var.semi_major_axis = MTG_SEMI_MAJOR
        proj_var.semi_minor_axis = MTG_SEMI_MINOR
        proj_var.sweep_angle_axis = "y"
        proj_var.longitude_of_projection_origin = 0.0

        # Scan-angle radians, as the real granule stores them.
        xv = ds.createVariable("x", "f8", ("x",))
        xv[:] = x / MTG_HEIGHT
        yv = ds.createVariable("y", "f8", ("y",))
        yv[:] = y / MTG_HEIGHT

        hv = ds.createVariable("cloud_top_height", "f4", ("y", "x"), fill_value=np.float32(np.nan))
        hv.units = "m"
        hv[:] = height

        qv = ds.createVariable("quality_method", "i1", ("y", "x"), fill_value=np.int8(-128))
        qv[:] = quality

        for name, values, units in (
            ("cloud_top_temperature", temperature, "K"),
            ("effective_cloudiness", cloudiness, "1"),
            ("cloud_top_aviation_height", aviation, "FL/10"),
        ):
            var = ds.createVariable(name, "f4", ("y", "x"), fill_value=np.float32(np.nan))
            var.units = units
            var[:] = values

        for name, values in (("delta_latitude", dlat), ("delta_longitude", dlon)):
            var = ds.createVariable(name, "i1", ("y", "x"), fill_value=np.int8(-128))
            var.scale_factor = 0.01
            var.units = "degrees"
            # Write the packed int8 exactly as the real granule stores it.
            # netCDF4 would otherwise apply scale_factor a second time on
            # assignment and overflow the type.
            var.set_auto_maskandscale(False)
            var[:] = np.round(values / 0.01).astype(np.int8)


def write_li(path: Path) -> None:
    import netCDF4

    # Twelve flashes clustered ~8 NM east of the station, one outlier 40 NM away.
    lats = np.array([STATION_LAT + 0.02 * i for i in range(12)] + [STATION_LAT + 0.7])
    lons = np.array([STATION_LON + 0.20 + 0.01 * i for i in range(12)] + [STATION_LON + 0.9])
    epoch_seconds = np.arange(13, dtype="f8") * 30.0

    with netCDF4.Dataset(str(path), "w", format="NETCDF4") as ds:
        ds.institution = "EUMETSAT"
        ds.license = "EUMETSAT Data Policy - free and open access"
        ds.sensing_end_time = "2026-08-25T14:00:00Z"
        ds.createDimension("flashes", lats.size)
        lat_var = ds.createVariable("latitude", "f4", ("flashes",))
        lat_var[:] = lats
        lon_var = ds.createVariable("longitude", "f4", ("flashes",))
        lon_var[:] = lons
        time_var = ds.createVariable("flash_time", "f8", ("flashes",))
        time_var.units = "seconds since 2026-08-25 13:50:00"
        time_var[:] = epoch_seconds


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_opera(DATA_DIR / "opera_dbzh.h5", "DBZH")
    write_opera(DATA_DIR / "opera_rate.h5", "RATE")
    write_ctth(DATA_DIR / "ctth.nc")
    write_li(DATA_DIR / "li_flashes.nc")
    for path in sorted(DATA_DIR.iterdir()):
        print(f"{path.name:24s} {path.stat().st_size / 1024:8.1f} KiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
