"""Calibrate the Met Office surface-pressure chart projection.

The colour charts carry no lat/lon graticule, so georeferencing is done by
clicking a handful of recognisable geographic features (coastline tips,
island centres, well-known points) on the 800x540 image, recording their
pixel coordinates, and fitting a 2D projective homography on top of a
polar-stereographic projection.

Workflow
--------
1. Open ``FSXX00T_00.gif`` (the +0h analysis) in an image viewer that shows
   pixel coordinates (Preview's "Tools > Show Inspector", GIMP pointer
   readout, or any browser dev-tools hover). Pixel origin is top-left,
   x right, y down.
2. For 6-10 well-spread features, record ``(name, lon, lat, x, y)``. Spread
   them to the corners + centre; more points = a more honest residual.
3. Write them to a JSON file (see ``--example``) and run::

       python -m weatherbrief.fetch.metoffice_calibrate points.json

   It fits the homography for the default projection, prints per-point
   residuals + max/RMS error, and the 8-tuple to paste into
   ``_CHART_CALIBRATIONS["colour"]["homography"]`` in metoffice_charts.py.
4. If residuals are large (> a few px), sweep the projection origin::

       python -m weatherbrief.fetch.metoffice_calibrate points.json --sweep

   and copy the winning ``proj`` block too.

A homography needs >= 4 points (8 equations); use more for a meaningful
residual. The same math produced the DWD calibrations.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ControlPoint:
    name: str
    lon: float
    lat: float
    x: float
    y: float


@dataclass
class FitResult:
    proj: dict
    homography: tuple[float, ...]
    residuals: list[tuple[str, float]]  # (name, pixel error)
    max_error: float
    rms_error: float


def load_points(path: Path) -> list[ControlPoint]:
    raw = json.loads(path.read_text())
    points = raw["points"] if isinstance(raw, dict) and "points" in raw else raw
    out: list[ControlPoint] = []
    for i, p in enumerate(points):
        out.append(
            ControlPoint(
                name=str(p.get("name", f"pt{i}")),
                lon=float(p["lon"]),
                lat=float(p["lat"]),
                x=float(p["x"]),
                y=float(p["y"]),
            )
        )
    if len(out) < 4:
        raise ValueError(f"need >= 4 control points to fit a homography, got {len(out)}")
    return out


def fit_homography(points: list[ControlPoint], proj_params: dict) -> FitResult:
    """Fit an 8-coef homography (lon/lat -> pixel) for a given projection.

    Projects each point with pyproj, then solves the linear DLT system for
    the projective transform that best maps projected metres to pixels.
    """
    import numpy as np
    import pyproj

    proj = pyproj.Proj(**proj_params)

    rows: list[list[float]] = []
    rhs: list[float] = []
    projected: list[tuple[float, float]] = []
    for p in points:
        X, Y = proj(p.lon, p.lat)
        projected.append((X, Y))
        # px = (aX + bY + c) / (gX + hY + 1)
        rows.append([X, Y, 1, 0, 0, 0, -p.x * X, -p.x * Y])
        rhs.append(p.x)
        # py = (dX + eY + f) / (gX + hY + 1)
        rows.append([0, 0, 0, X, Y, 1, -p.y * X, -p.y * Y])
        rhs.append(p.y)

    A = np.array(rows, dtype=float)
    b = np.array(rhs, dtype=float)
    coef, *_ = np.linalg.lstsq(A, b, rcond=None)
    a, bb, c, d, e, f, g, h = (float(v) for v in coef)

    residuals: list[tuple[str, float]] = []
    sq = 0.0
    for p, (X, Y) in zip(points, projected):
        denom = g * X + h * Y + 1
        px = (a * X + bb * Y + c) / denom
        py = (d * X + e * Y + f) / denom
        err = ((px - p.x) ** 2 + (py - p.y) ** 2) ** 0.5
        residuals.append((p.name, err))
        sq += err * err

    max_err = max(e for _, e in residuals)
    rms = (sq / len(residuals)) ** 0.5
    return FitResult(
        proj=proj_params,
        homography=(a, bb, c, d, e, f, g, h),
        residuals=residuals,
        max_error=max_err,
        rms_error=rms,
    )


def sweep(points: list[ControlPoint], base_proj: dict) -> FitResult:
    """Grid-search lon_0 / lat_ts to minimise max pixel error."""
    best: FitResult | None = None
    for lon_0 in range(-30, 31, 5):
        for lat_ts in (45, 50, 55, 60, 90):
            proj = dict(base_proj)
            proj["lon_0"] = lon_0
            proj["lat_ts"] = lat_ts
            try:
                res = fit_homography(points, proj)
            except Exception:
                continue
            if best is None or res.max_error < best.max_error:
                best = res
    if best is None:
        raise RuntimeError("sweep produced no valid fit")
    return best


_EXAMPLE = {
    "_comment": (
        "Pixel origin top-left; x right, y down. 800x540 colour chart. "
        "The x,y below are APPROXIMATE (from the current calibration) so the "
        "example runs without fitting a degenerate all-zero homography — "
        "REPLACE them with your own clicks when calibrating a fresh chart."
    ),
    "points": [
        {"name": "Lands End", "lon": -5.72, "lat": 50.07, "x": 418, "y": 325},
        {"name": "Brest", "lon": -4.49, "lat": 48.39, "x": 436, "y": 338},
        {"name": "Gibraltar", "lon": -5.35, "lat": 36.14, "x": 506, "y": 475},
        {"name": "Bergen", "lon": 5.32, "lat": 60.39, "x": 410, "y": 187},
        {"name": "Reykjavik", "lon": -21.94, "lat": 64.15, "x": 267, "y": 213},
        {"name": "Skagen", "lon": 10.59, "lat": 57.72, "x": 453, "y": 188},
    ],
}


def _print_fit(res: FitResult) -> None:
    print(f"\nprojection: {res.proj}")
    print(f"max error: {res.max_error:.2f} px   rms: {res.rms_error:.2f} px")
    print("per-point residuals:")
    for name, err in sorted(res.residuals, key=lambda r: -r[1]):
        print(f"  {err:7.2f} px  {name}")
    print("\nPaste into _CHART_CALIBRATIONS['colour'] in metoffice_charts.py:")
    print(f'    "proj": {res.proj!r},')
    print("    \"homography\": (")
    a, b, c, d, e, f, g, h = res.homography
    print(f"        {a!r}, {b!r}, {c!r},")
    print(f"        {d!r}, {e!r}, {f!r},")
    print(f"        {g!r}, {h!r},")
    print("    ),")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("points", nargs="?", type=Path, help="JSON file of control points")
    ap.add_argument("--sweep", action="store_true", help="grid-search projection origin")
    ap.add_argument("--lon0", type=float, default=0.0, help="polar-stereo central meridian")
    ap.add_argument("--lat-ts", type=float, default=60.0, help="polar-stereo true-scale latitude")
    ap.add_argument("--example", action="store_true", help="print an example points file and exit")
    args = ap.parse_args(argv)

    if args.example:
        print(json.dumps(_EXAMPLE, indent=2))
        return 0
    if not args.points:
        ap.error("points file required (or use --example)")

    pts = load_points(args.points)
    base_proj = {"proj": "stere", "lat_0": 90, "lat_ts": args.lat_ts, "lon_0": args.lon0}

    res = sweep(pts, base_proj) if args.sweep else fit_homography(pts, base_proj)
    _print_fit(res)
    if res.max_error > 5:
        print("\n[warn] max error > 5px — try --sweep, add/spread more points, or recheck clicks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
