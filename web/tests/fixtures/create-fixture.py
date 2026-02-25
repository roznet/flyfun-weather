#!/usr/bin/env python3
"""
Generate trimmed Playwright test fixtures from a real flight briefing.

Usage:
    python create-fixture.py <flight_id> [--base-url http://localhost:8000]

Example:
    python create-fixture.py egtf_eglf-2026-02-25-45ed
    python create-fixture.py lfbo_lfrs-2026-03-01-a1b2 --base-url http://localhost:8000

Creates a fixture directory under fixtures/<route_name>/ with trimmed JSON files
suitable for Playwright tests (~200 KB instead of ~3+ MB).

Trimming rules:
  - Snapshot forecasts: keep only ecmwf + gfs models
  - Hourly data: keep only flight hours ± 1 hour
  - Pressure levels: keep only the 8 lowest (highest hPa)
  - Analyses: filter per-model dicts to ecmwf + gfs
  - Pack metadata: set has_gramet=False, keep only ecmwf + gfs init times
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

KEEP_MODELS = ("ecmwf", "gfs")
KEEP_PRESSURE_LEVELS = 8
HOURS_BEFORE = 1
HOURS_AFTER = 1


def api_get(base_url: str, path: str):
    """Fetch JSON from an API endpoint."""
    url = f"{base_url}/api{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as resp:
        if resp.status != 200:
            raise RuntimeError(f"GET {url} returned {resp.status}")
        return json.loads(resp.read())


def api_get_safe(base_url: str, path: str):
    """Fetch JSON, return None on error."""
    try:
        return api_get(base_url, path)
    except Exception as e:
        print(f"  warning: {path} -> {e}")
        return None


def trim_snapshot(snapshot: dict, target_hour: int) -> dict:
    """Trim a snapshot to keep only relevant models, hours, and pressure levels."""
    # Determine which hours to keep based on target flight time
    target_date = snapshot["target_date"]
    flight_duration = snapshot["route"].get("flight_duration_hours", 1.0)

    # Build set of hours to keep: 1 before flight start through 1 after flight end
    keep_hours = set()
    start_hour = max(0, target_hour - HOURS_BEFORE)
    end_hour = min(23, target_hour + int(flight_duration) + HOURS_AFTER)
    for h in range(start_hour, end_hour + 1):
        keep_hours.add(f"{target_date}T{h:02d}:00:00")

    print(f"  keeping hours: {sorted(keep_hours)}")

    # Filter forecasts to keep models
    snapshot["forecasts"] = [
        f for f in snapshot["forecasts"] if f["model"] in KEEP_MODELS
    ]

    # Trim hourly data and pressure levels
    for f in snapshot["forecasts"]:
        f["hourly"] = [h for h in f["hourly"] if h["time"] in keep_hours]
        for h in f["hourly"]:
            if h.get("pressure_levels"):
                h["pressure_levels"] = sorted(
                    h["pressure_levels"], key=lambda p: -p["pressure_hpa"]
                )[:KEEP_PRESSURE_LEVELS]

    # Trim analyses per-model dicts
    for a in snapshot.get("analyses", []):
        for section in ("wind_components", "sounding"):
            if section in a:
                a[section] = {
                    k: v for k, v in a[section].items() if k in KEEP_MODELS
                }
        if "model_divergence" in a:
            for md in a["model_divergence"]:
                if "model_values" in md:
                    md["model_values"] = {
                        k: v
                        for k, v in md["model_values"].items()
                        if k in KEEP_MODELS
                    }

    return snapshot


def write_compact_json(data, path: str):
    """Write JSON with no whitespace for minimal file size."""
    with open(path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    size = os.path.getsize(path)
    print(f"  {os.path.basename(path):25s} {size:>8,} bytes")
    return size


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("flight_id", help="Flight ID (e.g. egtf_eglf-2026-02-25-45ed)")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: fixtures/<route_name>)")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    fid = args.flight_id
    enc_fid = urllib.parse.quote(fid, safe="")

    # 1. Fetch flight info
    print(f"Fetching flight {fid}...")
    flight = api_get(base, f"/flights/{enc_fid}")
    route_name = flight["route_name"]
    target_hour = flight.get("target_time_utc", 12)

    # 2. Determine output directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dest = args.output_dir or os.path.join(script_dir, route_name)
    os.makedirs(dest, exist_ok=True)
    print(f"Output: {dest}")

    # 3. Fetch pack list and pick the latest
    packs = api_get(base, f"/flights/{enc_fid}/packs")
    if not packs:
        print("No packs found for this flight.")
        sys.exit(1)
    timestamp = packs[0]["fetch_timestamp"]
    enc_ts = urllib.parse.quote(timestamp, safe="")
    print(f"Using pack: {timestamp}")

    # 4. Fetch pack metadata
    pack_meta = api_get(base, f"/flights/{enc_fid}/packs/{enc_ts}")

    # 5. Fetch snapshot and trim it
    print("Fetching and trimming snapshot...")
    snapshot = api_get(base, f"/flights/{enc_fid}/packs/{enc_ts}/snapshot")
    snapshot = trim_snapshot(snapshot, target_hour)

    # 6. Fetch remaining endpoints
    route_analyses = api_get_safe(base, f"/flights/{enc_fid}/packs/{enc_ts}/route-analyses")
    advisories = api_get_safe(base, f"/flights/{enc_fid}/packs/{enc_ts}/advisories")
    elevation = api_get_safe(base, f"/flights/{enc_fid}/packs/{enc_ts}/elevation")
    digest = api_get_safe(base, f"/flights/{enc_fid}/packs/{enc_ts}/digest/json")

    # 7. Adjust metadata: no gramet, only kept models
    pack_meta["has_gramet"] = False
    for key in ("model_init_times", "grib_init_times"):
        if key in pack_meta:
            pack_meta[key] = {
                k: v for k, v in pack_meta[key].items() if k in KEEP_MODELS
            }

    for p in packs:
        p["has_gramet"] = False
        for key in ("model_init_times", "grib_init_times"):
            if key in p:
                p[key] = {
                    k: v for k, v in p[key].items() if k in KEEP_MODELS
                }

    # 8. Write all fixtures
    print("\nWriting fixtures:")
    total = 0
    total += write_compact_json(flight, os.path.join(dest, "flight.json"))
    total += write_compact_json(packs, os.path.join(dest, "packs.json"))
    total += write_compact_json(pack_meta, os.path.join(dest, "pack_meta.json"))
    total += write_compact_json(snapshot, os.path.join(dest, "snapshot.json"))

    if route_analyses:
        total += write_compact_json(route_analyses, os.path.join(dest, "route_analyses.json"))
    if advisories:
        total += write_compact_json(advisories, os.path.join(dest, "advisories.json"))
    if elevation:
        total += write_compact_json(elevation, os.path.join(dest, "elevation.json"))
    if digest:
        total += write_compact_json(digest, os.path.join(dest, "digest.json"))

    print(f"\n  {'TOTAL':25s} {total:>8,} bytes  ({total / 1024:.1f} KB)")
    print(f"\nDone! Fixtures written to {dest}")


if __name__ == "__main__":
    main()
