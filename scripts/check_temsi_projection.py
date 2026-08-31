#!/usr/bin/env python3
"""Visually verify the Météo-France TEMSI calibration.

Draws two things on top of each cached TEMSI chart:

* **the re-projected graticule** (green) — every 5° parallel and meridian, run
  through the fitted calibration. The chart already prints its own graticule,
  so this is a direct, whole-chart check: where green sits on the chart's grey
  lines the projection is right, and where it drifts it is wrong. This matters
  most for the EUROC zone, whose control points cover only 45–50°N / 5°W–5°E
  while the chart spans 25–68°N / 30°W–55°E — the corners are extrapolation
  and only an edge-to-edge check can vouch for them.

* **a route** (red) — waypoints projected the same way the briefing overlay
  projects them, so what you see here is what the overlay will draw.

Usage::

    python scripts/check_temsi_projection.py --route LFAT LFQA LFSD LFML
    python scripts/check_temsi_projection.py --data-dir /tmp/mfcharts

With no cached charts it fetches a fresh set into ``--data-dir`` first, so it
works from a clean checkout given ``METEOFRANCE_API_CODE``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from weatherbrief.fetch import meteofrance_charts as mf  # noqa: E402

GRATICULE_COLOUR = (0, 170, 0)
ROUTE_COLOUR = (220, 0, 0)
LABEL_COLOUR = (0, 0, 0)

# Graticule extent per zone: enough to cover the drawn chart, clipped on draw.
ZONE_EXTENT: dict[str, tuple[float, float, float, float]] = {
    # lon_min, lon_max, lat_min, lat_max
    "france": (-12.0, 16.0, 38.0, 54.0),
    "euroc": (-35.0, 60.0, 22.0, 70.0),
}


def _resolve_route(codes: list[str]) -> list[tuple[str, float, float]]:
    """ICAO codes -> [(icao, lat, lon)] via the same resolver the app uses."""
    from weatherbrief.airports import resolve_waypoints

    db = os.environ.get("AIRPORTS_DB")
    if not db:
        raise SystemExit("AIRPORTS_DB is not set — needed to resolve the route")
    waypoints, rejected = resolve_waypoints(codes, db)
    for r in rejected:
        print(f"  ! rejected {r}", file=sys.stderr)
    return [(w.icao, w.lat, w.lon) for w in waypoints]


def _draw(zone: str, chart_path: Path, route: list[tuple[str, float, float]], out: Path) -> None:
    from PIL import Image, ImageDraw

    img = Image.open(chart_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size

    lon0, lon1, lat0, lat1 = ZONE_EXTENT[zone]

    def project(lon: float, lat: float) -> tuple[int, int]:
        return mf.lonlat_to_chart_pixel(lon, lat, zone)

    def polyline(points, colour, width):
        """Draw only the segments with at least one endpoint on-canvas.

        Far-field projected points can land thousands of pixels off-image;
        drawing those segments whole would streak across the chart.
        """
        for a, b in zip(points, points[1:]):
            if _on_canvas(a, w, h) or _on_canvas(b, w, h):
                draw.line([a, b], fill=colour, width=width)

    step = 0.25
    for lat in _frange(lat0, lat1, 5.0):
        polyline([project(lo, lat) for lo in _frange(lon0, lon1, step)],
                 GRATICULE_COLOUR, 1)
    for lon in _frange(lon0, lon1, 5.0):
        polyline([project(lon, la) for la in _frange(lat0, lat1, step)],
                 GRATICULE_COLOUR, 1)

    pts = [project(lon, lat) for _, lat, lon in route]
    polyline(pts, ROUTE_COLOUR, 3)
    for (icao, _, _), (px, py) in zip(route, pts):
        if not _on_canvas((px, py), w, h):
            print(f"  ! {icao} projects off-chart at ({px}, {py})")
            continue
        draw.ellipse([px - 5, py - 5, px + 5, py + 5], outline=ROUTE_COLOUR, width=3)
        draw.text((px + 9, py - 6), icao, fill=LABEL_COLOUR)

    img.save(out)
    print(f"  wrote {out}  ({w}x{h})")
    for (icao, lat, lon), (px, py) in zip(route, pts):
        print(f"    {icao}  {lat:7.3f},{lon:8.3f}  ->  ({px:5d}, {py:5d})")


def _on_canvas(pt: tuple[int, int], w: int, h: int, margin: int = 40) -> bool:
    x, y = pt
    return -margin <= x <= w + margin and -margin <= y <= h + margin


def _frange(lo: float, hi: float, step: float) -> list[float]:
    out, v = [], lo
    while v <= hi + 1e-9:
        out.append(round(v, 6))
        v += step
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--route", nargs="+", default=["LFAT", "LFQA", "LFSD", "LFML"])
    ap.add_argument("--data-dir", type=Path,
                    default=Path(os.environ.get("DATA_DIR", "data")))
    ap.add_argument("--out-dir", type=Path, default=Path("tmp/aeroweb"))
    args = ap.parse_args(argv)

    cycles = mf.list_cycles(args.data_dir)
    if not cycles:
        print(f"No cached TEMSI in {args.data_dir}; fetching…")
        report = mf.refresh_charts(args.data_dir)
        if report.error:
            raise SystemExit(f"refresh failed: {report.error}")
        cycles = mf.list_cycles(args.data_dir)
    if not cycles:
        raise SystemExit("still no cached charts")

    route = _resolve_route(args.route)
    print(f"Route {' '.join(args.route)}; cached validities: {', '.join(cycles)}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for zone in mf.CHART_IDS:
        # Newest validity that actually has *this* zone. The zones don't
        # publish in lockstep — EUROC has been seen a validity ahead of
        # France — so a single shared cycle would silently drop one chart.
        path, run_cycle = None, None
        for candidate in reversed(cycles):
            path = mf.resolve_chart_path(args.data_dir, candidate, zone)
            if path is not None:
                run_cycle = candidate
                break
        if path is None:
            print(f"  {zone}: not cached in any validity")
            continue
        print(f"  {zone}: valid {run_cycle}")
        if not mf._cache.is_calibrated(zone):
            print(f"  {zone}: not calibrated — skipping")
            continue
        _draw(zone, path, route, args.out_dir / f"projcheck_{zone}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
