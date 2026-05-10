#!/usr/bin/env python3
"""Per-waypoint × per-timestep cloud-top layer histogram for the route.

Instead of picking a single "best" pixel, this characterises the cloud
structure in a ~70 x 30 km window around each waypoint by counting cloudy
pixels in altitude bins and breaking down which L2 height-assignment method
produced each detection.

Bins (chosen for GA flight planning):
  Low      FL000-FL050   (stratus, fog, low Cu)
  Mid-low  FL050-FL150   (mid layer, decked Sc/As)
  Mid-high FL150-FL250   (As/altostratus, mid-cirrus)
  High     FL250-FL400   (Cb, opaque cirrus)
  V.high   FL400+        (overshooting / very high cirrus)

Methods (FCI L2 quality_method codes):
  1 = opaque IR window (typically low stratus / fog)
  6 = opaque IR for cold cloud (Cb, thick cirrus)
  7 = radiance-ratio thin cirrus (very high)
  8 = CO2 / semi-transparent high
  9 = lower-layer mixed / multi-layer suspect
  10 = other semi-transparent

Reads: data/packs/debug/<pack>/route_points.json + cached FCI zips at
/tmp/eumetsat_ctth/cache/.
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

BIN_EDGES_M = [0, 1524, 4572, 7620, 12192, 30000]  # FL000-50, 50-150, 150-250, 250-400, 400+
BIN_LABELS = ["FL00-50", "FL50-150", "FL150-250", "FL250-400", "FL400+"]


@dataclass
class Point:
    name: str
    lat: float
    lon: float


def load_named_waypoints(pack_dir: Path) -> list[Point]:
    inner = next(d for d in sorted(pack_dir.iterdir()) if d.is_dir())
    with (inner / "route_points.json").open() as f:
        rp = json.load(f)
    return [
        Point(p["waypoint_icao"], p["lat"], p["lon"])
        for p in rp
        if p.get("waypoint_icao")
    ]


def list_cached_nc_files(cache_dir: Path) -> list[Path]:
    nc_paths: list[Path] = []
    for zp in sorted(cache_dir.glob("*.zip")):
        ed = cache_dir / zp.stem
        ed.mkdir(exist_ok=True)
        with zipfile.ZipFile(zp) as zf:
            n = next(x for x in zf.namelist() if x.endswith(".nc"))
            np_ = ed / Path(n).name
            if not np_.exists():
                with zf.open(n) as s, open(np_, "wb") as d:
                    d.write(s.read())
            nc_paths.append(np_)
    return nc_paths


def parse_time(nc_path: Path) -> str:
    for p in nc_path.name.split("_"):
        if len(p) == 14 and p.startswith("2026"):
            return f"{p[8:10]}:{p[10:12]}"
    return "?"


def build_geos(attrs):
    h = float(attrs["perspective_point_height"])
    geos = CRS.from_proj4(
        f"+proj=geos +lon_0=0 +h={h} +a={float(attrs['semi_major_axis'])} "
        f"+b={float(attrs['semi_minor_axis'])} "
        f"+sweep={attrs.get('sweep_angle_axis', 'y')} +units=m +no_defs"
    )
    return Transformer.from_crs(CRS.from_epsg(4326), geos, always_xy=True), h


def histogram_window(nc_path: Path, points: list[Point], radius_km: float = 10.0):
    """For each waypoint, count cloud-top pixels whose PARALLAX-CORRECTED
    ground location falls within radius_km of the waypoint. The search box
    extends ~60 km north of the nominal pixel to catch high-cirrus pixels
    whose dlat shifts them up to ~0.5 deg south in the imagery."""
    ds = xr.open_dataset(nc_path)
    fwd, h = build_geos(ds["mtg_geos_projection"].attrs)
    inv = Transformer.from_crs(
        CRS.from_proj4(
            f"+proj=geos +lon_0=0 +h={h} "
            f"+a={float(ds['mtg_geos_projection'].attrs['semi_major_axis'])} "
            f"+b={float(ds['mtg_geos_projection'].attrs['semi_minor_axis'])} "
            f"+sweep={ds['mtg_geos_projection'].attrs.get('sweep_angle_axis','y')} "
            f"+units=m +no_defs"
        ),
        CRS.from_epsg(4326),
        always_xy=True,
    )
    x = ds.x.values
    y = ds.y.values
    cth = ds.cloud_top_height.values
    qm = ds.quality_method.values
    dlat_arr = ds.delta_latitude.values
    dlon_arr = ds.delta_longitude.values

    # Search box: in this geos projection, larger row index = larger latitude.
    # A cloud over the airport appears in a pixel NORTH of the airport (its
    # line-of-sight ground intersection is shifted north of the cloud), so
    # we extend the box NORTH (larger row) by enough to catch FL400 cirrus
    # (parallax dlat up to ~0.65 deg = ~22 pixels at this latitude).
    rows_above = 30   # rows NORTH of nominal (larger row index)
    rows_below = 6    # rows SOUTH (smaller row index)
    cols_each = 7     # ~14 km E-W

    out: dict[str, dict] = {}
    for p in points:
        x_m, y_m = fwd.transform(p.lon, p.lat)
        col0 = int(np.argmin(np.abs(x - x_m / h)))
        row0 = int(np.argmin(np.abs(y - y_m / h)))
        r_lo = max(0, row0 - rows_below)
        r_hi = min(cth.shape[0], row0 + rows_above + 1)
        c_lo = max(0, col0 - cols_each)
        c_hi = min(cth.shape[1], col0 + cols_each + 1)

        cth_w = cth[r_lo:r_hi, c_lo:c_hi]
        qm_w = qm[r_lo:r_hi, c_lo:c_hi]
        dlat_w = dlat_arr[r_lo:r_hi, c_lo:c_hi]
        dlon_w = dlon_arr[r_lo:r_hi, c_lo:c_hi]

        XX, YY = np.meshgrid(x[c_lo:c_hi] * h, y[r_lo:r_hi] * h)
        LON, LAT = inv.transform(XX, YY)

        # Corrected ground location of each pixel's cloud-top
        corr_lat = LAT + np.where(np.isnan(dlat_w), 0, dlat_w)
        corr_lon = LON + np.where(np.isnan(dlon_w), 0, dlon_w)
        cos_lat = np.cos(np.radians(p.lat))
        dist_km = np.sqrt(
            ((corr_lat - p.lat) * 111.0) ** 2
            + ((corr_lon - p.lon) * 111.0 * cos_lat) ** 2
        )

        valid_cloud = ~np.isnan(cth_w)
        # For clear-sky pixels (no parallax), use uncorrected ground distance
        # so we can still report the waypoint's clear-sky fraction.
        dist_clear_km = np.sqrt(
            ((LAT - p.lat) * 111.0) ** 2
            + ((LON - p.lon) * 111.0 * cos_lat) ** 2
        )
        in_circle = np.where(valid_cloud, dist_km <= radius_km, dist_clear_km <= radius_km)

        total = int(in_circle.sum())
        cloudy_in = valid_cloud & in_circle
        cloudy = int(cloudy_in.sum())
        bins = np.histogram(cth_w[cloudy_in], bins=BIN_EDGES_M)[0]
        m_counts: dict[int, int] = {}
        for m in [1, 6, 7, 8, 9, 10]:
            m_counts[m] = int(((qm_w == m) & cloudy_in).sum())
        out[p.name] = {
            "total": total,
            "cloudy": cloudy,
            "bins": bins.tolist(),
            "methods": m_counts,
        }
    return out


def fmt_pct(n: int, denom: int) -> str:
    if denom == 0:
        return "  - "
    pct = 100.0 * n / denom
    if pct == 0:
        return "    "
    return f"{pct:3.0f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--waypoints", nargs="+", default=None,
                        help="filter to a subset of named waypoints")
    parser.add_argument("--radius-km", type=float, default=10.0,
                        help="circle radius around the waypoint (default 10 km, "
                             "applied to the parallax-corrected ground location of each cloud-top pixel)")
    args = parser.parse_args()

    points = load_named_waypoints(args.pack)
    if args.waypoints:
        points = [p for p in points if p.name in args.waypoints]
    nc_files = list_cached_nc_files(args.cache_dir)

    by_time: dict[str, dict] = {}
    for nc in nc_files:
        by_time[parse_time(nc)] = histogram_window(nc, points, radius_km=args.radius_km)

    # Output: for each waypoint, two compact tables side by side:
    #   (a) layer fraction by FL band over time
    #   (b) high-cloud presence (methods 6+7+8) % vs low-cloud (method 1) %
    times = sorted(by_time.keys())
    print(f"Window: {args.radius_km:.0f} km radius around each waypoint, parallax-corrected.")
    print(f"For each cloud pixel we shift its ground location by (delta_lat, delta_lon)")
    print(f"and only count it if the corrected location lands within the circle.")
    print(f"Cells = % of pixels in the circle. 'cld%' = cloudy fraction.")
    print(f"'high%' = methods 6+7+8 (cold opaque cirrus + thin cirrus). ")
    print(f"'low%' = method 1 (opaque IR low stratus/fog). 'mix%' = method 9 (multi-layer).")

    for p in points:
        print(f"\n--- {p.name}  ({p.lat:.2f}N, {p.lon:.2f}E) ---")
        # Header
        hdr = f"  {'time':<5} {'cld%':>4}  " + " ".join(f"{lbl:>9}" for lbl in BIN_LABELS) + \
              f"   {'high%':>5} {'low%':>5} {'mix%':>5}"
        print(hdr)
        for t in times:
            d = by_time[t][p.name]
            total = d["total"]
            cloudy = d["cloudy"]
            bins = d["bins"]
            m = d["methods"]
            high_n = m[6] + m[7] + m[8]
            line = f"  {t:<5} {fmt_pct(cloudy, total):>4}  "
            for b in bins:
                line += f" {fmt_pct(b, total):>9}"
            line += f"   {fmt_pct(high_n, total):>5} {fmt_pct(m[1], total):>5} {fmt_pct(m[9], total):>5}"
            print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
