#!/usr/bin/env python3
"""Pivot the cached MTG FCI L2 CTTH samples into per-waypoint × per-timestep
tables for the route in the debug briefing pack.

Reads:
  data/packs/debug/<pack>/route_points.json     (named waypoints to sample)
  /tmp/eumetsat_ctth/cache/*.zip                (pre-downloaded FCI products)

Prints two tables:
  A) "Highest cloud top within ~70 x 30 km box around the waypoint" — best
     proxy for the highest layer overhead, accounting for parallax + the L2
     algorithm assigning different layers to different pixels.
  B) "Cloud-top at the waypoint, parallax-corrected" — the cloud whose top
     actually projects onto the waypoint's surface position. This is the
     LOWEST layer over the waypoint when the algorithm prefers it.
"""
from __future__ import annotations

import argparse
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import xarray as xr
from pyproj import CRS, Transformer

DEFAULT_PACK = Path("data/packs/debug/lsgs_lsgl_lfqb_lfqa_bilgo_lfat_egtf-2026-05-04-e2b6")
CACHE_DIR = Path("/tmp/eumetsat_ctth/cache")


@dataclass
class Point:
    name: str
    lat: float
    lon: float


def load_named_waypoints(pack_dir: Path) -> list[Point]:
    inner = next(d for d in sorted(pack_dir.iterdir()) if d.is_dir())
    rp_path = inner / "route_points.json"
    with rp_path.open() as f:
        rp = json.load(f)
    return [
        Point(p["waypoint_icao"], p["lat"], p["lon"])
        for p in rp
        if p.get("waypoint_icao")
    ]


def list_cached_nc_files(cache_dir: Path) -> list[Path]:
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
    parts = nc_path.name.split("_")
    for p in parts:
        if len(p) == 14 and p.startswith("2026"):
            return f"{p[8:10]}:{p[10:12]}"
    return nc_path.name[:8]


def build_geos(proj_attrs):
    h = float(proj_attrs["perspective_point_height"])
    a = float(proj_attrs["semi_major_axis"])
    b = float(proj_attrs["semi_minor_axis"])
    sweep = proj_attrs.get("sweep_angle_axis", "y")
    geos = CRS.from_proj4(
        f"+proj=geos +lon_0=0 +h={h} +a={a} +b={b} +sweep={sweep} +units=m +no_defs"
    )
    return (
        Transformer.from_crs(CRS.from_epsg(4326), geos, always_xy=True),
        Transformer.from_crs(geos, CRS.from_epsg(4326), always_xy=True),
        h,
    )


def sample_window(nc_path: Path, points: list[Point]):
    """Return per-timestep dict[name] = {parallax: (CTT_K, CTH_m), highest: (CTT_K, CTH_m)}."""
    ds = xr.open_dataset(nc_path)
    fwd, inv, h = build_geos(ds["mtg_geos_projection"].attrs)
    x = ds.x.values
    y = ds.y.values
    ctt = ds.cloud_top_temperature.values
    cth = ds.cloud_top_height.values
    dlat_arr = ds.delta_latitude.values
    dlon_arr = ds.delta_longitude.values

    # Parallax shifts cloud-top apparent position by ~0.4 deg lat at FL300+;
    # window biased northward (smaller row index = larger y = north).
    rows_north = 35
    rows_south = 5
    cols_each = 15

    out: dict[str, dict] = {}
    for p in points:
        x_m, y_m = fwd.transform(p.lon, p.lat)
        col0 = int(np.argmin(np.abs(x - x_m / h)))
        row0 = int(np.argmin(np.abs(y - y_m / h)))

        r_lo = max(0, row0 - rows_north)
        r_hi = min(ctt.shape[0], row0 + rows_south + 1)
        c_lo = max(0, col0 - cols_each)
        c_hi = min(ctt.shape[1], col0 + cols_each + 1)

        ctt_w = ctt[r_lo:r_hi, c_lo:c_hi]
        cth_w = cth[r_lo:r_hi, c_lo:c_hi]
        dlat_w = dlat_arr[r_lo:r_hi, c_lo:c_hi]
        dlon_w = dlon_arr[r_lo:r_hi, c_lo:c_hi]

        XX, YY = np.meshgrid(x[c_lo:c_hi] * h, y[r_lo:r_hi] * h)
        LON, LAT = inv.transform(XX, YY)

        valid = ~np.isnan(ctt_w)

        # Parallax: corrected ground location nearest waypoint
        corr_lat = LAT + np.where(np.isnan(dlat_w), 0, dlat_w)
        corr_lon = LON + np.where(np.isnan(dlon_w), 0, dlon_w)
        cos_lat = np.cos(np.radians(p.lat))
        cost = (corr_lat - p.lat) ** 2 + ((corr_lon - p.lon) * cos_lat) ** 2
        cost = np.where(valid, cost, np.inf)

        # Highest in window
        ctt_for_min = np.where(valid, ctt_w, np.inf)

        result = {"parallax": (np.nan, np.nan), "highest": (np.nan, np.nan)}
        if np.isfinite(cost).any():
            i, j = np.unravel_index(np.argmin(cost), cost.shape)
            result["parallax"] = (float(ctt_w[i, j]), float(cth_w[i, j]))
        if np.isfinite(ctt_for_min).any():
            i, j = np.unravel_index(np.argmin(ctt_for_min), ctt_for_min.shape)
            result["highest"] = (float(ctt_w[i, j]), float(cth_w[i, j]))
        out[p.name] = result
    return out


def fmt_cell(ctt_K: float, cth_m: float) -> str:
    if not np.isfinite(ctt_K):
        return "    -   "
    return f"{ctt_K - 273.15:+5.0f}/F{cth_m / 0.3048 / 100:03.0f}"


def print_table(title: str, key: str, points: list[Point],
                samples_by_time: dict[str, dict]) -> None:
    times = sorted(samples_by_time.keys())
    print(f"\n=== {title} ===")
    print(f"  Cell = CTT (deg C) / CTH as FL (hundreds of ft)")
    header = "  " + f"{'wpt':<6}" + " " + f"{'lat':>5}" + " " + f"{'lon':>6}" + " " + " ".join(f"{t:>9}" for t in times)
    print(header)
    for p in points:
        row = f"  {p.name:<6} {p.lat:5.2f} {p.lon:6.2f} "
        for t in times:
            ctt_K, cth_m = samples_by_time[t][p.name][key]
            row += f" {fmt_cell(ctt_K, cth_m):>8}"
        print(row)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    args = parser.parse_args()

    points = load_named_waypoints(args.pack)
    nc_files = list_cached_nc_files(args.cache_dir)

    print(f"Route waypoints ({len(points)}):")
    for p in points:
        print(f"  {p.name:<6} {p.lat:7.3f}  {p.lon:7.3f}")
    print(f"FCI CTTH timesteps ({len(nc_files)}): "
          f"{parse_sensing_time(nc_files[0])}Z .. {parse_sensing_time(nc_files[-1])}Z")

    samples_by_time: dict[str, dict] = {}
    for nc in nc_files:
        ts = parse_sensing_time(nc)
        samples_by_time[ts] = sample_window(nc, points)

    print_table(
        "A. Highest cloud top within ~70 x 30 km box around waypoint "
        "(catches the high cirrus layer where the L2 algorithm assigned it)",
        "highest", points, samples_by_time,
    )
    print_table(
        "B. Cloud-top right above the waypoint (parallax-corrected) "
        "(usually the lowest layer when there is one)",
        "parallax", points, samples_by_time,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
