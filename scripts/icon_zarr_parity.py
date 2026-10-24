#!/usr/bin/env python3
"""Full-run parity sweep for the ICON-EU zarr cache (#705).

    icon_zarr_parity.py RUN_DIR [--points N] [--keep]

``RUN_DIR`` is one cached ICON-EU run holding GRIB blobs, e.g.
``$DATA_DIR/.cache/grib/icon-eu/20261024_06z`` (written with ``WB_ICON_ZARR=0``
or before #705, so the GRIB is still there). For every whole-column blob
(``f{FFF}_ICON_EU_{VAR}.grib2``) it converts to a scratch store, decodes both
at a European route-like target set (``--points`` random points plus a few
fixed airports and edge points), and checks the outputs are identical: same
levels, same missing points, bit-identical floats. The source GRIB is never
touched.

Prints one line per blob and a summary: files, mismatches, dropped keys,
GRIB size vs store size, and the per-file times (direct GRIB decode, convert,
zarr read). Exit 1 on any mismatch. No network.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from weatherbrief.fetch.grib import decode as dec
from weatherbrief.fetch.grib import icon_zarr

_BLOB = re.compile(r"^f\d{3}_ICON_EU_[A-Z]+\.grib2$")


def _targets(n: int) -> tuple[list[float], list[float]]:
    rng = np.random.default_rng(705)
    lats = list(rng.uniform(30.0, 70.0, n))
    lons = list(rng.uniform(-23.0, 62.0, n))
    fixed = [
        (51.47, -0.45), (49.01, 2.55), (43.66, 7.21), (55.95, -3.37),
        (37.94, 23.94), (59.65, 17.92), (29.5, 0.0), (70.5, 10.0), (75.0, 10.0),
    ]
    return lats + [a for a, _ in fixed], lons + [b for _, b in fixed]


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--points", type=int, default=150)
    ap.add_argument("--keep", action="store_true", help="keep the scratch stores")
    args = ap.parse_args()

    blobs = sorted(p for p in args.run_dir.iterdir() if _BLOB.match(p.name))
    if not blobs:
        print(f"no ICON-EU whole-column GRIB blobs in {args.run_dir}", file=sys.stderr)
        return 2
    lats, lons = _targets(args.points)
    scratch = Path(tempfile.mkdtemp(prefix="icon-zarr-parity-"))
    bad = refused = 0
    grib_bytes = store_bytes = 0
    t_grib = t_conv = t_zarr = 0.0
    try:
        for blob in blobs:
            store = scratch / icon_zarr.store_name(blob.name)
            t0 = time.perf_counter()
            want = dec._decode_icon_eu_single_var(blob.read_bytes(), lats, lons)
            t1 = time.perf_counter()
            ok = icon_zarr.convert_grib_to_store(blob, store)
            t2 = time.perf_counter()
            if not ok:
                refused += 1
                print(f"{blob.name}: REFUSED (stays GRIB)")
                continue
            got = dec._decode_icon_eu_single_var(store, lats, lons)
            t3 = time.perf_counter()
            t_grib += t1 - t0
            t_conv += t2 - t1
            t_zarr += t3 - t2
            gs, zs = _size(blob), _size(store)
            grib_bytes += gs
            store_bytes += zs
            dropped = sorted(set(want) ^ set(got))
            diff = [lev for lev in want if lev in got and got[lev] != want[lev]]
            status = "OK" if not dropped and not diff else "MISMATCH"
            if status != "OK":
                bad += 1
            print(
                f"{blob.name}: {status} levels={len(want)} "
                f"grib={t1 - t0:.2f}s convert={t2 - t1:.2f}s zarr={t3 - t2:.3f}s "
                f"size={zs / gs:.2f}x"
                + (f" dropped={dropped}" if dropped else "")
                + (f" differ={diff[:5]}" if diff else "")
            )
            if not args.keep:
                shutil.rmtree(store, ignore_errors=True)
    finally:
        if not args.keep:
            shutil.rmtree(scratch, ignore_errors=True)
        else:
            print(f"stores kept in {scratch}")

    n = len(blobs) - refused
    print(
        f"\n{len(blobs)} blobs, {n} converted, {refused} refused, {bad} mismatched. "
        f"Disk {store_bytes / 1e6:.0f} MB zarr vs {grib_bytes / 1e6:.0f} MB GRIB "
        f"({store_bytes / max(grib_bytes, 1):.2f}x). Per file: GRIB decode "
        f"{t_grib / max(n, 1):.2f}s, convert {t_conv / max(n, 1):.2f}s, "
        f"zarr read {t_zarr / max(n, 1):.3f}s."
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
