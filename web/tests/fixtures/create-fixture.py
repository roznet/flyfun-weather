#!/usr/bin/env python3
"""
Generate trimmed Playwright test fixtures from a real flight briefing.

Usage:
    python create-fixture.py <flight_id> [--base-url http://localhost:8000]

Example:
    python create-fixture.py egtf_eglf-2026-02-25-45ed
    python create-fixture.py lfbo_lfrs-2026-03-01-a1b2 --base-url http://localhost:8000

Creates a fixture directory under fixtures/<route_name>/ with trimmed JSON files
suitable for Playwright tests.

The /snapshot endpoint serves briefing.json (no forecasts), so the fixture is
already small. Trimming rules for analyses:
  - Analyses: filter per-model dicts to ecmwf + gfs
  - Pack metadata: set has_gramet=False, keep only ecmwf + gfs init times
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

KEEP_MODELS = ("ecmwf", "gfs")


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


def trim_snapshot(snapshot: dict) -> dict:
    """Trim a snapshot/briefing to keep only ecmwf + gfs model data in analyses.

    The /snapshot endpoint now serves briefing.json which has no forecasts,
    so we only need to trim the analyses per-model dicts.
    """
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

    # 5. Fetch snapshot (briefing.json — no forecasts) and trim analyses
    print("Fetching and trimming snapshot...")
    snapshot = api_get(base, f"/flights/{enc_fid}/packs/{enc_ts}/snapshot")
    snapshot = trim_snapshot(snapshot)

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
