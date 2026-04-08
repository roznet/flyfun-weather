#!/usr/bin/env python3
"""Compare ECMWF GRIB data with Open-Meteo ECMWF from a briefing pack.

Usage:
    python scripts/compare_ecmwf_grib.py <pack_dir> <grib_dir> [--step STEP]

Example:
    python scripts/compare_ecmwf_grib.py \
        data/packs/debug/egtf_lfat_lfqa-2026-04-10-b02a/2026-04-06T06-02-54.928542p00-00 \
        ~/tmp/ecmwf/data

Shows side-by-side comparison of:
  - Vertical resolution (13 vs 25 pressure levels)
  - Temperature, RH, wind at matching pressure levels
  - New GRIB-only fields: CLWC, CIWC, CC per level
  - Surface diagnostics: ceiling, cloud base, freezing level, cloud covers
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import eccodes
import numpy as np

# Add src to path for weatherbrief imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from weatherbrief.fetch.grib.ecmwf_fetch import (
    parse_ecmwf_filename,
    scan_ecmwf_files,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _bilinear_weight(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray):
    """Find the 4 surrounding grid points and bilinear weights.

    Returns (indices, weights) for bilinear interpolation, or None if
    the point is outside the grid.
    """
    # Find bounding indices
    lat_sorted = np.sort(lats) if lats[0] > lats[-1] else lats
    lon_sorted = lons  # assume ascending

    if lat < lat_sorted[0] or lat > lat_sorted[-1]:
        return None
    if lon < lon_sorted[0] or lon > lon_sorted[-1]:
        return None

    # Find surrounding lat/lon indices
    j_below = np.searchsorted(lat_sorted, lat) - 1
    j_below = max(0, min(j_below, len(lat_sorted) - 2))
    j_above = j_below + 1

    i_left = np.searchsorted(lon_sorted, lon) - 1
    i_left = max(0, min(i_left, len(lon_sorted) - 2))
    i_right = i_left + 1

    # Bilinear weights (computed in ascending-lat space)
    lat0, lat1 = lat_sorted[j_below], lat_sorted[j_above]
    lon0, lon1 = lon_sorted[i_left], lon_sorted[i_right]

    dlat = (lat1 - lat0) if lat1 != lat0 else 1.0
    dlon = (lon1 - lon0) if lon1 != lon0 else 1.0

    t = (lat - lat0) / dlat  # 0 = at j_below (lower lat), 1 = at j_above (higher lat)
    s = (lon - lon0) / dlon

    # If original lats are descending, flip j indices and invert t
    # so weights remain consistent with the remapped positions.
    if lats[0] > lats[-1]:
        j_below = len(lats) - 1 - j_below
        j_above = len(lats) - 1 - j_above
        t = 1 - t

    weights = [
        ((j_below, i_left), (1 - t) * (1 - s)),
        ((j_below, i_right), (1 - t) * s),
        ((j_above, i_left), t * (1 - s)),
        ((j_above, i_right), t * s),
    ]
    return weights


def _interp_value(values_2d: np.ndarray, weights) -> float | None:
    """Apply bilinear weights to a 2D grid."""
    if weights is None:
        return None
    result = 0.0
    for (j, i), w in weights:
        if 0 <= j < values_2d.shape[0] and 0 <= i < values_2d.shape[1]:
            result += w * values_2d[j, i]
        else:
            return None
    return float(result)


# ── GRIB Decode ──────────────────────────────────────────────────────────────

# Pressure-level variables we want from a2 files
_PL_VARS = {"t", "r", "u", "v", "cc", "clwc", "ciwc", "w", "z"}
# Surface variables from a1 files
_SFC_VARS = {"lcc", "mcc", "hcc", "tcc", "ceil", "cbh", "deg0l", "vis", "sp", "2t", "2d"}


def decode_grib_at_points(
    file_path: Path,
    lats: list[float],
    lons: list[float],
    target_vars: set[str],
) -> dict[str, dict[int, list[float | None]]]:
    """Decode a GRIB file and interpolate to route points.

    Returns: {shortName: {level: [value_per_point, ...]}}
    For surface fields, level is 0.
    """
    results: dict[str, dict[int, list[float | None]]] = defaultdict(
        lambda: defaultdict(lambda: [None] * len(lats))
    )

    with open(file_path, "rb") as f:
        while True:
            msgid = eccodes.codes_grib_new_from_file(f)
            if msgid is None:
                break
            try:
                name = eccodes.codes_get(msgid, "shortName")
                if name not in target_vars:
                    continue

                level = eccodes.codes_get(msgid, "level")
                ni = eccodes.codes_get(msgid, "Ni")
                nj = eccodes.codes_get(msgid, "Nj")
                lat1 = eccodes.codes_get(msgid, "latitudeOfFirstGridPointInDegrees")
                lat2 = eccodes.codes_get(msgid, "latitudeOfLastGridPointInDegrees")
                lon1 = eccodes.codes_get(msgid, "longitudeOfFirstGridPointInDegrees")
                lon2 = eccodes.codes_get(msgid, "longitudeOfLastGridPointInDegrees")

                # Assumes regular lat-lon grid (valid for IFS HRES European subarea;
                # would need scipy griddata for reduced Gaussian grids).
                grid_lats = np.linspace(lat1, lat2, nj)
                grid_lons = np.linspace(lon1, lon2, ni)

                values = eccodes.codes_get_values(msgid).reshape(nj, ni)

                for pt_idx, (pt_lat, pt_lon) in enumerate(zip(lats, lons)):
                    # Skip if already have a value for this point (from a prior grid)
                    if results[name][level][pt_idx] is not None:
                        continue
                    weights = _bilinear_weight(pt_lat, pt_lon, grid_lats, grid_lons)
                    val = _interp_value(values, weights)
                    if val is not None:
                        results[name][level][pt_idx] = val
            finally:
                eccodes.codes_release(msgid)

    return dict(results)


# ── Pack Loading ─────────────────────────────────────────────────────────────


def load_pack_ecmwf(pack_dir: Path) -> dict:
    """Load Open-Meteo ECMWF forecasts from a briefing pack."""
    forecasts = json.loads((pack_dir / "forecasts.json").read_text())
    route_points = json.loads((pack_dir / "route_points.json").read_text())

    # Filter ECMWF forecasts
    ecmwf_forecasts = [
        fc for fc in forecasts["forecasts"] if fc["model"] == "ecmwf"
    ]

    return {
        "route_points": route_points,
        "ecmwf_forecasts": ecmwf_forecasts,
    }


# ── Comparison ───────────────────────────────────────────────────────────────


def find_matching_grib_files(
    grib_dir: Path,
    target_valid_times: list[datetime] | None = None,
) -> tuple[dict[int, dict[str, Path]], datetime]:
    """Find a2 (pressure) and a1 (surface) files covering target valid times.

    Picks the base_time that has the most a2 (pressure-level) files
    covering the target valid times. This ensures we get the run with
    the best pressure-level coverage for the flight window.

    Returns: ({step_hours: {"a2": path, "a1": path}}, base_time)
    """
    files = scan_ecmwf_files(grib_dir, operational_only=False)
    if not files:
        return {}, datetime.now(tz=timezone.utc)

    # Group files by base_time
    by_bt: dict[datetime, list] = defaultdict(list)
    for f in files:
        by_bt[f.base_time].append(f)

    # Score each base_time by how many a2 files cover target valid times
    if target_valid_times:
        target_set = set(target_valid_times)
        best_bt = max(
            by_bt.keys(),
            key=lambda bt: sum(
                1 for f in by_bt[bt]
                if f.is_pressure_level and f.valid_time in target_set
            ),
        )
    else:
        best_bt = max(by_bt.keys())

    run_files = by_bt[best_bt]
    result: dict[int, dict[str, Path]] = defaultdict(dict)
    for f in run_files:
        part = "a2" if f.is_pressure_level else "a1" if f.is_surface else None
        if part:
            result[f.step_hours][part] = f.path

    return dict(result), best_bt


def compare_pressure_levels(
    om_hourly: dict,
    grib_pl: dict[str, dict[int, list[float | None]]],
    pt_idx: int,
) -> None:
    """Print pressure-level comparison for one point, one hour."""
    om_levels = {
        pl["pressure_hpa"]: pl for pl in om_hourly.get("pressure_levels", [])
    }
    om_pressures = sorted(om_levels.keys(), reverse=True)

    # All GRIB pressure levels (from temperature)
    grib_pressures = sorted(
        (lvl for lvl in grib_pl.get("t", {}).keys()), reverse=True
    )

    all_pressures = sorted(set(om_pressures) | set(grib_pressures), reverse=True)

    print(f"  {'hPa':>6s}  {'T(OM)':>7s} {'T(GRIB)':>8s} {'dT':>5s}  "
          f"{'RH(OM)':>7s} {'RH(GRIB)':>8s}  "
          f"{'CLWC':>10s} {'CIWC':>10s} {'CC%':>5s}")
    print(f"  {'─'*6}  {'─'*7} {'─'*8} {'─'*5}  {'─'*7} {'─'*8}  {'─'*10} {'─'*10} {'─'*5}")

    for p in all_pressures:
        om = om_levels.get(p)
        has_om = om is not None

        # Open-Meteo values
        t_om = om["temperature_c"] if has_om and om.get("temperature_c") is not None else None
        rh_om = om["relative_humidity_pct"] if has_om and om.get("relative_humidity_pct") is not None else None

        # GRIB values at this point
        t_grib_k = grib_pl.get("t", {}).get(p, [None] * (pt_idx + 1))[pt_idx]
        t_grib = (t_grib_k - 273.15) if t_grib_k is not None else None
        rh_grib = grib_pl.get("r", {}).get(p, [None] * (pt_idx + 1))[pt_idx]
        clwc = grib_pl.get("clwc", {}).get(p, [None] * (pt_idx + 1))[pt_idx]
        ciwc = grib_pl.get("ciwc", {}).get(p, [None] * (pt_idx + 1))[pt_idx]
        cc = grib_pl.get("cc", {}).get(p, [None] * (pt_idx + 1))[pt_idx]

        # Format
        t_om_s = f"{t_om:7.1f}" if t_om is not None else "   ·   "
        t_grib_s = f"{t_grib:8.1f}" if t_grib is not None else "    ·   "
        dt_s = f"{t_grib - t_om:+5.1f}" if (t_om is not None and t_grib is not None) else "  ·  "
        rh_om_s = f"{rh_om:7.1f}" if rh_om is not None else "   ·   "
        rh_grib_s = f"{rh_grib:8.1f}" if rh_grib is not None else "    ·   "
        clwc_s = f"{clwc:10.2e}" if clwc is not None and clwc > 0 else "     ·    " if clwc is None else "     0    "
        ciwc_s = f"{ciwc:10.2e}" if ciwc is not None and ciwc > 0 else "     ·    " if ciwc is None else "     0    "
        cc_s = f"{cc * 100:5.1f}" if cc is not None else "  ·  "

        marker = "  " if has_om else " *"  # * = GRIB-only level
        print(f"  {p:5d}{marker} {t_om_s} {t_grib_s} {dt_s}  {rh_om_s} {rh_grib_s}  {clwc_s} {ciwc_s} {cc_s}")


def compare_surface(
    om_hourly: dict,
    grib_sfc: dict[str, dict[int, list[float | None]]],
    pt_idx: int,
) -> None:
    """Print surface diagnostics comparison."""
    print(f"\n  Surface diagnostics:")
    print(f"  {'Field':<25s} {'Open-Meteo':>12s} {'GRIB':>12s}")
    print(f"  {'─'*25} {'─'*12} {'─'*12}")

    # Cloud covers (GRIB is 0-1 fraction)
    for var, label in [("lcc", "Low cloud cover %"),
                       ("mcc", "Mid cloud cover %"),
                       ("hcc", "High cloud cover %"),
                       ("tcc", "Total cloud cover %")]:
        grib_val = grib_sfc.get(var, {}).get(0, [None] * (pt_idx + 1))[pt_idx]
        grib_pct = grib_val * 100 if grib_val is not None else None

        om_key = {"lcc": "cloud_cover_low_pct", "mcc": "cloud_cover_mid_pct", "hcc": "cloud_cover_high_pct", "tcc": "cloud_cover_pct"}.get(var)
        om_val = om_hourly.get(om_key)

        om_s = f"{om_val:12.1f}" if om_val is not None else "           ·"
        grib_s = f"{grib_pct:12.1f}" if grib_pct is not None else "           ·"
        print(f"  {label:<25s} {om_s} {grib_s}")

    # Heights (GRIB in meters)
    for var, label in [("ceil", "Ceiling (ft)"),
                       ("cbh", "Cloud base (ft)"),
                       ("deg0l", "Freezing level (ft)")]:
        grib_val = grib_sfc.get(var, {}).get(0, [None] * (pt_idx + 1))[pt_idx]
        grib_ft = grib_val * 3.28084 if grib_val is not None else None
        # 9999m sentinel = no cloud
        if grib_val is not None and grib_val >= 9990:
            grib_ft = None
            grib_s = "    no cloud"
        else:
            grib_s = f"{grib_ft:12.0f}" if grib_ft is not None else "           ·"

        print(f"  {label:<25s} {'           ·':>12s} {grib_s}")

    # Visibility
    grib_vis = grib_sfc.get("vis", {}).get(0, [None] * (pt_idx + 1))[pt_idx]
    if grib_vis is not None:
        vis_km = grib_vis / 1000
        print(f"  {'Visibility (km)':<25s} {'           ·':>12s} {vis_km:12.1f}")


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Compare ECMWF GRIB vs Open-Meteo")
    parser.add_argument("pack_dir", type=Path, help="Briefing pack directory")
    parser.add_argument("grib_dir", type=Path, help="ECMWF GRIB data directory")
    parser.add_argument("--step", type=int, default=None,
                        help="Specific forecast step to compare (hours)")
    parser.add_argument("--waypoints-only", action="store_true",
                        help="Only show waypoint comparisons")
    args = parser.parse_args()

    # Load pack data
    pack = load_pack_ecmwf(args.pack_dir)
    route_points = pack["route_points"]
    ecmwf_fcs = pack["ecmwf_forecasts"]

    lats = [rp["lat"] for rp in route_points]
    lons = [rp["lon"] for rp in route_points]

    print(f"Pack: {args.pack_dir.name}")
    print(f"Route: {len(route_points)} points, "
          f"lat [{min(lats):.2f}, {max(lats):.2f}], "
          f"lon [{min(lons):.2f}, {max(lons):.2f}]")

    # Compute target valid times from Open-Meteo forecast hours
    all_valid_times = set()
    for fc in ecmwf_fcs:
        for hourly in fc["hourly"]:
            all_valid_times.add(datetime.fromisoformat(hourly["time"]))

    # Find GRIB files with best coverage for these valid times
    grib_files, grib_bt = find_matching_grib_files(
        args.grib_dir, target_valid_times=sorted(all_valid_times),
    )
    if not grib_files:
        print("ERROR: No GRIB files found")
        return 1

    print(f"GRIB base time: {grib_bt.isoformat()}")
    available_steps = sorted(grib_files.keys())
    print(f"Available steps: {available_steps[:20]}{'...' if len(available_steps) > 20 else ''}")

    # Match Open-Meteo forecast hours to GRIB steps
    # Open-Meteo hourly times are absolute; GRIB steps are hours from base_time
    if not ecmwf_fcs:
        print("ERROR: No ECMWF forecasts in pack")
        return 1

    # Use first waypoint's forecast to get available hours
    sample_fc = ecmwf_fcs[0]
    om_hours = sample_fc["hourly"]

    om_pressures = sorted(
        [pl['pressure_hpa'] for pl in om_hours[0]['pressure_levels']],
        reverse=True,
    )

    # Discover actual GRIB pressure levels from first available a2 file
    grib_pressures_discovered: list[int] = []
    for step_h in sorted(grib_files.keys()):
        if "a2" in grib_files[step_h]:
            sample_grib = decode_grib_at_points(
                grib_files[step_h]["a2"], lats[:1], lons[:1], {"t"},
            )
            grib_pressures_discovered = sorted(
                sample_grib.get("t", {}).keys(), reverse=True,
            )
            break

    print(f"\nOpen-Meteo ECMWF: {len(om_hours)} hourly forecasts")
    print(f"Open-Meteo pressure levels: {om_pressures}")
    print(f"GRIB pressure levels: {len(grib_pressures_discovered)} "
          f"({', '.join(str(p) for p in grib_pressures_discovered)})")

    # Build mapping: step_hours -> list of (wp_idx, hourly, wp_icao)
    # Deduplicate by (step, wp_icao) to avoid printing the same waypoint twice
    step_to_om: dict[int, list[tuple[int, dict, str]]] = {}
    seen_step_wp: set[tuple[int, str]] = set()
    for fc in ecmwf_fcs:
        wp = fc.get("waypoint", {})
        wp_icao = wp.get("icao", "")
        wp_idx = next(
            (i for i, rp in enumerate(route_points)
             if rp.get("waypoint_icao") == wp_icao),
            None,
        )
        if wp_idx is None:
            continue

        for h_idx, hourly in enumerate(fc["hourly"]):
            h_time = datetime.fromisoformat(hourly["time"])
            step_h = int((h_time - grib_bt).total_seconds() / 3600)
            if step_h < 0:
                continue
            # Snap to nearest available GRIB step (not hardcoded 3h)
            grib_step = min(grib_files.keys(), key=lambda s: abs(s - step_h)) if grib_files else step_h
            if grib_step in grib_files and abs(grib_step - step_h) <= 3:
                key = (grib_step, wp_icao)
                if key in seen_step_wp:
                    continue
                seen_step_wp.add(key)
                if grib_step not in step_to_om:
                    step_to_om[grib_step] = []
                step_to_om[grib_step].append((wp_idx, hourly, wp_icao))

    if args.step is not None:
        step_to_om = {k: v for k, v in step_to_om.items() if k == args.step}

    if not step_to_om:
        print("\nNo matching GRIB steps for the Open-Meteo forecast hours.")
        print("This may mean the GRIB base time doesn't overlap with the flight window.")
        return 1

    matching_steps = sorted(step_to_om.keys())
    print(f"\nMatching steps ({len(matching_steps)}): {matching_steps}")

    # Summary accumulators
    t_deltas = []
    rh_deltas = []
    max_clwc = 0.0
    max_ciwc = 0.0
    points_covered = 0
    points_total = 0

    # Compare each matching step
    for step_h in matching_steps:
        files = grib_files[step_h]
        print(f"\n{'='*80}")
        valid_time = grib_bt + timedelta(hours=step_h)
        print(f"Step +{step_h}h  (valid {valid_time.strftime('%Y-%m-%d %H:%MZ')})")
        print(f"{'='*80}")

        # Decode GRIB
        grib_pl = {}
        grib_sfc = {}
        if "a2" in files:
            grib_pl = decode_grib_at_points(files["a2"], lats, lons, _PL_VARS)
        if "a1" in files:
            grib_sfc = decode_grib_at_points(files["a1"], lats, lons, _SFC_VARS)

        if not grib_pl and not grib_sfc:
            print("  No GRIB data decoded for this step")
            continue

        # Show comparison at each waypoint
        for wp_idx, om_hourly, wp_icao in step_to_om[step_h]:
            points_total += 1
            # Check coverage
            t_val = grib_pl.get("t", {}).get(700, [None] * len(lats))[wp_idx]
            if t_val is not None:
                points_covered += 1

            print(f"\n  ── {wp_icao} (point {wp_idx}, "
                  f"lat={lats[wp_idx]:.2f}, lon={lons[wp_idx]:.2f}) ──")

            if grib_pl:
                compare_pressure_levels(om_hourly, grib_pl, wp_idx)

                # Accumulate stats
                om_levels = {
                    pl["pressure_hpa"]: pl
                    for pl in om_hourly.get("pressure_levels", [])
                }
                for p, om_pl in om_levels.items():
                    t_grib_k = grib_pl.get("t", {}).get(p, [None] * len(lats))[wp_idx]
                    if t_grib_k is not None and om_pl.get("temperature_c") is not None:
                        t_deltas.append(t_grib_k - 273.15 - om_pl["temperature_c"])
                    rh_grib = grib_pl.get("r", {}).get(p, [None] * len(lats))[wp_idx]
                    if rh_grib is not None and om_pl.get("relative_humidity_pct") is not None:
                        rh_deltas.append(rh_grib - om_pl["relative_humidity_pct"])

                # Track max microphysics
                for p in grib_pl.get("clwc", {}):
                    v = grib_pl["clwc"][p][wp_idx]
                    if v is not None:
                        max_clwc = max(max_clwc, v)
                for p in grib_pl.get("ciwc", {}):
                    v = grib_pl["ciwc"][p][wp_idx]
                    if v is not None:
                        max_ciwc = max(max_ciwc, v)

            if grib_sfc:
                compare_surface(om_hourly, grib_sfc, wp_idx)

    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print(f"Points covered by GRIB: {points_covered}/{points_total}")
    if t_deltas:
        print(f"Temperature delta (GRIB - OM): "
              f"mean={np.mean(t_deltas):+.2f}°C, "
              f"std={np.std(t_deltas):.2f}°C, "
              f"max={np.max(np.abs(t_deltas)):.2f}°C")
    if rh_deltas:
        print(f"RH delta (GRIB - OM):          "
              f"mean={np.mean(rh_deltas):+.1f}%, "
              f"std={np.std(rh_deltas):.1f}%, "
              f"max={np.max(np.abs(rh_deltas)):.1f}%")
    print(f"Max CLWC: {max_clwc:.2e} kg/kg")
    print(f"Max CIWC: {max_ciwc:.2e} kg/kg")
    grib_only = sorted(
        set(grib_pressures_discovered) - set(om_pressures), reverse=True,
    )
    if grib_only:
        print(f"GRIB-only levels (not in Open-Meteo): "
              f"{', '.join(str(p) for p in grib_only)} hPa")

    return 0


if __name__ == "__main__":
    sys.exit(main())
