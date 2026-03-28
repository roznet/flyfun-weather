#!/usr/bin/env python3
"""Generate a GPX file simulating a flight along a briefing route.

Reads route_points.json and flight metadata from a pack, produces a GPX
with realistic timing and altitude profile (climb → cruise → descent).

Usage:
    python scripts/generate_flight_gpx.py FLIGHT_ID [--speed-kt 100] [--output path.gpx]

Examples:
    # Use latest pack, default 100kt groundspeed, departure time from flight
    python scripts/generate_flight_gpx.py egtf_egbj-2026-03-28-6d62

    # 10x speed — 41min flight replays in ~4min
    python scripts/generate_flight_gpx.py egtf_egbj-2026-03-28-6d62 --time-scale 10

    # Override departure to "now + 5min", 10x speed
    python scripts/generate_flight_gpx.py egtf_egbj-2026-03-28-6d62 --depart-in 5 --time-scale 10
"""

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent


def find_pack_dir(data_dir: Path, flight_id: str) -> Path | None:
    """Find the latest pack directory for a flight."""
    flight_dir = None
    for user_dir in data_dir.glob("*"):
        candidate = user_dir / flight_id
        if candidate.is_dir():
            flight_dir = candidate
            break
    if not flight_dir:
        return None
    # Latest pack by directory name (ISO timestamp sorts correctly)
    packs = sorted(flight_dir.iterdir(), reverse=True)
    for p in packs:
        if p.is_dir() and (p / "route_points.json").exists():
            return p
    return None


def load_route_points(pack_dir: Path) -> list[dict]:
    with open(pack_dir / "route_points.json") as f:
        return json.load(f)


def load_flight_meta(pack_dir: Path) -> dict:
    """Extract flight metadata from briefing.json or route_analyses.json."""
    # Try route_analyses first (has departure_time, cruise_altitude_ft)
    ra_path = pack_dir / "route_analyses.json"
    if ra_path.exists():
        with open(ra_path) as f:
            data = json.load(f)
        return {
            "departure_time": data.get("departure_time"),
            "cruise_altitude_ft": data.get("cruise_altitude_ft", 4000),
            "flight_duration_hours": data.get("flight_duration_hours", 1.0),
            "total_distance_nm": data.get("total_distance_nm"),
        }
    # Fall back to briefing.json
    briefing_path = pack_dir / "briefing.json"
    if briefing_path.exists():
        with open(briefing_path) as f:
            data = json.load(f)
        route = data.get("route", {})
        return {
            "departure_time": data.get("departure_time"),
            "cruise_altitude_ft": route.get("cruise_altitude_ft", 4000),
            "flight_duration_hours": route.get("flight_duration_hours", 1.0),
            "total_distance_nm": None,
        }
    return {"departure_time": None, "cruise_altitude_ft": 4000,
            "flight_duration_hours": 1.0, "total_distance_nm": None}


def altitude_profile(fraction: float, cruise_ft: float, field_elev_ft: float = 200) -> float:
    """Compute altitude at a given fraction (0-1) along route.

    Simulates: climb first 15%, cruise middle, descend last 15%.
    """
    climb_end = 0.15
    descent_start = 0.85

    if fraction < climb_end:
        # Linear climb from field elevation to cruise
        t = fraction / climb_end
        return field_elev_ft + t * (cruise_ft - field_elev_ft)
    elif fraction > descent_start:
        # Linear descent from cruise to field elevation
        t = (fraction - descent_start) / (1.0 - descent_start)
        return cruise_ft - t * (cruise_ft - field_elev_ft)
    else:
        return cruise_ft


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    R = 3440.065  # Earth radius in nm
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def interpolate_points(points: list[dict], spacing_nm: float = 0.5) -> list[dict]:
    """Interpolate route points to finer spacing for smooth GPX track."""
    if len(points) < 2:
        return points

    result = [points[0]]
    for i in range(len(points) - 1):
        p1 = points[i]
        p2 = points[i + 1]
        seg_dist = haversine_nm(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
        if seg_dist < 0.01:
            continue
        n_steps = max(1, int(seg_dist / spacing_nm))
        for s in range(1, n_steps + 1):
            t = s / n_steps
            result.append({
                "lat": p1["lat"] + t * (p2["lat"] - p1["lat"]),
                "lon": p1["lon"] + t * (p2["lon"] - p1["lon"]),
                "distance_from_origin_nm": (
                    p1.get("distance_from_origin_nm", 0) +
                    t * (p2.get("distance_from_origin_nm", 0) - p1.get("distance_from_origin_nm", 0))
                ),
            })
    return result


def generate_gpx(points: list[dict], departure: datetime, speed_kt: float,
                 cruise_ft: float, total_dist_nm: float, time_scale: float = 1.0) -> Element:
    """Build GPX XML from interpolated points with timestamps and altitude."""
    # Xcode requires <wpt> waypoints (not <trk>/<trkpt>) for movement simulation.
    # It interpolates speed from time differences between consecutive waypoints.
    gpx = Element("gpx", {
        "version": "1.1",
        "creator": "Xcode",
        "xmlns": "http://www.topografix.com/GPX/1/1",
    })

    for pt in points:
        dist = pt.get("distance_from_origin_nm", 0)
        fraction = dist / total_dist_nm if total_dist_nm > 0 else 0
        elapsed_hours = dist / speed_kt if speed_kt > 0 else 0
        timestamp = departure + timedelta(hours=elapsed_hours / time_scale)
        alt_ft = altitude_profile(fraction, cruise_ft)
        alt_m = alt_ft * 0.3048

        wpt = SubElement(gpx, "wpt", {
            "lat": f"{pt['lat']:.6f}",
            "lon": f"{pt['lon']:.6f}",
        })
        SubElement(wpt, "ele").text = f"{alt_m:.1f}"
        SubElement(wpt, "time").text = timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")

    return gpx


def main():
    parser = argparse.ArgumentParser(description="Generate GPX from a flight briefing pack")
    parser.add_argument("flight_id", help="Flight ID (e.g. egtf_egbj-2026-03-28-6d62)")
    parser.add_argument("--speed-kt", type=float, default=100, help="Groundspeed in knots (default: 100)")
    parser.add_argument("--output", "-o", help="Output GPX path (default: scripts/<flight_id>.gpx)")
    parser.add_argument("--depart-in", type=float, help="Override departure to N minutes from now")
    parser.add_argument("--data-dir", default="data/packs", help="Pack data directory")
    parser.add_argument("--spacing-nm", type=float, default=0.5, help="Point spacing in nm (default: 0.5)")
    parser.add_argument("--time-scale", type=float, default=1.0,
                        help="Time compression factor: 10 = 10x faster replay (default: 1.0 = real-time)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    pack_dir = find_pack_dir(data_dir, args.flight_id)
    if not pack_dir:
        print(f"Error: No pack found for flight {args.flight_id} in {data_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Using pack: {pack_dir.name}")

    points = load_route_points(pack_dir)
    meta = load_flight_meta(pack_dir)

    # Compute total distance from points if not in metadata
    total_dist = meta.get("total_distance_nm")
    if not total_dist:
        total_dist = points[-1].get("distance_from_origin_nm", 0) if points else 0
    if total_dist == 0:
        # Compute from haversine
        total_dist = sum(
            haversine_nm(points[i]["lat"], points[i]["lon"], points[i + 1]["lat"], points[i + 1]["lon"])
            for i in range(len(points) - 1)
        )

    cruise_ft = meta.get("cruise_altitude_ft", 4000)

    # Departure time
    if args.depart_in is not None:
        departure = datetime.now(timezone.utc) + timedelta(minutes=args.depart_in)
    elif meta.get("departure_time"):
        departure = datetime.fromisoformat(meta["departure_time"])
    else:
        departure = datetime.now(timezone.utc) + timedelta(minutes=5)
        print(f"No departure time found, using now + 5min")

    print(f"Route: {points[0].get('waypoint_icao', '?')} → {points[-1].get('waypoint_icao', '?')}")
    print(f"Distance: {total_dist:.1f} nm, Cruise: {cruise_ft} ft, Speed: {args.speed_kt} kt")
    print(f"Departure: {departure.isoformat()}")
    real_duration_min = total_dist / args.speed_kt * 60
    replay_duration_min = real_duration_min / args.time_scale
    print(f"ETA: {(departure + timedelta(minutes=real_duration_min)).isoformat()} ({real_duration_min:.0f} min real)")
    if args.time_scale != 1.0:
        print(f"Replay: {replay_duration_min:.1f} min at {args.time_scale}x speed")

    # Interpolate for smooth track
    fine_points = interpolate_points(points, spacing_nm=args.spacing_nm)
    print(f"Points: {len(points)} route → {len(fine_points)} interpolated")

    gpx = generate_gpx(fine_points, departure, args.speed_kt, cruise_ft, total_dist, args.time_scale)

    # Output
    output_path = args.output or f"scripts/{args.flight_id}.gpx"
    indent(gpx)
    tree = ElementTree(gpx)
    tree.write(output_path, xml_declaration=True, encoding="UTF-8")
    print(f"\nWrote {output_path}")
    print(f"Use in Xcode: Debug > Simulate Location > {output_path}")


if __name__ == "__main__":
    main()
