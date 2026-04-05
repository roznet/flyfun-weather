#!/usr/bin/env python3
"""
Generate trimmed Playwright test fixtures from a real flight briefing.

Usage:
    python create-fixture.py <flight_id> [--base-url http://localhost:8000]

Example:
    python create-fixture.py egtf_eglf-2026-02-25-45ed
    python create-fixture.py lfbo_lfrs-2026-03-01-a1b2 --base-url http://localhost:8000

Creates a fixture directory under fixtures/<route_name>/ with aggressively
trimmed JSON files suitable for Playwright tests.

Trimming rules:
  - Models: keep only ecmwf + gfs everywhere
  - Derived levels: cap at MAX_LEVELS per model per waypoint
  - Route points: cap at MAX_ROUTE_POINTS in route_analyses
  - Advisories per_model: trim to kept models only
  - Digest: validate against current WeatherDigest schema
  - Pack metadata: set has_gramet=False
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

KEEP_MODELS = {"ecmwf", "gfs"}
MAX_LEVELS = 5      # derived_levels per model per waypoint
MAX_ROUTE_POINTS = 4  # route analysis points


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


def _trim_model_dict(d: dict | None) -> dict:
    """Filter a dict keyed by model name to only KEEP_MODELS."""
    if not d:
        return {}
    return {k: v for k, v in d.items() if k in KEEP_MODELS}


def _trim_derived_levels(levels: list | None) -> list:
    """Keep at most MAX_LEVELS, evenly spaced through the profile."""
    if not levels or len(levels) <= MAX_LEVELS:
        return levels or []
    step = max(1, (len(levels) - 1) / (MAX_LEVELS - 1))
    indices = [round(i * step) for i in range(MAX_LEVELS)]
    return [levels[i] for i in indices]


def _trim_sounding(sounding: dict) -> dict:
    """Trim a single model's sounding: cap derived_levels."""
    if "derived_levels" in sounding:
        sounding["derived_levels"] = _trim_derived_levels(sounding["derived_levels"])
    return sounding


def trim_snapshot(snapshot: dict) -> dict:
    """Trim snapshot to 2 models and capped derived_levels."""
    for a in snapshot.get("analyses", []):
        # Trim per-model dicts
        for section in ("wind_components", "sounding"):
            if section in a:
                a[section] = _trim_model_dict(a[section])

        # Trim derived_levels within each sounding
        if "sounding" in a:
            for model, sd in a["sounding"].items():
                _trim_sounding(sd)

        # Trim model_divergence
        if "model_divergence" in a:
            for md in a["model_divergence"]:
                if "model_values" in md:
                    md["model_values"] = _trim_model_dict(md["model_values"])

    return snapshot


def trim_route_analyses(data: dict) -> dict:
    """Trim route analyses: 2 models, MAX_ROUTE_POINTS, capped levels."""
    analyses = data.get("analyses", [])

    # Cap route points — keep first, last, and evenly spaced middle
    if len(analyses) > MAX_ROUTE_POINTS:
        step = max(1, (len(analyses) - 1) / (MAX_ROUTE_POINTS - 1))
        indices = [round(i * step) for i in range(MAX_ROUTE_POINTS)]
        analyses = [analyses[i] for i in indices]
        data["analyses"] = analyses

    for a in analyses:
        for section in ("wind_components", "sounding"):
            if section in a:
                a[section] = _trim_model_dict(a[section])

        if "sounding" in a:
            for model, sd in a["sounding"].items():
                _trim_sounding(sd)

        if "model_divergence" in a:
            for md in a["model_divergence"]:
                if "model_values" in md:
                    md["model_values"] = _trim_model_dict(md["model_values"])

    # Update models list
    if "models" in data:
        data["models"] = [m for m in data["models"] if m in KEEP_MODELS]

    return data


def trim_advisories(data: dict) -> dict:
    """Light trim of advisories — keep all models for realistic aggregation testing."""
    # Don't trim per_model: the aggregation toggle test needs model disagreement
    # (e.g. 4 GREEN + 1 RED → majority=GREEN but worst=RED)
    return data


def make_worst_advisories(data: dict) -> dict:
    """Create a worst-aggregation variant from majority-aggregation advisories."""
    import copy
    worst = copy.deepcopy(data)
    worst["aggregation"] = "worst"

    status_order = {"green": 0, "amber": 1, "red": 2, "unavailable": -1}
    for adv in worst.get("advisories", []):
        per_model = adv.get("per_model", [])
        if per_model:
            worst_status = max(
                (m["status"] for m in per_model if m["status"] != "unavailable"),
                key=lambda s: status_order.get(s, -1),
                default="green",
            )
            adv["aggregate_status"] = worst_status
            # Use the detail from the worst model
            worst_model = next(
                (m for m in per_model if m["status"] == worst_status), per_model[0]
            )
            adv["aggregate_detail"] = worst_model.get("detail", adv.get("aggregate_detail", ""))

    return worst


def trim_digest(data: dict) -> dict:
    """Keep only the current 6-field schema."""
    current_fields = {
        "assessment", "assessment_reason", "synoptic",
        "specific_concerns", "trend", "watch_items",
    }
    return {k: v for k, v in data.items() if k in current_fields}


def trim_pack_meta(meta: dict) -> dict:
    """Trim pack metadata to kept models, no gramet."""
    meta["has_gramet"] = False
    for key in ("model_init_times", "grib_init_times"):
        if key in meta:
            meta[key] = _trim_model_dict(meta[key])
    return meta


def write_compact_json(data, path: str):
    """Write JSON with no whitespace for minimal file size."""
    with open(path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    size = os.path.getsize(path)
    print(f"  {os.path.basename(path):25s} {size:>8,} bytes")
    return size


def load_json_safe(path: str):
    """Load JSON from file, return None if missing."""
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def generate_from_pack(pack_dir: str, flight_id: str, dest: str):
    """Generate fixtures directly from pack files on disk (no server needed)."""
    pack_dir = os.path.abspath(pack_dir)
    timestamp = os.path.basename(pack_dir).replace("T", "T").replace("p", "+")

    # Load pack files
    snapshot = load_json_safe(os.path.join(pack_dir, "briefing.json"))
    if not snapshot:
        print(f"No briefing.json in {pack_dir}")
        sys.exit(1)

    route_name = snapshot["route"]["name"]
    waypoints = [wp["icao"] for wp in snapshot["route"]["waypoints"]]
    target_date = snapshot.get("target_date", "unknown")

    # Build synthetic flight.json (API response shape)
    flight = {
        "id": flight_id,
        "user_id": "dev-user-001",
        "profile_id": None,
        "route_name": route_name,
        "waypoints": waypoints,
        "target_date": target_date,
        "target_time_utc": 9,
        "cruise_altitude_ft": snapshot["route"].get("cruise_altitude_ft", 8000),
        "flight_ceiling_ft": snapshot["route"].get("flight_ceiling_ft", 18000),
        "flight_duration_hours": snapshot["route"].get("flight_duration_hours", 0),
        "auto_refresh": False,
        "created_at": timestamp,
    }

    # Build synthetic pack_meta
    pack_meta = {
        "flight_id": flight_id,
        "fetch_timestamp": timestamp,
        "days_out": snapshot.get("days_out", 0),
        "has_gramet": False,
        "has_digest": os.path.exists(os.path.join(pack_dir, "digest.json")),
        "has_advisories": os.path.exists(os.path.join(pack_dir, "route_advisories.json")),
        "assessment": None,
        "assessment_reason": None,
        "model_init_times": {},
        "grib_init_times": {},
    }

    # Load digest and set assessment
    digest = load_json_safe(os.path.join(pack_dir, "digest.json"))
    if digest:
        pack_meta["assessment"] = digest.get("assessment")
        pack_meta["assessment_reason"] = digest.get("assessment_reason")

    packs = [dict(pack_meta)]  # pack list is just one entry

    route_analyses = load_json_safe(os.path.join(pack_dir, "route_analyses.json"))
    advisories = load_json_safe(os.path.join(pack_dir, "route_advisories.json"))
    elevation = load_json_safe(os.path.join(pack_dir, "elevation_profile.json"))

    return flight, packs, pack_meta, snapshot, route_analyses, advisories, elevation, digest


def generate_from_api(base_url: str, flight_id: str):
    """Generate fixtures by fetching from a running API server."""
    base = base_url.rstrip("/")
    enc_fid = urllib.parse.quote(flight_id, safe="")

    print(f"Fetching flight {flight_id}...")
    flight = api_get(base, f"/flights/{enc_fid}")

    packs = api_get(base, f"/flights/{enc_fid}/packs")
    if not packs:
        print("No packs found for this flight.")
        sys.exit(1)
    timestamp = packs[0]["fetch_timestamp"]
    enc_ts = urllib.parse.quote(timestamp, safe="")
    print(f"Using pack: {timestamp}")

    pack_meta = api_get(base, f"/flights/{enc_fid}/packs/{enc_ts}")
    snapshot = api_get(base, f"/flights/{enc_fid}/packs/{enc_ts}/snapshot")
    route_analyses = api_get_safe(base, f"/flights/{enc_fid}/packs/{enc_ts}/route-analyses")
    advisories = api_get_safe(base, f"/flights/{enc_fid}/packs/{enc_ts}/advisories")
    elevation = api_get_safe(base, f"/flights/{enc_fid}/packs/{enc_ts}/elevation")
    digest = api_get_safe(base, f"/flights/{enc_fid}/packs/{enc_ts}/digest/json")

    return flight, packs, pack_meta, snapshot, route_analyses, advisories, elevation, digest


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("flight_id", help="Flight ID (e.g. egtf_eglf-2026-02-25-45ed)")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--from-pack", default=None, help="Load from pack directory on disk instead of API")
    parser.add_argument("--output-dir", default=None, help="Output directory")
    args = parser.parse_args()

    # Load data from disk or API
    if args.from_pack:
        print(f"Loading from pack: {args.from_pack}")
        flight, packs, pack_meta, snapshot, route_analyses, advisories, elevation, digest = (
            generate_from_pack(args.from_pack, args.flight_id, args.output_dir or "")
        )
    else:
        flight, packs, pack_meta, snapshot, route_analyses, advisories, elevation, digest = (
            generate_from_api(args.base_url, args.flight_id)
        )

    route_name = flight["route_name"]

    # Determine output directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dest = args.output_dir or os.path.join(script_dir, route_name)
    os.makedirs(dest, exist_ok=True)
    print(f"Output: {dest}")

    # Trim everything
    print("Trimming...")
    snapshot = trim_snapshot(snapshot)
    pack_meta = trim_pack_meta(pack_meta)
    packs = [trim_pack_meta(p) for p in packs]

    if route_analyses:
        route_analyses = trim_route_analyses(route_analyses)
    advisories_worst = None
    if advisories:
        advisories = trim_advisories(advisories)
        advisories_worst = make_worst_advisories(advisories)
    if digest:
        digest = trim_digest(digest)

    # Write all fixtures
    print("\nFixtures:")
    total = 0
    total += write_compact_json(flight, os.path.join(dest, "flight.json"))
    total += write_compact_json(packs, os.path.join(dest, "packs.json"))
    total += write_compact_json(pack_meta, os.path.join(dest, "pack_meta.json"))
    total += write_compact_json(snapshot, os.path.join(dest, "snapshot.json"))

    if route_analyses:
        total += write_compact_json(route_analyses, os.path.join(dest, "route_analyses.json"))
    if advisories:
        total += write_compact_json(advisories, os.path.join(dest, "advisories.json"))
    if advisories_worst:
        total += write_compact_json(advisories_worst, os.path.join(dest, "advisories_worst.json"))
    if elevation:
        total += write_compact_json(elevation, os.path.join(dest, "elevation.json"))
    if digest:
        total += write_compact_json(digest, os.path.join(dest, "digest.json"))

    print(f"\n  {'TOTAL':25s} {total:>8,} bytes  ({total / 1024:.1f} KB)")
    print(f"\nDone! Fixtures written to {dest}")


if __name__ == "__main__":
    main()
