#!/usr/bin/env python3
"""Side-by-side diff of two Hewson snapshot trees (#911: GRIB vs Open-Meteo).

    compare_hewson_sources.py GRIB_ROOT OM_ROOT [--models ecmwf,gfs,icon] [--init ISO]
        [--route "LAT,LON LAT,LON ..." --depart ISO [--kt 120] [--alt 6000]]

Each root is a ``hewson/`` directory (``<model>/<init>.npz``). For every model
the newest init present in both trees is compared (or ``--init``,
e.g. ``2026-10-24T00:00:00Z``), on the valid times and grid points the two
share. So an Open-Meteo snapshot on the pre-#911 grid (101 × 193) compares
against a GRIB one on the trimmed grid (99 × 183) over the trimmed domain, and
the Open-Meteo series (anchored at 00 Z of its day) against the GRIB one
(anchored at init) on their common hours.

Per model and level it prints, over all shared cells and hours:
  θe       max / mean |Δ| (K)
  |∇θe|    max / mean |Δ| (K/100 km), and the mean of each side
  ≥ 4      cells where only one side reaches the 4 K/100 km gradient band,
           as a share of cells where either does

``--route`` adds what a briefing would see: the route front detector
(``tasks/fronts.compute_route_fronts``) run against each tree for that flight,
listing every accepted crossing per model and level (position, kind,
intensity, gradient). The fronts advisory grades from exactly these crossings,
so a crossing that appears, vanishes or changes kind is an advisory change.

No network, no Open-Meteo call. Typical use on the compute node, before the
next Open-Meteo cycle replaces the snapshot to compare against:

    python -m weatherbrief.hewson precompute --output-dir /tmp/hewson-grib --force
    scripts/compare_hewson_sources.py /tmp/hewson-grib "$DATA_DIR/hewson"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

GRADIENT_BAND = 4.0  # K / 100 km, the lower Hewson gradient band


def _common_index(a: np.ndarray, b: np.ndarray, tol: float = 1e-6):
    """Indices into a and b of the values they share (sorted axes)."""
    ia, ib = [], []
    for i, x in enumerate(a):
        j = np.flatnonzero(np.abs(b - x) <= tol)
        if j.size:
            ia.append(i)
            ib.append(int(j[0]))
    return np.array(ia, dtype=int), np.array(ib, dtype=int)


def _pick_init(model: str, roots: list[Path], init: str | None) -> str | None:
    names = [{p.name for p in (r / model).glob("*.npz")} for r in roots]
    shared = sorted(names[0] & names[1])
    if init:
        name = f"{init}.npz"
        return name if name in shared else None
    return shared[-1] if shared else None


def compare_model(model: str, a_path: Path, b_path: Path) -> list[str]:
    lines = [f"{model}  {a_path.name}"]
    with np.load(a_path) as a, np.load(b_path) as b:
        ti, tj = _common_index(
            a["valid_times"].astype("datetime64[s]").astype(np.int64).astype(float),
            b["valid_times"].astype("datetime64[s]").astype(np.int64).astype(float),
        )
        yi, yj = _common_index(a["lat"], b["lat"])
        xi, xj = _common_index(a["lon"], b["lon"])
        lines.append(
            f"  shared: {ti.size} hours, {yi.size} × {xi.size} points "
            f"(A {a['theta_e_850'].shape}, B {b['theta_e_850'].shape})"
        )
        if not (ti.size and yi.size and xi.size):
            lines.append("  nothing shared")
            return lines
        levels = sorted(set(a["levels"].tolist()) & set(b["levels"].tolist()))
        lines.append(
            "  level   θe max|Δ|  mean|Δ|   |∇θe| max|Δ|  mean|Δ|  mean A  mean B"
            f"   ≥{GRADIENT_BAND:g} disagree"
        )
        for L in levels:
            def sl(npz, key, t, y, x):
                return npz[f"{key}_{L}"][np.ix_(t, y, x)].astype(np.float64)

            te = np.abs(sl(a, "theta_e", ti, yi, xi) - sl(b, "theta_e", tj, yj, xj))
            ga = sl(a, "gradient", ti, yi, xi)
            gb = sl(b, "gradient", tj, yj, xj)
            gd = np.abs(ga - gb)
            in_a, in_b = ga >= GRADIENT_BAND, gb >= GRADIENT_BAND
            either = np.count_nonzero(in_a | in_b)
            disagree = np.count_nonzero(in_a ^ in_b)
            share = f"{100.0 * disagree / either:5.1f} %" if either else "   n/a"
            lines.append(
                f"  {L:4d}   {np.nanmax(te):8.2f}  {np.nanmean(te):7.3f}"
                f"   {np.nanmax(gd):11.2f}  {np.nanmean(gd):7.3f}"
                f"  {np.nanmean(ga):6.2f}  {np.nanmean(gb):6.2f}   {share}"
            )
    return lines


def _route_crossings(root: Path, waypoints, etas, alt_ft: int, models) -> dict:
    from weatherbrief.tasks.fronts import compute_route_fronts

    manifest = compute_route_fronts(
        waypoints, etas, route_name="compare", cruise_altitude_ft=alt_ft,
        advisory_models=models, output_dir=root,
    )
    out: dict[tuple[str, int], list[str]] = {}
    for model, analyses in manifest.per_model.items():
        for a in analyses:
            out[(model, a.level_hPa)] = [
                f"{c.distance_km:6.0f} km {c.kind:<16} {c.intensity:<11} "
                f"|∇θe| {c.gradient:5.2f}"
                for c in a.crossings
            ]
    return out


def compare_route(a_root: Path, b_root: Path, route: str, depart: str,
                  kt: float, alt_ft: int, models: list[str]) -> list[str]:
    from datetime import datetime, timedelta

    from weatherbrief.tasks.fronts import _cumulative_km

    waypoints = [tuple(float(v) for v in tok.split(",")) for tok in route.split()]
    t0 = datetime.fromisoformat(depart.replace("Z", "+00:00"))
    etas = [t0 + timedelta(hours=km / (kt * 1.852)) for km in _cumulative_km(waypoints)]
    a = _route_crossings(a_root, waypoints, etas, alt_ft, models)
    b = _route_crossings(b_root, waypoints, etas, alt_ft, models)
    lines = [f"route {route}  depart {depart}  {kt:g} kt  {alt_ft} ft"]
    changed = 0
    for key in sorted(set(a) | set(b)):
        ca, cb = a.get(key, []), b.get(key, [])
        same = [c.split(" |")[0] for c in ca] == [c.split(" |")[0] for c in cb]
        changed += not same
        lines.append(f"  {key[0]} {key[1]} hPa  {'same' if same else 'CHANGED'}")
        for tag, cs in (("A", ca), ("B", cb)):
            for c in cs or ["(no crossing)"]:
                lines.append(f"    {tag} {c}")
    lines.append(f"  {changed} model-level(s) with a different crossing set")
    return lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("a_root", type=Path, help="first hewson/ tree (e.g. GRIB)")
    p.add_argument("b_root", type=Path, help="second hewson/ tree (e.g. Open-Meteo)")
    p.add_argument("--models", default="ecmwf,gfs,icon")
    p.add_argument("--init", default=None, help="init stem, e.g. 2026-10-24T00:00:00Z")
    p.add_argument("--route", default=None, help='waypoints "lat,lon lat,lon ..."')
    p.add_argument("--depart", default=None, help="departure, ISO UTC")
    p.add_argument("--kt", type=float, default=120.0, help="ground speed (default 120)")
    p.add_argument("--alt", type=int, default=6000, help="cruise ft (default 6000)")
    args = p.parse_args(argv)

    print(f"A = {args.a_root}\nB = {args.b_root}\n")
    found = False
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        name = _pick_init(model, [args.a_root, args.b_root], args.init)
        if name is None:
            print(f"{model}: no init present in both trees\n")
            continue
        found = True
        print("\n".join(compare_model(model, args.a_root / model / name,
                                      args.b_root / model / name)))
        print()
    if args.route:
        if not args.depart:
            p.error("--route needs --depart")
        models = [m.strip() for m in args.models.split(",") if m.strip()]
        print("\n".join(compare_route(args.a_root, args.b_root, args.route,
                                      args.depart, args.kt, args.alt, models)))
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
