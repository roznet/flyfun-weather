#!/usr/bin/env python3
"""Old lat-lon vs new native-grid ICON parity check (#974).

    icon_native_parity.py [--model icon-eu|icon-d2] [--run YYYYMMDDHH]
                          [--steps 0,6,24] [--step-deg DEG] [--cache DIR]

Until DWD switches off the regular lat-lon feed (after 2026-11-30) both feeds
carry the same runs. For one run this downloads a handful of fields from both
(model-level, pressure-level, single-level and surface), samples them at a
lattice of points over the model's domain plus a few airports, and compares:
today's path (regular lat-lon, bilinear, ``decode._bilinear_grid_weights``)
against the new one (native grid, ``icon_grid`` barycentric and nearest).

Prints one line per field and method: points both feeds cover, points only
one covers, mean difference (native minus lat-lon), mean and 95th-percentile
absolute difference, and the worst point. The lat-lon product is itself DWD's
interpolation of the native field, so expect differences of the size of one
regridding, not systematic offsets. Needs network access to opendata.dwd.de
(run on the droplet or the Mac, not in the cloud sandbox). Exit 0 always:
this is a report, not a gate.
"""

from __future__ import annotations

import argparse
import bz2
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

from weatherbrief.fetch.grib import decode as dec
from weatherbrief.fetch.grib import icon_eu_fetch as ief
from weatherbrief.fetch.grib.grib_reader import gather_bilinear, iter_messages
from weatherbrief.fetch.grib.icon_grid import (
    IconNativeGrid,
    gather_native,
    get_grid,
    grid_cache_path,
    register_grid,
)

AIRPORTS = {
    "EGLL": (51.47, -0.45), "LFPG": (49.01, 2.55), "EDDF": (50.03, 8.57),
    "LSGS": (46.22, 7.33), "LSZH": (47.46, 8.55), "EDDM": (48.35, 11.79),
    "LFMN": (43.66, 7.21), "EGPH": (55.95, -3.37), "LIRF": (41.80, 12.25),
    "LEMD": (40.47, -3.56), "ESSA": (59.65, 17.92), "EKCH": (55.62, 12.65),
}

# (label, legacy variable, v1 param, kind, level). kind: ml = model level,
# pl = pressure level (hPa), sl = single level.
FIELDS = [
    ("T ml60", "t", "T", "ml", 60),
    ("U ml60", "u", "U", "ml", 60),
    ("QC ml60", "qc", "QC", "ml", 60),
    ("T 850hPa", "t", "T", "pl", 850),
    ("RELHUM 850hPa", "relhum", "RELHUM", "pl", 850),
    ("CEILING", "ceiling", "CEILING", "sl", None),
    ("CLCT", "clct", "CLCT", "sl", None),
    ("T_2M", "t_2m", "T_2M", "sl", None),
    ("PMSL", "pmsl", "PMSL", "sl", None),
]


def _get(session: requests.Session, url: str) -> bytes | None:
    r = session.get(url, timeout=60)
    if r.status_code != 200:
        print(f"  HTTP {r.status_code} {url}", file=sys.stderr)
        return None
    return r.content


def _legacy_url(variant, init_date, init_hour, fh, var, kind, level) -> str:
    if kind == "ml":
        return ief.icon_eu_file_url(init_date, init_hour, fh, level, var, variant)
    if kind == "pl":
        return ief.icon_eu_pressure_level_url(init_date, init_hour, fh, level, var, variant)
    return ief.icon_eu_single_level_url(init_date, init_hour, fh, var, variant)


def _v1_url(variant, init_date, init_hour, fh, param, kind, level) -> str:
    if kind == "ml":
        return ief.icon_v1_url(
            variant, param, init_date, init_hour, fh,
            level_type=ief.ICON_V1_LEVEL_MODEL, level=level,
        )
    if kind == "pl":
        return ief.icon_v1_url(
            variant, param, init_date, init_hour, fh,
            level_type=ief.ICON_V1_LEVEL_PRESSURE, level=level * 100,
        )
    return ief.icon_v1_url(variant, param, init_date, init_hour, fh)


def _sample_legacy(blob: bytes, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    out = np.full(lats.size, np.nan)
    for msg in iter_messages(blob):
        grid = msg.grid()
        if grid is None:
            continue
        values = msg.values(grid)
        bw = dec._bilinear_grid_weights(grid.lats, grid.lons, lats, lons)
        if values is None or bw is None:
            continue
        out[bw.inb_idx] = gather_bilinear(values, bw)
        break
    return out


def _sample_native(blob: bytes, weights: dict, n: int) -> tuple[str, np.ndarray]:
    out = {}
    for msg in iter_messages(blob):
        values = msg.native_values()
        if values is None:
            continue
        for method, nw in weights.items():
            arr = np.full(n, np.nan)
            arr[nw.inb_idx] = gather_native(values, nw)
            out[method] = arr
        return msg.uuid, out
    return "", out


def _load_grid(variant, init_date, init_hour, session, cache: Path) -> IconNativeGrid:
    clat_url, clon_url = ief.icon_v1_grid_urls(variant, init_date, init_hour)
    # The UUID is only known from a message; the CLAT download tells us.
    clat = _get(session, clat_url)
    if clat is None:
        raise SystemExit("CLAT not available for this run")
    uuid = next(iter_messages(clat)).uuid
    grid = get_grid(uuid, cache_root=cache)
    if grid is None:
        clon = _get(session, clon_url)
        if clon is None:
            raise SystemExit("CLON not available for this run")
        t0 = time.perf_counter()
        grid = register_grid(IconNativeGrid.from_grib(clat, clon))
        grid.save(grid_cache_path(cache, grid.uuid))
        print(f"grid {grid.uuid}: {grid.n_cells} cells, spacing {grid.spacing_km:.2f} km, "
              f"built in {time.perf_counter() - t0:.1f}s")
    return grid


def _report(label: str, method: str, legacy: np.ndarray, native: np.ndarray, names) -> None:
    both = ~np.isnan(legacy) & ~np.isnan(native)
    only_l = int((~np.isnan(legacy) & np.isnan(native)).sum())
    only_n = int((np.isnan(legacy) & ~np.isnan(native)).sum())
    if not both.any():
        print(f"{label:<15} {method:<11} no common points (only lat-lon {only_l}, only native {only_n})")
        return
    d = native[both] - legacy[both]
    ad = np.abs(d)
    worst = int(np.flatnonzero(both)[int(np.argmax(ad))])
    print(
        f"{label:<15} {method:<11} n={int(both.sum()):5d} only_ll={only_l:4d} only_nat={only_n:4d} "
        f"mean={d.mean():+9.3f} mean|d|={ad.mean():8.3f} p95|d|={np.percentile(ad, 95):8.3f} "
        f"max|d|={ad.max():8.3f} at {names[worst]}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", choices=("icon-eu", "icon-d2"), default="icon-eu")
    ap.add_argument("--run", help="YYYYMMDDHH; default: the 3-hourly run 5 h ago")
    ap.add_argument("--steps", default="0,6,24")
    ap.add_argument(
        "--step-deg", type=float, default=None,
        help="lattice spacing; default 0.5 for icon-eu, 0.1 for icon-d2",
    )
    ap.add_argument("--cache", type=Path, default=Path("data/.cache/grib"))
    args = ap.parse_args()

    variant = ief.ICON_EU if args.model == "icon-eu" else ief.ICON_D2
    if args.step_deg is None:
        args.step_deg = 0.5 if args.model == "icon-eu" else 0.1
    if args.run:
        init = datetime.strptime(args.run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    else:
        t = datetime.now(timezone.utc) - timedelta(hours=5)
        init = t.replace(hour=t.hour - t.hour % 3, minute=0, second=0, microsecond=0)
    init_date, init_hour = init.strftime("%Y%m%d"), init.hour
    steps = [int(s) for s in args.steps.split(",") if s.strip()]
    print(f"{variant.slug} run {init:%Y-%m-%d %H}Z, steps {steps}")

    la, lo = np.meshgrid(
        np.arange(variant.lat_min, variant.lat_max + 1e-9, args.step_deg),
        np.arange(variant.lon_min, variant.lon_max + 1e-9, args.step_deg),
        indexing="ij",
    )
    names = [f"{a:.2f},{b:.2f}" for a, b in zip(la.ravel(), lo.ravel())] + list(AIRPORTS)
    lats = np.concatenate([la.ravel(), [p[0] for p in AIRPORTS.values()]])
    lons = np.concatenate([lo.ravel(), [p[1] for p in AIRPORTS.values()]])

    session = requests.Session()
    args.cache.mkdir(parents=True, exist_ok=True)
    grid = _load_grid(variant, init_date, init_hour, session, args.cache)
    t0 = time.perf_counter()
    weights = {m: grid.weights(lats, lons, m) for m in ("barycentric", "nearest")}
    print(f"weights for {lats.size} points in {time.perf_counter() - t0:.2f}s; "
          f"{weights['nearest'].inb_idx.size} inside the native footprint")
    inside = np.zeros(lats.size, bool)
    inside[weights["nearest"].inb_idx] = True
    in_bbox = (lats >= variant.lat_min) & (lats <= variant.lat_max) & (
        lons >= variant.lon_min) & (lons <= variant.lon_max)
    print(f"bbox gate vs native footprint: {int((in_bbox & ~inside).sum())} bbox points "
          f"outside the grid, {int((~in_bbox & inside).sum())} grid points outside the bbox")

    for fh in steps:
        print(f"\n-- step +{fh} h")
        for label, var, param, kind, level in FIELDS:
            if kind == "pl" and level not in ief.icon_snapshot_pressure_levels(variant):
                continue
            old = _get(session, _legacy_url(variant, init_date, init_hour, fh, var, kind, level))
            new = _get(session, _v1_url(variant, init_date, init_hour, fh, param, kind, level))
            if old is None or new is None:
                print(f"{label:<15} skipped (missing file)")
                continue
            try:
                old = bz2.decompress(old)
            except OSError:
                pass
            t1 = time.perf_counter()
            legacy = _sample_legacy(old, lats, lons)
            t2 = time.perf_counter()
            uuid, native = _sample_native(new, weights, lats.size)
            t3 = time.perf_counter()
            if uuid != grid.uuid:
                print(f"{label:<15} native file on grid {uuid!r}, expected {grid.uuid}")
                continue
            for method, arr in native.items():
                _report(label, method, legacy, arr, names)
            print(f"{'':<15} sizes lat-lon {len(old) / 1e6:.2f} MB (unpacked bz2) vs "
                  f"native {len(new) / 1e6:.2f} MB; decode {t2 - t1:.2f}s vs {t3 - t2:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
