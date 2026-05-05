"""One-shot Hewson route comparison for the May 1 vs May 4 calibration pair.

Reads the backdated precompute NPZs at ``data/hewson/<model>/2026-05-0{1,4}T00:00:00Z.npz``
and samples each route's waypoints at 850 hPa for the relevant hour window,
across all 3 models. Prints a side-by-side per-waypoint table colored against
the §3 thresholds from designs/future/hewson-fields-aviation-advisories.md.

Run from project root:
    python scripts/compare_hewson_may1_may4.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env")

from weatherbrief.airports import resolve_waypoints  # noqa: E402
from weatherbrief.frontal.route_sampling import bilinear_sample  # noqa: E402

MODELS = ("ecmwf", "gfs", "icon")
LEVELS = (925, 850)  # 925 ≈ 2,500 ft (low-level), 850 ≈ 5,000 ft (synoptic)
SNAPSHOT_DIR = ROOT / "data" / "hewson"

# Per-flight legs to interpolate intermediate sample points on. Each entry
# is (from_icao, to_icao, n_intermediate). Two intermediate points splits a
# leg into thirds — enough to localise a TFP zero-crossing without flooding
# the table.
INTERPOLATE_LEGS = {
    "2026-05-04": [
        ("LSGL", "LFQB", 2),  # Lausanne → Troyes — leg approaching front from south
        ("LFQB", "LFQA", 2),  # Troyes → Reims — leg crossing the Dijon-Reims front line
    ],
    "2026-05-01": [
        ("LFQA", "LFSD", 2),  # Reims → Dijon — same area, opposite direction
    ],
}

# Two flights to compare. Hours are UTC valid-time at 850 hPa.
FLIGHTS = [
    {
        "label": "May 1 EGTF→LSGS (smooth, no front)",
        "date": "2026-05-01",
        "route": "EGTF LFQA LFSD LSGL LSGS",
        "hours": (7, 8, 9, 10, 11),
    },
    {
        "label": "May 4 LSGS→EGTF (front Dijon→N. Reims)",
        "date": "2026-05-04",
        "route": "LSGS LSGL LFQB LFQA BILGO LFAT EGTF",
        "hours": (7, 8, 9, 10, 11, 12),
    },
]

# §3 thresholds. (warn, alert) — abs-value comparison.
THRESHOLDS: dict[str, tuple[float, float] | None] = {
    "theta_e": None,           # raw label, no threshold
    "gradient": (4.0, 8.0),    # K/100km — significant boundary / classical front
    "neg_laplacian": (2.0, 4.0),  # K/(100km)² — sharp edge
    "tfp": None,
    "advection": (1.0, 2.0),   # K/h — significant tendency / rapid frontal passage
    "tendency": (0.5, 1.0),    # K/h — destination tendency
}


def _color(value: float, thresholds: tuple[float, float] | None) -> str:
    if np.isnan(value):
        return f"{'   nan':>7s}"
    label = f"{value:+7.2f}"
    if thresholds is None:
        return label
    warn, alert = thresholds
    mag = abs(value)
    if mag >= alert:
        return f"\033[91m{label}\033[0m"  # red
    if mag >= warn:
        return f"\033[93m{label}\033[0m"  # yellow
    return label


def _load_snapshot(model: str, date: str) -> dict:
    path = SNAPSHOT_DIR / model / f"{date}T00:00:00Z.npz"
    if not path.exists():
        raise FileNotFoundError(f"missing snapshot {path}")
    with np.load(path) as z:
        return {k: z[k] for k in z.files}


def _hour_index(snap: dict, hour: int) -> int:
    """Find the time index for a given valid-hour UTC."""
    target = np.datetime64(
        f"{str(snap['valid_times'][0])[:10]}T{hour:02d}:00:00"
    )
    matches = np.where(snap["valid_times"] == target)[0]
    if len(matches) == 0:
        raise ValueError(f"hour {hour} not in snapshot valid_times")
    return int(matches[0])


def _interpolate_route(route: list, legs_to_split: list) -> list:
    """Insert ``n`` intermediate points between named legs.

    ``legs_to_split`` is a list of (from_icao, to_icao, n_intermediate) — for
    each matching adjacent pair, n linearly-spaced lat/lon points are inserted
    between them. Names are ``MID(<from>-<to>:k/n+1)`` so they're easy to read.
    Non-matching adjacent pairs are passed through unchanged.
    """
    if not legs_to_split:
        return list(route)

    leg_map = {(a, b): n for a, b, n in legs_to_split}
    expanded: list = [route[0]]
    for wp_a, wp_b in zip(route[:-1], route[1:]):
        n = leg_map.get((wp_a.icao, wp_b.icao))
        if n:
            for k in range(1, n + 1):
                frac = k / (n + 1)
                lat = wp_a.lat + frac * (wp_b.lat - wp_a.lat)
                lon = wp_a.lon + frac * (wp_b.lon - wp_a.lon)
                # Build a lightweight namespace-like object with same attrs
                class _MidPoint:
                    pass
                mp = _MidPoint()
                mp.icao = f".{wp_a.icao}-{wp_b.icao}.{k}/{n+1}"
                mp.lat = lat
                mp.lon = lon
                expanded.append(mp)
        expanded.append(wp_b)
    return expanded


def _sample_route(
    snap: dict,
    route: list,
    hour: int,
    level: int,
) -> list[dict]:
    """Sample all 6 metrics at the given pressure level for each waypoint."""
    h = _hour_index(snap, hour)
    lat = snap["lat"]
    lon = snap["lon"]
    metrics = ("theta_e", "gradient", "neg_laplacian", "tfp", "advection", "tendency")
    out = []
    for wp in route:
        row = {"name": wp.icao, "lat": wp.lat, "lon": wp.lon}
        for m in metrics:
            grid = snap[f"{m}_{level}"][h]
            row[m] = bilinear_sample(grid, lat, lon, wp.lat, wp.lon)
        out.append(row)
    return out


def _print_flight(flight: dict, db_path: str) -> None:
    print()
    print(f"=== {flight['label']} ({flight['date']}) ===")
    print(f"    Route: {flight['route']}  hours: {flight['hours'][0]:02d}Z–{flight['hours'][-1]:02d}Z")
    waypoints, rejected = resolve_waypoints(flight["route"].split(), db_path)
    if rejected:
        for r in rejected:
            print(f"    (rejected {r.name}: {r.reason})")

    legs_to_split = INTERPOLATE_LEGS.get(flight["date"], [])
    expanded = _interpolate_route(waypoints, legs_to_split)
    if legs_to_split:
        mid_names = [w.icao for w in expanded if w.icao.startswith(".")]
        print(f"    Mid-leg points: {', '.join(mid_names)}")
    print(f"    {len(expanded)} sample points: "
          f"{', '.join(f'{w.icao}({w.lat:.2f},{w.lon:.2f})' for w in expanded)}")

    for model in MODELS:
        try:
            snap = _load_snapshot(model, flight["date"])
        except FileNotFoundError as e:
            print(f"  {model}: {e}")
            continue
        for level in LEVELS:
            print(f"\n  --- {model.upper()} @ {level} hPa ---")
            cols = ["WP                        ", "  hr", "    θe", "  |∇θe|", " −∇²θe", "    TFP", " −V·∇θe", "  ∂/∂t"]
            print("  " + " ".join(cols))
            for hour in flight["hours"]:
                samples = _sample_route(snap, expanded, hour, level)
                for s in samples:
                    print(
                        f"  {s['name']:<26s} {hour:02d}Z "
                        f"{_color(s['theta_e'], THRESHOLDS['theta_e'])} "
                        f"{_color(s['gradient'], THRESHOLDS['gradient'])} "
                        f"{_color(s['neg_laplacian'], THRESHOLDS['neg_laplacian'])} "
                        f"{_color(s['tfp'], THRESHOLDS['tfp'])} "
                        f"{_color(s['advection'], THRESHOLDS['advection'])} "
                        f"{_color(s['tendency'], THRESHOLDS['tendency'])}"
                    )
                print()


def main() -> int:
    db_path = os.environ.get("AIRPORTS_DB")
    if not db_path:
        # WORKING_DIR isn't set in our shell; fall back to local data/nav.db
        candidate = ROOT / "data" / "nav.db"
        if candidate.exists():
            db_path = str(candidate)
        else:
            print("ERROR: AIRPORTS_DB not set and data/nav.db missing", file=sys.stderr)
            return 1

    print(f"AIRPORTS_DB: {db_path}")
    print(f"SNAPSHOT_DIR: {SNAPSHOT_DIR}")
    print(f"Models: {', '.join(MODELS)}    Levels: {', '.join(str(L) for L in LEVELS)} hPa")
    print()
    print("Threshold legend (color on |x|):")
    print("  |∇θe|       K/100km    yellow≥4  red≥8   (>4 = significant boundary, >8 = classical front)")
    print("  −∇²θe       K/(100km)² yellow≥2  red≥4   (>2 = sharp edge)")
    print("  −V·∇θe      K/h        yellow≥1  red≥2   (>1 = significant tendency, >2 = rapid frontal passage)")
    print("  ∂θe/∂t      K/h        yellow≥0.5 red≥1  (>0.5 = noticeable airport tendency)")

    for flight in FLIGHTS:
        _print_flight(flight, db_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
