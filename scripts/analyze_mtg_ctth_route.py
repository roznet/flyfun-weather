#!/usr/bin/env python3
"""Re-sample cached MTG FCI CTTH netCDFs with parallax-aware logic.

For every airport / route point and every cached 10-min product, reports THREE
samples so we can see what the L2 algorithm assigned and where:

  1. nominal      - nearest pixel to the airport's GROUND footprint (what
                    fetch_mtg_ctth_route.py prints). For high clouds, this
                    pixel is generally clear or shows a lower layer because
                    the cirrus appears displaced toward the satellite.
  2. parallax     - within a ~70 x 30 km window around the airport, the pixel
                    whose corrected ground location (pixel_lat + delta_lat,
                    pixel_lon + delta_lon) is closest to the airport. This is
                    the cloud whose top is actually OVER the airport.
  3. highest      - within the same window, the pixel with the lowest CTT
                    (= highest cloud top). Useful when multi-layer pixels
                    near the airport get assigned to lower layers but the
                    high cirrus shows up in a neighboring pixel.

Reads zips already downloaded by fetch_mtg_ctth_route.py from
/tmp/eumetsat_ctth/cache/. No network access.
"""
from __future__ import annotations

import argparse
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import xarray as xr
from pyproj import CRS, Transformer

NAV_DB = Path("data/nav.db")
CACHE_DIR = Path("/tmp/eumetsat_ctth/cache")
DEFAULT_AIRPORTS = ["LSGL", "LFQB", "LFQA", "LFAT", "EGTF"]


@dataclass
class Point:
    name: str
    lat: float
    lon: float


def load_airport_points(idents: list[str], db_path: Path) -> list[Point]:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    placeholders = ",".join("?" for _ in idents)
    cur.execute(
        f"SELECT icao_code, latitude_deg, longitude_deg FROM airports "
        f"WHERE icao_code IN ({placeholders})",
        idents,
    )
    rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    return [Point(i, rows[i][0], rows[i][1]) for i in idents]


def insert_midpoints(points: list[Point]) -> list[Point]:
    out: list[Point] = []
    for i, p in enumerate(points):
        out.append(p)
        if i + 1 < len(points):
            q = points[i + 1]
            out.append(Point(f"mid_{p.name}_{q.name}", (p.lat + q.lat) / 2, (p.lon + q.lon) / 2))
    return out


def list_cached_nc_files(cache_dir: Path) -> list[Path]:
    """Extract every cached zip if not already done; return list of inner .nc paths sorted by sensing_start (in filename)."""
    nc_paths: list[Path] = []
    for zp in sorted(cache_dir.glob("*.zip")):
        extract_dir = cache_dir / zp.stem
        extract_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(zp) as zf:
            nc_name = next(n for n in zf.namelist() if n.endswith(".nc"))
            nc_path = extract_dir / Path(nc_name).name
            if not nc_path.exists():
                with zf.open(nc_name) as src, open(nc_path, "wb") as dst:
                    dst.write(src.read())
            nc_paths.append(nc_path)
    return nc_paths


def parse_sensing_time(nc_path: Path) -> str:
    # ..._OPE_20260504080000_20260504081000_... -> "08:00Z"
    parts = nc_path.name.split("_")
    for p in parts:
        if len(p) == 14 and p.startswith("2026"):
            return f"{p[8:10]}:{p[10:12]}Z"
    return nc_path.name[:30]


def build_geos(proj_attrs):
    h = float(proj_attrs["perspective_point_height"])
    a = float(proj_attrs["semi_major_axis"])
    b = float(proj_attrs["semi_minor_axis"])
    sweep = proj_attrs.get("sweep_angle_axis", "y")
    geos = CRS.from_proj4(
        f"+proj=geos +lon_0=0 +h={h} +a={a} +b={b} +sweep={sweep} +units=m +no_defs"
    )
    fwd = Transformer.from_crs(CRS.from_epsg(4326), geos, always_xy=True)
    inv = Transformer.from_crs(geos, CRS.from_epsg(4326), always_xy=True)
    return fwd, inv, h


def fmt_T(K: float) -> str:
    if not np.isfinite(K):
        return "    -"
    return f"{K:6.1f}K {K - 273.15:+6.1f}C"


def fmt_H(m: float) -> str:
    if not np.isfinite(m):
        return "      -"
    return f"{m:5.0f}m FL{m / 0.3048 / 100:03.0f}"


def analyze(nc_path: Path, points: list[Point]):
    ds = xr.open_dataset(nc_path)
    fwd, inv, h = build_geos(ds["mtg_geos_projection"].attrs)
    x = ds.x.values
    y = ds.y.values
    ctt = ds.cloud_top_temperature.values
    cth = ds.cloud_top_height.values
    ctp = ds.cloud_top_pressure.values
    eff = ds.effective_cloudiness.values
    dlat_arr = ds.delta_latitude.values
    dlon_arr = ds.delta_longitude.values

    # Window: parallax can shift cloud ~0.5° lat (high cirrus). Be generous:
    # we want the AIRPORT to sit near the SOUTH edge so we look mostly NORTH.
    rows_north = 35   # pixels north of nominal (~70 km)
    rows_south = 5    # pixels south
    cols_each = 15    # ~30 km E/W

    print()
    print(f"--- {parse_sensing_time(nc_path)} ---")
    header = (f"  {'point':<20} {'mode':<10} {'lat':>6} {'lon':>6} "
              f"{'CTT':>16} {'CTH':>14} {'CTP':>7} {'cld%':>5} "
              f"{'pix_lat':>7} {'pix_lon':>7} {'dlat':>5} {'dlon':>5}")
    print(header)
    for p in points:
        x_m, y_m = fwd.transform(p.lon, p.lat)
        col0 = int(np.argmin(np.abs(x - x_m / h)))
        row0 = int(np.argmin(np.abs(y - y_m / h)))

        r_lo = max(0, row0 - rows_north)   # smaller row index = bigger y = NORTH
        r_hi = min(ctt.shape[0], row0 + rows_south + 1)
        c_lo = max(0, col0 - cols_each)
        c_hi = min(ctt.shape[1], col0 + cols_each + 1)

        ctt_w = ctt[r_lo:r_hi, c_lo:c_hi]
        cth_w = cth[r_lo:r_hi, c_lo:c_hi]
        ctp_w = ctp[r_lo:r_hi, c_lo:c_hi]
        eff_w = eff[r_lo:r_hi, c_lo:c_hi]
        dlat_w = dlat_arr[r_lo:r_hi, c_lo:c_hi]
        dlon_w = dlon_arr[r_lo:r_hi, c_lo:c_hi]

        # Per-pixel ground (lat, lon) in the window
        XX, YY = np.meshgrid(x[c_lo:c_hi] * h, y[r_lo:r_hi] * h)
        LON, LAT = inv.transform(XX, YY)

        valid = ~np.isnan(ctt_w)
        nominal_ij = (row0 - r_lo, col0 - c_lo)

        # Apparent positions corrected for parallax: for pixel (i,j) the cloud-top
        # ground projection is (LAT[i,j] + dlat[i,j], LON[i,j] + dlon[i,j]).
        # We want this corrected position to sit at the airport.
        corr_lat = LAT + np.where(np.isnan(dlat_w), 0, dlat_w)
        corr_lon = LON + np.where(np.isnan(dlon_w), 0, dlon_w)
        cos_lat = np.cos(np.radians(p.lat))
        cost = (corr_lat - p.lat) ** 2 + ((corr_lon - p.lon) * cos_lat) ** 2
        cost = np.where(valid, cost, np.inf)

        # Highest cloud top in the window (= lowest CTT)
        ctt_for_min = np.where(valid, ctt_w, np.inf)

        rows_to_print = []
        rows_to_print.append(("nominal", nominal_ij))
        if np.isfinite(cost).any():
            rows_to_print.append(("parallax", np.unravel_index(np.argmin(cost), cost.shape)))
        if np.isfinite(ctt_for_min).any():
            rows_to_print.append(("highest", np.unravel_index(np.argmin(ctt_for_min), ctt_for_min.shape)))

        for label, ij in rows_to_print:
            i, j = ij
            print(
                f"  {p.name:<20} {label:<10} {p.lat:6.2f} {p.lon:6.2f} "
                f"{fmt_T(float(ctt_w[i, j])):>16} {fmt_H(float(cth_w[i, j])):>14} "
                f"{ctp_w[i, j] if np.isfinite(ctp_w[i, j]) else 0:7.1f} "
                f"{(eff_w[i, j] * 100 if np.isfinite(eff_w[i, j]) else 0):5.1f} "
                f"{LAT[i, j]:7.3f} {LON[i, j]:7.3f} "
                f"{(dlat_w[i, j] if np.isfinite(dlat_w[i, j]) else 0):5.2f} "
                f"{(dlon_w[i, j] if np.isfinite(dlon_w[i, j]) else 0):5.2f}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--airports", nargs="+", default=DEFAULT_AIRPORTS)
    parser.add_argument("--midpoints", action="store_true", default=True)
    parser.add_argument("--no-midpoints", dest="midpoints", action="store_false")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--nav-db", type=Path, default=NAV_DB)
    args = parser.parse_args()

    points = load_airport_points(args.airports, args.nav_db)
    if args.midpoints:
        points = insert_midpoints(points)

    nc_files = list_cached_nc_files(args.cache_dir)
    if not nc_files:
        raise SystemExit(f"no cached zips in {args.cache_dir} - run fetch_mtg_ctth_route.py first")

    print(f"Cached files: {len(nc_files)}; route points: {len(points)}")
    print("CTT shown as Kelvin and Celsius. CTH in metres and FL (hundreds of ft).")
    print("Parallax: dlat/dlon are the satellite-product's per-pixel parallax shift")
    print("(degrees). The cloud whose top is in pixel (lat,lon) actually projects on")
    print("the ground at (lat+dlat, lon+dlon).")
    for nc in nc_files:
        analyze(nc, points)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
