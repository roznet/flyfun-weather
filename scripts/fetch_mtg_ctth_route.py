#!/usr/bin/env python3
"""Sample EUMETSAT MTG FCI L2 Cloud Top Temperature & Height (collection
EO:EUM:DAT:0681) at a list of airports / route waypoints over a time window.

Per FCI repeat cycle (10 min) the product is a 5568 x 5568 full-disk netCDF in
geostationary projection (lon0=0, h=35786400 m, sweep=y). For each requested
point we:

  1. Project (lon, lat) -> projected metres on MTG GEOS, divide by h to get
     scan-angle radians, find nearest column/row in the netCDF x/y arrays.
  2. Read cloud_top_temperature (K), cloud_top_height (m), cloud_top_pressure
     (hPa), effective_cloudiness (%), and the parallax shift
     (delta_latitude / delta_longitude) at that pixel.

NaN means "no cloud assigned" by the L2 algorithm at that pixel.

Auth: reads EUMETSAT_CONSUMER_KEY / EUMETSAT_CONSUMER_SECRET from .env or env.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import xarray as xr
from dotenv import load_dotenv
from pyproj import CRS, Transformer

import eumdac

COLLECTION_ID = "EO:EUM:DAT:0681"
DEFAULT_AIRPORTS = ["LSGL", "LFQB", "LFQA", "LFAT", "EGTF"]
NAV_DB = Path("data/nav.db")
CACHE_DIR = Path("/tmp/eumetsat_ctth/cache")


@dataclass
class Point:
    name: str
    lat: float
    lon: float


def parse_iso_utc(s: str) -> datetime:
    s = s.rstrip("Z")
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def load_airport_points(idents: list[str], db_path: Path) -> list[Point]:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    placeholders = ",".join("?" for _ in idents)
    cur.execute(
        f"SELECT icao_code, latitude_deg, longitude_deg "
        f"FROM airports WHERE icao_code IN ({placeholders})",
        idents,
    )
    rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    missing = [i for i in idents if i not in rows]
    if missing:
        raise SystemExit(f"airports not found in {db_path}: {missing}")
    return [Point(i, rows[i][0], rows[i][1]) for i in idents]


def insert_midpoints(points: list[Point]) -> list[Point]:
    """Add a great-circle midpoint between each adjacent pair of route points."""
    out: list[Point] = []
    for i, p in enumerate(points):
        out.append(p)
        if i + 1 < len(points):
            q = points[i + 1]
            mid = Point(
                name=f"mid_{p.name}_{q.name}",
                lat=(p.lat + q.lat) / 2.0,  # OK for short legs
                lon=(p.lon + q.lon) / 2.0,
            )
            out.append(mid)
    return out


def list_products(token, start: datetime, end: datetime):
    ds = eumdac.DataStore(token)
    col = ds.get_collection(COLLECTION_ID)
    return sorted(col.search(dtstart=start, dtend=end), key=lambda p: p.sensing_start)


def download_and_extract(product, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / f"{product}.zip"
    if not zip_path.exists():
        tmp = zip_path.with_suffix(".zip.part")
        with product.open() as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        tmp.rename(zip_path)
    extract_dir = cache_dir / str(product)
    extract_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        nc_name = next(n for n in zf.namelist() if n.endswith(".nc"))
        nc_path = extract_dir / Path(nc_name).name
        if not nc_path.exists():
            with zf.open(nc_name) as src, open(nc_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
    return nc_path


def build_geos_to_scan(proj_attrs) -> Transformer:
    """Return Transformer that maps WGS84 (lon, lat) -> projected metres on the
    MTG GEOS frame. Divide outputs by h to get scan-angle radians."""
    h = float(proj_attrs["perspective_point_height"])
    a = float(proj_attrs["semi_major_axis"])
    b = float(proj_attrs["semi_minor_axis"])
    sweep = proj_attrs.get("sweep_angle_axis", "y")
    geos = CRS.from_proj4(
        f"+proj=geos +lon_0=0 +h={h} +a={a} +b={b} +sweep={sweep} "
        f"+units=m +no_defs"
    )
    return Transformer.from_crs(CRS.from_epsg(4326), geos, always_xy=True), h


def sample_file(nc_path: Path, points: list[Point]) -> list[dict]:
    ds = xr.open_dataset(nc_path)
    transformer, h = build_geos_to_scan(ds["mtg_geos_projection"].attrs)
    x = ds.x.values
    y = ds.y.values
    ctt_arr = ds.cloud_top_temperature.values
    cth_arr = ds.cloud_top_height.values
    ctp_arr = ds.cloud_top_pressure.values
    eff_arr = ds.effective_cloudiness.values
    dlat_arr = ds.delta_latitude.values
    dlon_arr = ds.delta_longitude.values
    avhgt_arr = ds.cloud_top_aviation_height.values

    rows: list[dict] = []
    for p in points:
        x_m, y_m = transformer.transform(p.lon, p.lat)
        if not (np.isfinite(x_m) and np.isfinite(y_m)):
            rows.append({"name": p.name, "off_disk": True})
            continue
        x_rad = x_m / h
        y_rad = y_m / h
        col = int(np.argmin(np.abs(x - x_rad)))
        row = int(np.argmin(np.abs(y - y_rad)))
        ctt_k = float(ctt_arr[row, col])
        cth_m = float(cth_arr[row, col])
        rows.append({
            "name": p.name,
            "lat": p.lat,
            "lon": p.lon,
            "row": row,
            "col": col,
            "ctt_K": ctt_k,
            "ctt_C": ctt_k - 273.15 if np.isfinite(ctt_k) else float("nan"),
            "cth_m": cth_m,
            "cth_ft": cth_m / 0.3048 if np.isfinite(cth_m) else float("nan"),
            "ctp_hPa": float(ctp_arr[row, col]),
            "cloud_frac_pct": float(eff_arr[row, col]) * 100.0,  # stored as 0-1 fraction
            "aviation_FL_div10": float(avhgt_arr[row, col]),
            "parallax_dlat": float(dlat_arr[row, col]),
            "parallax_dlon": float(dlon_arr[row, col]),
        })
    return rows


def fmt_value(v, fmt: str, na: str = "    -") -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return na
    return format(v, fmt)


def print_timestep(product, rows: list[dict]) -> None:
    sensing = product.sensing_start.strftime("%Y-%m-%d %H:%M:%SZ")
    print(f"\n=== {sensing}  product={product} ===")
    print(f"  {'point':<20} {'lat':>7} {'lon':>7} "
          f"{'cld%':>5} {'CTT_K':>7} {'CTT_C':>7} "
          f"{'CTH_m':>7} {'CTH_ft':>7} {'CTP_hPa':>8} "
          f"{'pdlat':>6} {'pdlon':>6}")
    for r in rows:
        if r.get("off_disk"):
            print(f"  {r['name']:<20} OFF DISK")
            continue
        print(
            f"  {r['name']:<20} {r['lat']:>7.3f} {r['lon']:>7.3f} "
            f"{fmt_value(r['cloud_frac_pct'], '5.1f')} "
            f"{fmt_value(r['ctt_K'], '7.2f')} "
            f"{fmt_value(r['ctt_C'], '7.2f')} "
            f"{fmt_value(r['cth_m'], '7.0f')} "
            f"{fmt_value(r['cth_ft'], '7.0f')} "
            f"{fmt_value(r['ctp_hPa'], '8.1f')} "
            f"{fmt_value(r['parallax_dlat'], '6.2f')} "
            f"{fmt_value(r['parallax_dlon'], '6.2f')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-05-04T08:00",
                        help="UTC start, ISO (default: 2026-05-04T08:00)")
    parser.add_argument("--end", default="2026-05-04T10:00",
                        help="UTC end, ISO (default: 2026-05-04T10:00)")
    parser.add_argument("--airports", nargs="+", default=DEFAULT_AIRPORTS,
                        help=f"ICAO codes (default: {' '.join(DEFAULT_AIRPORTS)})")
    parser.add_argument("--midpoints", action="store_true", default=True,
                        help="Insert great-circle midpoints between adjacent route points (default: on)")
    parser.add_argument("--no-midpoints", dest="midpoints", action="store_false")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR,
                        help=f"Where to cache zip + .nc files (default: {CACHE_DIR})")
    parser.add_argument("--nav-db", type=Path, default=NAV_DB,
                        help=f"Airports DB (default: {NAV_DB})")
    parser.add_argument("--csv", type=Path, default=None,
                        help="Optional CSV output path")
    args = parser.parse_args()

    load_dotenv()
    key = os.environ.get("EUMETSAT_CONSUMER_KEY")
    secret = os.environ.get("EUMETSAT_CONSUMER_SECRET")
    if not key or not secret:
        print("EUMETSAT_CONSUMER_KEY / EUMETSAT_CONSUMER_SECRET missing in env/.env",
              file=sys.stderr)
        return 1

    points = load_airport_points(args.airports, args.nav_db)
    if args.midpoints:
        points = insert_midpoints(points)

    print(f"Route points ({len(points)}):")
    for p in points:
        print(f"  {p.name:<20} {p.lat:>7.3f}  {p.lon:>7.3f}")

    start = parse_iso_utc(args.start)
    end = parse_iso_utc(args.end)

    token = eumdac.AccessToken((key, secret))
    products = list_products(token, start, end)
    # Filter to slots whose START is within [start, end)
    products = [p for p in products
                if start <= p.sensing_start.replace(tzinfo=timezone.utc) < end]
    print(f"\nFound {len(products)} FCI CTTH products in "
          f"[{start.isoformat()}, {end.isoformat()})")

    csv_rows: list[str] = []
    if args.csv:
        csv_rows.append("sensing_start,point,lat,lon,row,col,"
                        "ctt_K,ctt_C,cth_m,cth_ft,ctp_hPa,cloud_frac_pct,"
                        "aviation_FL_div10,parallax_dlat,parallax_dlon")

    for prod in products:
        nc_path = download_and_extract(prod, args.cache_dir)
        rows = sample_file(nc_path, points)
        print_timestep(prod, rows)
        if args.csv:
            ts = prod.sensing_start.strftime("%Y-%m-%dT%H:%M:%SZ")
            for r in rows:
                if r.get("off_disk"):
                    continue
                csv_rows.append(
                    f"{ts},{r['name']},{r['lat']},{r['lon']},{r['row']},{r['col']},"
                    f"{r['ctt_K']},{r['ctt_C']},{r['cth_m']},{r['cth_ft']},"
                    f"{r['ctp_hPa']},{r['cloud_frac_pct']},"
                    f"{r['aviation_FL_div10']},"
                    f"{r['parallax_dlat']},{r['parallax_dlon']}"
                )

    if args.csv:
        args.csv.write_text("\n".join(csv_rows) + "\n")
        print(f"\nWrote {args.csv} ({len(csv_rows)-1} data rows)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
