#!/usr/bin/env python3
"""Replay real GRIB through the old (cfgrib) and new (eccodes) decoders (#674).

Phase 1 of #674 replaced cfgrib on the ECMWF a1/a2 and ICON-EU model-level
decode paths. Its acceptance bar is identical output on real data, apart from
one pinned edge rule (a zero-weight corner never blanks a value). The unit
tests prove it on synthetic files; this script is the check on real runs.

Usage:
    # ECMWF: any a1/a2 files (kind detected from the ECPDS file name)
    python scripts/grib_decode_parity.py --ecmwf $ECMWF_GRIB_DIR/<run files>...

    # ICON-EU model levels: one glob per variable (the files of one forecast
    # hour, concatenated in sorted order as the decode worker does)
    python scripts/grib_decode_parity.py \\
        --icon "<cache>/icon_eu/<run>/*_000_*_P.grib2" \\
        --icon "<cache>/icon_eu/<run>/*_000_*_T.grib2"

Targets: a fixed lattice over the ECMWF Europe/Nordic/US areas that is
deliberately off the 0.25° grid, plus points exactly on grid rows/columns and
known edge cases (EGVN row, the 59.5–60°N seam, US airports). Add your own
with ``--points lat,lon lat,lon ...``.

Exit status 1 when any value differs beyond ``--rtol`` or a key exists on one
side only (edge-rule differences are reported separately and do not fail).
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from weatherbrief.fetch.grib import decode as dec  # noqa: E402
from weatherbrief.fetch.grib.ecmwf_fetch import parse_ecmwf_filename  # noqa: E402
from weatherbrief.fetch.grib.grib_reader import DECODER_ENV  # noqa: E402


def _default_points() -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    lat = 35.13
    while lat < 71.5:
        lon = -17.37
        while lon < 40.0:
            pts.append((round(lat, 3), round(lon, 3)))
            lon += 1.37
        lat += 0.91
    for lat in (36.0, 45.25, 51.75, 59.5, 60.0):  # exactly on grid rows
        for lon in (-17.5, -0.25, 0.0, 5.0, 17.75, 39.5):
            pts.append((lat, lon))
    pts += [
        (51.7517, -0.0004),  # EGVN-like row neighbour
        (59.65, 17.92), (59.59, 16.63), (59.90, 17.59), (59.88, -1.30),  # seam (#672)
        (40.64, -73.78), (33.94, -118.41), (41.98, -87.90),  # US (#673)
    ]
    return pts


def _run(fn, *args, cfgrib: bool):
    if cfgrib:
        os.environ[DECODER_ENV] = "cfgrib"
    else:
        os.environ.pop(DECODER_ENV, None)
    t0 = time.perf_counter()
    out = fn(*args)
    return out, time.perf_counter() - t0


def _flatten(obj, prefix=()) -> dict[tuple, float]:
    flat: dict[tuple, float] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            flat.update(_flatten(v, prefix + (k,)))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            flat.update(_flatten(v, prefix + (i,)))
    elif obj is not None:
        flat[prefix] = float(obj)  # coverage booleans compare as 0/1
    return flat


def _compare(label: str, old, new, t_old: float, t_new: float, rtol: float) -> bool:
    fo, fn = _flatten(old), _flatten(new)
    only_old = sorted(set(fo) - set(fn))
    only_new = sorted(set(fn) - set(fo))
    common = set(fo) & set(fn)
    worst_abs = worst_rel = 0.0
    bad = []
    for k in common:
        a, b = fo[k], fn[k]
        d = abs(a - b)
        rel = d / max(abs(a), 1e-30)
        worst_abs, worst_rel = max(worst_abs, d), max(worst_rel, rel)
        if not math.isclose(a, b, rel_tol=rtol, abs_tol=1e-12):
            bad.append((k, a, b))
    print(f"\n== {label}")
    print(f"   cfgrib {t_old:6.2f}s   eccodes {t_new:6.2f}s   "
          f"(×{t_old / t_new if t_new else float('inf'):.1f})")
    print(f"   values compared {len(common)}   max |Δ| {worst_abs:.3g}   "
          f"max rel {worst_rel:.3g}   beyond rtol {len(bad)}")
    if only_old:
        print(f"   only in cfgrib output: {len(only_old)}  e.g. {only_old[:3]}")
    if only_new:
        # Expected only from the zero-weight edge rule (targets exactly on a
        # grid row/column next to a masked cell).
        print(f"   only in eccodes output (edge rule?): {len(only_new)}  e.g. {only_new[:3]}")
    for k, a, b in bad[:5]:
        print(f"   DIFF {k}: cfgrib={a!r} eccodes={b!r}")
    return not bad and not only_old


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ecmwf", nargs="*", default=[], help="ECMWF a1/a2 files")
    ap.add_argument("--icon", action="append", default=[], help="glob of one ICON-EU variable's level files")
    ap.add_argument("--points", nargs="*", default=[], help="extra lat,lon targets")
    ap.add_argument("--rtol", type=float, default=1e-9)
    args = ap.parse_args()

    pts = _default_points() + [tuple(map(float, p.split(","))) for p in args.points]
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    print(f"{len(pts)} target points")
    ok = True

    for f in args.ecmwf:
        path = Path(f)
        info = parse_ecmwf_filename(path)
        if info is None:
            print(f"\n== {path.name}: not an ECMWF file name, skipped")
            continue
        fn = (dec.decode_ecmwf_pressure_per_point if info.is_pressure_level
              else dec.decode_ecmwf_surface_per_point)
        old, t_old = _run(fn, path, lats, lons, cfgrib=True)
        new, t_new = _run(fn, path, lats, lons, cfgrib=False)
        ok &= _compare(f"{path.name} ({'a2' if info.is_pressure_level else 'a1'})",
                       old, new, t_old, t_new, args.rtol)

    for pattern in args.icon:
        files = sorted(glob.glob(pattern))
        if not files:
            print(f"\n== {pattern}: no files")
            continue
        blob = b"".join(Path(p).read_bytes() for p in files)
        old, t_old = _run(dec._decode_icon_eu_single_var, blob, lats, lons, cfgrib=True)
        new, t_new = _run(dec._decode_icon_eu_single_var, blob, lats, lons, cfgrib=False)
        ok &= _compare(f"ICON {pattern} ({len(files)} files)", old, new, t_old, t_new, args.rtol)

    print("\nPARITY OK" if ok else "\nPARITY FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
