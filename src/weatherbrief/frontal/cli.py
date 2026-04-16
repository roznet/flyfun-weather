"""Interactive CLI for frontal detection development and validation.

Runs the same detection code as the scheduled pipeline, with interactive
output: console tables, optional map plots, and cache for fast iteration.

Usage:
    python -m weatherbrief.frontal.cli analyze
    python -m weatherbrief.frontal.cli analyze --model ecmwf --plot
    python -m weatherbrief.frontal.cli zones --hour 24
    python -m weatherbrief.frontal.cli route --template uk_alps
    python -m weatherbrief.frontal.cli clear-cache
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from weatherbrief.frontal import cache
from weatherbrief.frontal.grid import (
    build_grid_coords,
    fetch_grid_fields,
    reshape_to_fields,
    build_terrain_mask,
)
from weatherbrief.frontal.tracking import (
    build_zone_timeseries,
    find_clearance_times_all_models,
    compute_timing_spread,
)
from weatherbrief.frontal.zones import ZONES, ROUTE_TEMPLATES

logger = logging.getLogger(__name__)

_DEFAULT_MODELS = ["ecmwf", "gfs", "icon"]

# Display horizons for the summary table (6h intervals)
_TABLE_HORIZONS = [0, 6, 12, 18, 24, 36, 48, 60, 72]


# ---------------------------------------------------------------------------
# Core analysis — shared by all subcommands
# ---------------------------------------------------------------------------


def _run_analysis(
    models: list[str],
    use_cache: bool = True,
    cache_dir: Path = cache._DEFAULT_CACHE_DIR,
    t_threshold: float = 0.8,
    te_threshold: float = 4.0,
) -> dict:
    """Fetch data and run frontal detection for all requested models.

    Returns dict with:
        lat, lon, terrain_mask,
        model_timeseries: {model: zone_timeseries},
        model_fields: {model: {hour: fields}},
        model_init_times: {model: init_time},
        timestamps: {model: [iso_timestamps]},
    """
    from weatherbrief.fetch.open_meteo import OpenMeteoClient

    client = OpenMeteoClient()
    lat, lon = build_grid_coords()

    logger.info("Building terrain mask...")
    terrain_mask = build_terrain_mask(lat, lon)

    model_timeseries = {}
    model_fields = {}
    model_init_times = {}
    model_timestamps = {}

    for model_key in models:
        logger.info("Processing model: %s", model_key)

        # Try cache first
        raw = None
        init_time = 0
        if use_cache:
            from weatherbrief.fetch.model_status import fetch_model_metadata

            meta = fetch_model_metadata(models=[model_key])
            if model_key in meta:
                init_time = meta[model_key].last_init_time
                raw = cache.load_raw_response(
                    model_key, str(init_time), cache_dir,
                )

        if raw is None:
            raw, timestamps, init_time = fetch_grid_fields(
                client, model_key, lat, lon,
            )
            if use_cache and init_time:
                cache.save_raw_response(
                    model_key, str(init_time), raw, cache_dir,
                )
        else:
            # Reconstruct timestamps from cached data
            first_var = next(iter(raw.values()))
            if first_var and first_var[0]:
                timestamps = [f"T+{i}" for i in range(len(first_var[0]))]
            else:
                timestamps = []

        model_init_times[model_key] = init_time
        model_timestamps[model_key] = timestamps

        # Reshape all available hours
        n_hours = len(timestamps)
        fields_by_hour: dict[int, dict] = {}
        for h in range(n_hours):
            fields = reshape_to_fields(raw, lat, lon, h, terrain_mask)
            if fields is not None:
                fields_by_hour[h] = fields

        model_fields[model_key] = fields_by_hour

        # Build zone timeseries
        ts = build_zone_timeseries(
            fields_by_hour,
            lat,
            lon,
            hours=range(n_hours),
            terrain_mask=terrain_mask,
            t_gradient_threshold=t_threshold,
            te_gradient_threshold=te_threshold,
        )
        model_timeseries[model_key] = ts

    return {
        "lat": lat,
        "lon": lon,
        "terrain_mask": terrain_mask,
        "model_timeseries": model_timeseries,
        "model_fields": model_fields,
        "model_init_times": model_init_times,
        "timestamps": model_timestamps,
    }


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------


def _front_symbol(entry: dict | None) -> str:
    """Single-char symbol for a zone's frontal status at one hour."""
    if entry is None or not entry["present"]:
        return "—"
    t = entry.get("type", "")
    intensity = entry.get("intensity", 0)
    strong = intensity > 2.0
    if t == "cold":
        return "C!" if strong else "C"
    elif t == "warm":
        return "W!" if strong else "W"
    elif t == "indeterminate":
        return "?!" if strong else "?"
    return "F"


def _print_analyze_table(result: dict, models: list[str]) -> None:
    """Print zone × horizon table per model."""
    for model_key in models:
        ts = result["model_timeseries"].get(model_key, {})
        init_time = result["model_init_times"].get(model_key, 0)
        init_str = (
            datetime.fromtimestamp(init_time, tz=timezone.utc).strftime("%Y-%m-%d %HZ")
            if init_time
            else "unknown"
        )

        print(f"\nFrontal Analysis — {model_key.upper()} {init_str}")
        print("─" * 80)

        # Header
        header = f"{'Zone':<32}"
        for h in _TABLE_HORIZONS:
            header += f"{'T+' + str(h):>6}"
        print(header)
        print("─" * 80)

        # Zone rows — only show zones with any frontal activity
        active_zones = []
        for zone_name in ZONES:
            entries = ts.get(zone_name, [])
            if any(e["present"] for e in entries):
                active_zones.append(zone_name)

        if not active_zones:
            print("  No frontal activity detected in any zone.")
        else:
            for zone_name in active_zones:
                entries = ts.get(zone_name, [])
                entry_by_hour = {e["hour"]: e for e in entries}
                display = ZONES[zone_name]["display"]
                row = f"{display:<32}"
                for h in _TABLE_HORIZONS:
                    row += f"{_front_symbol(entry_by_hour.get(h)):>6}"
                print(row)

        print()
        print("C=cold  W=warm  ?=indeterminate  !=strong (>2.0 K/100km)  —=clear")


def _print_zones(result: dict, model_key: str, hour: int) -> None:
    """Print all zones with frontal activity at one horizon."""
    ts = result["model_timeseries"].get(model_key, {})

    print(f"\nZone Activity — {model_key.upper()} T+{hour}h")
    print("─" * 72)
    print(
        f"{'Zone':<32} {'Type':<14} {'Intensity':>10} {'Coverage':>10} {'Orient':>8}"
    )
    print("─" * 72)

    any_active = False
    for zone_name in ZONES:
        entries = ts.get(zone_name, [])
        entry = next((e for e in entries if e["hour"] == hour), None)
        if entry and entry["present"]:
            any_active = True
            display = ZONES[zone_name]["display"]
            t = entry.get("type", "—")
            intensity = entry.get("intensity", 0)
            coverage = entry.get("coverage_fraction") or 0
            orient = entry.get("orientation", "—") or "—"
            print(
                f"{display:<32} {t:<14} {intensity:>9.1f} {coverage:>9.1%} {orient:>8}"
            )

    if not any_active:
        print("  No frontal activity at this hour.")


def _print_route(result: dict, zone_list: list[str], route_name: str) -> None:
    """Print route frontal table with clearance timing."""
    models = list(result["model_timeseries"].keys())

    print(f"\nRoute: {route_name}")
    print("─" * 72)

    # Header
    header = f"{'Segment':<32}"
    for m in models:
        header += f"{m.upper():>12}"
    header += f"{'Agree':>8}"
    print(header)
    print("─" * 72)

    for zone_name in zone_list:
        display = ZONES[zone_name]["display"]
        row = f"{display:<32}"
        symbols = []
        for m in models:
            ts = result["model_timeseries"][m]
            # Show status at T+0 (or first available hour)
            entries = ts.get(zone_name, [])
            entry = entries[0] if entries else None
            sym = _front_symbol(entry)
            row += f"{sym:>12}"
            symbols.append(sym)
        # Agreement: all symbols match
        agree = "Y" if len(set(symbols)) == 1 else "N"
        row += f"{agree:>8}"
        print(row)

    # Clearance timing
    print()
    print("Clearance timing:")
    for zone_name in zone_list:
        display = ZONES[zone_name]["display"]
        clearance = find_clearance_times_all_models(
            result["model_timeseries"], zone_name,
        )
        spread = compute_timing_spread(clearance)

        parts = []
        for m, t in clearance.items():
            if t is not None:
                parts.append(f"{m.upper()} T+{t}h")
            else:
                parts.append(f"{m.upper()} persists")

        if parts:
            timing_str = ", ".join(parts)
            spread_str = (
                f" (spread: {spread['spread_hours']}h)"
                if spread["spread_hours"] is not None
                else ""
            )
            print(f"  {display}: {timing_str}{spread_str}")


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------


def _plot_analysis(result: dict, model_key: str, hour: int, output: str | None) -> None:
    """Generate map plot of frontal detection for visual validation."""
    try:
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
        import matplotlib.pyplot as plt
    except ImportError:
        print(
            "Error: cartopy is required for plotting. "
            "Install with: pip install -e '.[frontal-dev]'",
            file=sys.stderr,
        )
        return

    from weatherbrief.frontal.detect import compute_frontal_zones_dual

    fields = result["model_fields"].get(model_key, {}).get(hour)
    if fields is None:
        print(f"No data for {model_key} at hour {hour}", file=sys.stderr)
        return

    lat = result["lat"]
    lon = result["lon"]
    terrain_mask = result["terrain_mask"]

    zones_result = compute_frontal_zones_dual(
        fields["T850"], fields["theta_e"], lat, lon,
        terrain_mask=terrain_mask,
    )

    fig, ax = plt.subplots(
        1, 1, figsize=(14, 10),
        subplot_kw={"projection": ccrs.PlateCarree()},
    )

    # Gradient magnitude
    lon_grid, lat_grid = np.meshgrid(lon, lat)
    im = ax.pcolormesh(
        lon_grid, lat_grid, zones_result["gradient"],
        cmap="YlOrRd", vmin=0, vmax=4.0,
        transform=ccrs.PlateCarree(), alpha=0.7,
    )
    plt.colorbar(im, ax=ax, label="Gradient (K/100km)", shrink=0.7)

    # Frontal mask contour
    ax.contour(
        lon_grid, lat_grid,
        zones_result["frontal_mask"].astype(float),
        levels=[0.5], colors="blue", linewidths=1.5,
        transform=ccrs.PlateCarree(),
    )

    # TFP zero-crossings (thin lines)
    ax.contour(
        lon_grid, lat_grid, zones_result["tfp"],
        levels=[0], colors="black", linewidths=0.5, linestyles="dashed",
        transform=ccrs.PlateCarree(),
    )

    # Zone boundaries
    for zone_name, bounds in ZONES.items():
        lat0, lat1 = bounds["lat"]
        lon0, lon1 = bounds["lon"]
        ax.plot(
            [lon0, lon1, lon1, lon0, lon0],
            [lat0, lat0, lat1, lat1, lat0],
            color="gray", linewidth=0.8, linestyle="--",
            transform=ccrs.PlateCarree(),
        )

    # Terrain mask (hatch)
    if terrain_mask is not None:
        ax.contourf(
            lon_grid, lat_grid, (~terrain_mask).astype(float),
            levels=[0.5, 1.5], colors="none",
            hatches=["///"], transform=ccrs.PlateCarree(),
        )

    ax.add_feature(cfeature.COASTLINE, linewidth=0.8)
    ax.add_feature(cfeature.BORDERS, linewidth=0.5, linestyle=":")
    ax.set_extent([-12, 28, 35, 60], crs=ccrs.PlateCarree())

    init_time = result["model_init_times"].get(model_key, 0)
    init_str = (
        datetime.fromtimestamp(init_time, tz=timezone.utc).strftime("%Y-%m-%d %HZ")
        if init_time
        else "unknown"
    )
    ax.set_title(f"Frontal Detection — {model_key.upper()} {init_str} T+{hour}h")

    if output:
        fig.savefig(output, dpi=150, bbox_inches="tight")
        print(f"Plot saved to {output}")
    else:
        plt.show()


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_analyze(args: argparse.Namespace) -> None:
    models = [args.model] if args.model else _DEFAULT_MODELS

    if args.dry_run:
        lat, lon = build_grid_coords()
        print(f"Grid: {len(lat)} lat × {len(lon)} lon = {len(lat) * len(lon)} points")
        print(f"Lat: {lat[0]:.1f} to {lat[-1]:.1f}, Lon: {lon[0]:.1f} to {lon[-1]:.1f}")
        print(f"Resolution: {lat[1] - lat[0]:.1f}°")
        print(f"Models: {', '.join(models)}")
        print(f"Zones: {len(ZONES)}")
        for name, bounds in ZONES.items():
            display = bounds["display"]
            n_lat = int((bounds["lat"][1] - bounds["lat"][0]) / 0.5) + 1
            n_lon = int((bounds["lon"][1] - bounds["lon"][0]) / 0.5) + 1
            print(f"  {name:<28} {display:<32} {n_lat}×{n_lon}={n_lat * n_lon} pts")
        return

    result = _run_analysis(
        models,
        use_cache=not args.no_cache,
        cache_dir=Path(args.cache_dir) if args.cache_dir else cache._DEFAULT_CACHE_DIR,
        t_threshold=args.threshold,
        te_threshold=args.te_threshold,
    )

    _print_analyze_table(result, models)

    if args.plot:
        plot_model = args.model or _DEFAULT_MODELS[0]
        plot_hour = args.hour if args.hour is not None else 0
        _plot_analysis(result, plot_model, plot_hour, args.output)


def _cmd_zones(args: argparse.Namespace) -> None:
    model = args.model or _DEFAULT_MODELS[0]
    result = _run_analysis([model])
    _print_zones(result, model, args.hour)


def _cmd_route(args: argparse.Namespace) -> None:
    if args.template:
        if args.template not in ROUTE_TEMPLATES:
            print(
                f"Unknown template '{args.template}'. Available: "
                + ", ".join(sorted(ROUTE_TEMPLATES)),
                file=sys.stderr,
            )
            sys.exit(1)
        zone_list = ROUTE_TEMPLATES[args.template]
        route_name = args.template
    else:
        print("Error: --template is required (--from/--to deferred to Phase 2)")
        sys.exit(1)

    result = _run_analysis(_DEFAULT_MODELS)
    _print_route(result, zone_list, route_name)


def _cmd_score(args: argparse.Namespace) -> None:
    """Score detection against expected zones from calibration dataset."""
    import yaml

    case_dir = Path(args.case)
    expected_path = case_dir / "expected.yaml"
    if not expected_path.exists():
        print(f"Error: {expected_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(expected_path) as f:
        expected_cases = yaml.safe_load(f)

    from weatherbrief.frontal.detect import compute_frontal_zones_dual, classify_front_type
    from weatherbrief.frontal.tracking import _compute_mean_gradient, _ANOMALY_THRESHOLD, _ABSOLUTE_FLOOR

    lat, lon = build_grid_coords()
    terrain_mask = build_terrain_mask(lat, lon)

    # Override anomaly/floor from args
    anomaly_threshold = args.anomaly
    absolute_floor = args.floor

    # Load raw data per model from calibration dir
    all_zone_names = set(ZONES.keys())

    for model_key in args.models:
        raw_path = case_dir / "raw" / f"{model_key}.json"
        if not raw_path.exists():
            print(f"  Skipping {model_key}: {raw_path} not found")
            continue

        import json
        raw = json.loads(raw_path.read_text())

        # Reshape all hours
        n_hours = len(list(raw.values())[0][0])
        all_fields: dict[int, dict] = {}
        for h in range(min(n_hours, 97)):
            fields = reshape_to_fields(raw, lat, lon, h, terrain_mask)
            if fields is not None:
                all_fields[h] = fields

        # Compute mean gradient for anomaly filtering
        mean_gradient = _compute_mean_gradient(
            all_fields, lat, lon, range(min(n_hours, 97)), terrain_mask,
        )

        print(f"\n{'='*72}")
        print(f"  {model_key.upper()} — threshold={args.threshold} θe={args.te_threshold} "
              f"anomaly={anomaly_threshold} floor={absolute_floor}")
        print(f"{'='*72}")

        total_hits = 0
        total_misses = 0
        total_false_alarms = 0
        total_correct_neg = 0
        type_correct = 0
        type_total = 0

        for case in expected_cases:
            hour_offset = case.get("hour_offset", 0)
            time_str = case["time"]
            expected_zones = case.get("zones", {})

            if hour_offset < 0:
                continue  # before model init, skip

            fields = all_fields.get(hour_offset)
            if fields is None:
                print(f"\n  {time_str} (T+{hour_offset}h): no data")
                continue

            # Run detection
            zones_result = compute_frontal_zones_dual(
                fields["T850"], fields["theta_e"], lat, lon,
                terrain_mask=terrain_mask,
                t_gradient_threshold=args.threshold,
                te_gradient_threshold=args.te_threshold,
            )
            anomaly = zones_result["gradient"] - mean_gradient
            anomaly_mask = (
                (anomaly > anomaly_threshold)
                & (zones_result["gradient"] > absolute_floor)
            )
            filtered_mask = zones_result["frontal_mask"] & anomaly_mask

            ft = classify_front_type(
                zones_result["dT_dx"], zones_result["dT_dy"],
                fields["u850"], fields["v850"],
                filtered_mask, detected_by=zones_result.get("detected_by"),
            )
            from weatherbrief.frontal.zones import find_fronts_in_regions
            regions = find_fronts_in_regions(
                filtered_mask, ft, zones_result["gradient"],
                zones_result["front_orientation"], lat, lon,
                terrain_mask=terrain_mask,
            )

            # Score
            hits = []
            misses = []
            false_alarms = []
            correct_neg = 0

            for zone_name in all_zone_names:
                expected_type = expected_zones.get(zone_name)
                detected = regions.get(zone_name, {}).get("present", False)
                detected_type = regions.get(zone_name, {}).get("type")

                if expected_type and detected:
                    hits.append(zone_name)
                    # Check type match (occluded matches either cold or warm)
                    if expected_type == "occluded" or expected_type == detected_type:
                        type_correct += 1
                    type_total += 1
                elif expected_type and not detected:
                    misses.append(zone_name)
                elif not expected_type and detected:
                    false_alarms.append(zone_name)
                else:
                    correct_neg += 1

            total_hits += len(hits)
            total_misses += len(misses)
            total_false_alarms += len(false_alarms)
            total_correct_neg += correct_neg

            # Print per-time results
            status = "✓" if not misses and not false_alarms else "~" if hits else "✗"
            print(f"\n  {status} {time_str} (T+{hour_offset}h):")
            if hits:
                for z in hits:
                    exp_t = expected_zones[z]
                    det_t = regions[z]["type"]
                    match = "✓" if exp_t == "occluded" or exp_t == det_t else "✗"
                    print(f"      HIT  {ZONES[z]['display']:<32} "
                          f"expected={exp_t:<10} detected={det_t} {match}")
            if misses:
                for z in misses:
                    print(f"      MISS {ZONES[z]['display']:<32} "
                          f"expected={expected_zones[z]}")
            if false_alarms:
                for z in false_alarms:
                    det_t = regions[z]["type"]
                    print(f"      FA   {ZONES[z]['display']:<32} "
                          f"detected={det_t} (not expected)")

        # Summary
        total_expected = total_hits + total_misses
        total_detected = total_hits + total_false_alarms
        pod = total_hits / total_expected if total_expected > 0 else 0
        far = total_false_alarms / total_detected if total_detected > 0 else 0
        csi = total_hits / (total_hits + total_misses + total_false_alarms) if (total_hits + total_misses + total_false_alarms) > 0 else 0
        type_acc = type_correct / type_total if type_total > 0 else 0

        print(f"\n  {'─'*60}")
        print(f"  SUMMARY:")
        print(f"    Hits: {total_hits}  Misses: {total_misses}  "
              f"False alarms: {total_false_alarms}  Correct negatives: {total_correct_neg}")
        print(f"    POD (probability of detection): {pod:.0%}")
        print(f"    FAR (false alarm ratio):        {far:.0%}")
        print(f"    CSI (critical success index):   {csi:.0%}")
        print(f"    Type accuracy (hits only):      {type_acc:.0%} ({type_correct}/{type_total})")


def _draw_zone_map(
    ax, regions: dict, title: str,
    lat: np.ndarray, lon: np.ndarray,
    gradient_field: np.ndarray | None = None,
    score_text: str | None = None,
) -> None:
    """Draw zone rectangles on a cartopy axes. Shared by validate columns."""
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    import matplotlib.patches as mpatches

    COLORS = {"cold": "#2060C0", "warm": "#C03030", "occluded": "#7030A0",
              "indeterminate": "#808080"}

    ax.add_feature(cfeature.LAND, facecolor="#F0EDE4", zorder=0)
    ax.add_feature(cfeature.OCEAN, facecolor="#D8E8F0", zorder=0)
    ax.add_feature(cfeature.COASTLINE, linewidth=0.6, color="#666666")
    ax.add_feature(cfeature.BORDERS, linewidth=0.4, linestyle=":", color="#999999")
    ax.set_extent([-22, 30, 34, 62], crs=ccrs.PlateCarree())

    if gradient_field is not None:
        lon_grid, lat_grid = np.meshgrid(lon, lat)
        ax.pcolormesh(
            lon_grid, lat_grid, gradient_field,
            cmap="Greys", vmin=0, vmax=5.0, alpha=0.15,
            transform=ccrs.PlateCarree(), zorder=1,
        )

    annotations = []
    for zone_name, bounds in ZONES.items():
        r = regions.get(zone_name, {})
        lat0, lat1 = bounds["lat"]
        lon0, lon1 = bounds["lon"]
        present = r.get("present", False)
        ftype = r.get("type")

        if present and ftype:
            coverage = r.get("coverage_fraction", 0.3)
            color = COLORS.get(ftype, "#808080")
            alpha = min(0.15 + coverage * 1.5, 0.6)

            rect = mpatches.Rectangle(
                (lon0, lat0), lon1 - lon0, lat1 - lat0,
                facecolor=color, alpha=alpha, edgecolor=color,
                linewidth=1.5, transform=ccrs.PlateCarree(), zorder=2,
            )
            ax.add_patch(rect)

            clat, clon = (lat0 + lat1) / 2, (lon0 + lon1) / 2
            label = ftype[0].upper()
            if ftype == "occluded":
                label = "O"
            ax.text(
                clon, clat, label,
                ha="center", va="center", fontsize=12,
                fontweight="bold", color="white",
                transform=ccrs.PlateCarree(), zorder=3,
                bbox=dict(boxstyle="round,pad=0.2", facecolor=color, alpha=0.8),
            )
            intensity = r.get("intensity")
            orient = r.get("orientation", "")
            if intensity:
                annotations.append(
                    f"{bounds['display']}: {ftype} {intensity:.1f}K {orient}"
                )
            else:
                annotations.append(f"{bounds['display']}: {ftype}")
        else:
            rect = mpatches.Rectangle(
                (lon0, lat0), lon1 - lon0, lat1 - lat0,
                facecolor="none", edgecolor="#CCCCCC",
                linewidth=0.5, linestyle="--",
                transform=ccrs.PlateCarree(), zorder=1,
            )
            ax.add_patch(rect)

    ax.set_title(title, fontsize=12, fontweight="bold")

    # Annotation text below map
    bottom_text = "\n".join(annotations) if annotations else "No frontal activity"
    if score_text:
        bottom_text = score_text + "\n" + bottom_text
    ax.text(
        0.02, -0.02, bottom_text, transform=ax.transAxes,
        fontsize=7, verticalalignment="top", fontfamily="monospace",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.9),
    )


def _score_one_time(
    expected_zones: dict[str, str],
    detected_regions: dict,
) -> dict:
    """Score detection vs expected for a single time step. Returns stats dict."""
    hits, misses, false_alarms, correct_neg = 0, 0, 0, 0
    type_correct, type_total = 0, 0

    for zone_name in ZONES:
        exp_type = expected_zones.get(zone_name)
        det = detected_regions.get(zone_name, {})
        det_present = det.get("present", False)
        det_type = det.get("type")

        if exp_type and det_present:
            hits += 1
            type_total += 1
            if exp_type == "occluded" or exp_type == det_type:
                type_correct += 1
        elif exp_type and not det_present:
            misses += 1
        elif not exp_type and det_present:
            false_alarms += 1
        else:
            correct_neg += 1

    total = hits + misses + false_alarms
    pod = hits / (hits + misses) if (hits + misses) > 0 else 1.0
    far = false_alarms / (hits + false_alarms) if (hits + false_alarms) > 0 else 0.0
    csi = hits / total if total > 0 else 1.0

    return {
        "hits": hits, "misses": misses, "false_alarms": false_alarms,
        "pod": pod, "far": far, "csi": csi,
        "type_acc": type_correct / type_total if type_total > 0 else 0.0,
    }


def _cmd_validate(args: argparse.Namespace) -> None:
    """Generate 4-column comparison: MF | Reference zones | ECMWF | GFS."""
    if len(args.charts) != len(args.times):
        print("Error: --charts and --times must have the same number of entries")
        sys.exit(1)

    try:
        import cartopy.crs as ccrs
        import matplotlib.image as mpimg
        import matplotlib.patches as mpatches
        import matplotlib.pyplot as plt
    except ImportError:
        print(
            "Error: cartopy is required. Install with: pip install -e '.[frontal-dev]'",
            file=sys.stderr,
        )
        return

    from weatherbrief.frontal.tracking import (
        _ANOMALY_THRESHOLD,
        _ABSOLUTE_FLOOR,
        _compute_mean_gradient,
    )
    from weatherbrief.frontal.detect import compute_frontal_zones_dual, classify_front_type
    from weatherbrief.frontal.zones import find_fronts_in_regions

    # Load expected zones if provided
    expected_by_time: dict[str, dict] = {}
    if args.expected:
        import yaml

        with open(args.expected) as f:
            for case in yaml.safe_load(f):
                expected_by_time[case["time"]] = case.get("zones", {})

    # Parse times and compute hour offsets
    chart_times = []
    for t in args.times:
        parts = t.replace("Z", "").strip().split()
        day_month = parts[0]
        hour_utc = int(parts[1]) if len(parts) > 1 else 0
        day, month = day_month.split("/")
        chart_times.append((int(day), int(month), hour_utc))

    # Run analysis for ECMWF and GFS
    models_to_run = ["ecmwf", "gfs"]
    result = _run_analysis(
        models_to_run,
        use_cache=not args.no_cache,
        cache_dir=Path(args.cache_dir) if args.cache_dir else cache._DEFAULT_CACHE_DIR,
    )

    lat = result["lat"]
    lon = result["lon"]
    terrain_mask = result["terrain_mask"]

    # Compute mean gradient per model
    model_mean_gradients = {}
    for model_key in models_to_run:
        model_mean_gradients[model_key] = _compute_mean_gradient(
            result["model_fields"][model_key], lat, lon,
            range(len(result["timestamps"].get(model_key, []))),
            terrain_mask,
        )

    # Map chart times to hour offsets
    ecmwf_init = result["model_init_times"].get("ecmwf", 0)
    if ecmwf_init:
        from datetime import datetime, timezone as tz

        init_dt = datetime.fromtimestamp(ecmwf_init, tz=tz.utc)
        chart_hours = []
        for day, month, hour_utc in chart_times:
            chart_dt = init_dt.replace(month=month, day=day, hour=hour_utc)
            delta_h = int((chart_dt - init_dt).total_seconds() / 3600)
            chart_hours.append(delta_h)
    else:
        chart_hours = list(range(0, len(args.charts) * 12, 12))

    n_rows = len(args.charts)
    n_cols = 4  # MF | Reference | ECMWF | GFS
    fig = plt.figure(figsize=(38, 7.5 * n_rows))

    for row, (chart_path, time_str, hour_offset) in enumerate(
        zip(args.charts, args.times, chart_hours)
    ):
        expected_zones = expected_by_time.get(time_str, {})

        # Column 1: Meteo-France chart
        ax_mf = fig.add_subplot(n_rows, n_cols, row * n_cols + 1)
        mf_img = mpimg.imread(chart_path)
        ax_mf.imshow(mf_img)
        ax_mf.set_title(f"Météo-France — {time_str}", fontsize=12, fontweight="bold")
        ax_mf.axis("off")

        # Column 2: Reference zones from expected.yaml
        ax_ref = fig.add_subplot(
            n_rows, n_cols, row * n_cols + 2,
            projection=ccrs.PlateCarree(),
        )
        ref_regions = {}
        for zone_name in ZONES:
            exp_type = expected_zones.get(zone_name)
            if exp_type:
                ref_regions[zone_name] = {
                    "present": True, "type": exp_type,
                    "coverage_fraction": 0.3,
                }
            else:
                ref_regions[zone_name] = {"present": False}

        _draw_zone_map(ax_ref, ref_regions, f"Expected — {time_str}", lat, lon)

        # Columns 3-4: ECMWF and GFS detection with scores
        for col_idx, model_key in enumerate(models_to_run):
            ax = fig.add_subplot(
                n_rows, n_cols, row * n_cols + 3 + col_idx,
                projection=ccrs.PlateCarree(),
            )

            fields = result["model_fields"].get(model_key, {}).get(hour_offset)
            if fields is None:
                _draw_zone_map(
                    ax, {}, f"{model_key.upper()} — {time_str} (no data)", lat, lon,
                )
                continue

            zones_result = compute_frontal_zones_dual(
                fields["T850"], fields["theta_e"], lat, lon,
                terrain_mask=terrain_mask,
            )
            mean_grad = model_mean_gradients[model_key]
            anomaly = zones_result["gradient"] - mean_grad
            anomaly_mask = (
                (anomaly > _ANOMALY_THRESHOLD)
                & (zones_result["gradient"] > _ABSOLUTE_FLOOR)
            )
            filtered_mask = zones_result["frontal_mask"] & anomaly_mask

            ft = classify_front_type(
                zones_result["dT_dx"], zones_result["dT_dy"],
                fields["u850"], fields["v850"],
                filtered_mask, detected_by=zones_result.get("detected_by"),
            )
            regions = find_fronts_in_regions(
                filtered_mask, ft, zones_result["gradient"],
                zones_result["front_orientation"], lat, lon,
                terrain_mask=terrain_mask,
            )

            # Compute per-tile score
            score_text = ""
            if expected_zones:
                scores = _score_one_time(expected_zones, regions)
                score_text = (
                    f"POD={scores['pod']:.0%} FAR={scores['far']:.0%} "
                    f"CSI={scores['csi']:.0%}  "
                    f"H={scores['hits']} M={scores['misses']} FA={scores['false_alarms']}"
                )

            init_time = result["model_init_times"].get(model_key, 0)
            init_str = (
                datetime.fromtimestamp(init_time, tz=tz.utc).strftime("%HZ")
                if init_time else "?"
            )

            _draw_zone_map(
                ax, regions,
                f"{model_key.upper()} {init_str} — {time_str} (T+{hour_offset}h)",
                lat, lon,
                gradient_field=zones_result["gradient"],
                score_text=score_text,
            )

    # Legend
    COLORS = {"cold": "#2060C0", "warm": "#C03030", "occluded": "#7030A0"}
    legend_elements = [
        mpatches.Patch(facecolor=COLORS["cold"], alpha=0.5, label="Cold front"),
        mpatches.Patch(facecolor=COLORS["warm"], alpha=0.5, label="Warm front"),
        mpatches.Patch(facecolor=COLORS["occluded"], alpha=0.5, label="Occluded"),
    ]
    fig.legend(
        handles=legend_elements, loc="lower center", ncol=3,
        fontsize=12, frameon=True, fancybox=True,
    )

    fig.suptitle(
        "Frontal Detection Validation: Météo-France | Expected | ECMWF | GFS",
        fontsize=16, fontweight="bold", y=0.995,
    )
    plt.tight_layout(rect=[0, 0.02, 1, 0.99], h_pad=3)

    fig.savefig(args.output, dpi=140, bbox_inches="tight")
    print(f"Saved to {args.output}")


def _cmd_clear_cache(args: argparse.Namespace) -> None:
    cache_dir = Path(args.cache_dir) if args.cache_dir else cache._DEFAULT_CACHE_DIR
    count = cache.clear_cache(cache_dir)
    print(f"Cleared {count} cached file(s).")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m weatherbrief.frontal.cli",
        description="Frontal detection CLI for development and validation",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # analyze
    p_analyze = sub.add_parser("analyze", help="Run frontal analysis")
    p_analyze.add_argument("--model", choices=["ecmwf", "gfs", "icon"])
    p_analyze.add_argument("--hour", type=int, help="Specific hour for --plot")
    p_analyze.add_argument(
        "--threshold", type=float, default=2.0,
        help="T850 gradient threshold (K/100km, default: 2.0)",
    )
    p_analyze.add_argument(
        "--te-threshold", type=float, default=4.0,
        help="θe gradient threshold (K/100km, default: 4.0)",
    )
    p_analyze.add_argument("--no-cache", action="store_true")
    p_analyze.add_argument("--cache-dir")
    p_analyze.add_argument("--plot", action="store_true", help="Generate map plot")
    p_analyze.add_argument("--output", help="Save plot to file")
    p_analyze.add_argument(
        "--dry-run", action="store_true",
        help="Show grid/zone info without fetching",
    )

    # zones
    p_zones = sub.add_parser("zones", help="Show zones with frontal activity")
    p_zones.add_argument("--model", choices=["ecmwf", "gfs", "icon"])
    p_zones.add_argument("--hour", type=int, default=0)

    # route
    p_route = sub.add_parser("route", help="Show route frontal table")
    p_route.add_argument("--template", help="Route template name")

    # score
    p_score = sub.add_parser(
        "score",
        help="Score detection against expected zones from calibration data",
    )
    p_score.add_argument(
        "--case", required=True,
        help="Calibration case dir (e.g. data/calibration/2026-04-16_12Z)",
    )
    p_score.add_argument(
        "--models", nargs="+", default=["ecmwf", "gfs"],
        help="Models to score (default: ecmwf gfs)",
    )
    p_score.add_argument(
        "--threshold", type=float, default=2.0,
        help="T850 gradient threshold (K/100km)",
    )
    p_score.add_argument(
        "--te-threshold", type=float, default=4.0,
        help="θe gradient threshold (K/100km)",
    )
    p_score.add_argument(
        "--anomaly", type=float, default=1.0,
        help="Anomaly threshold (K/100km)",
    )
    p_score.add_argument(
        "--floor", type=float, default=2.0,
        help="Absolute gradient floor (K/100km)",
    )

    # validate
    p_validate = sub.add_parser(
        "validate",
        help="Compare detection against Meteo-France carte des fronts",
    )
    p_validate.add_argument(
        "--charts", nargs="+", required=True,
        help="Meteo-France chart image files (ordered by time)",
    )
    p_validate.add_argument(
        "--times", nargs="+", required=True,
        help="Chart valid times, e.g. '17/04 00Z' '17/04 12Z' (same order as --charts)",
    )
    p_validate.add_argument(
        "--output", default="data/frontal_validation.png",
        help="Output image path (default: data/frontal_validation.png)",
    )
    p_validate.add_argument(
        "--expected",
        help="Path to expected.yaml for reference zones and per-tile scoring",
    )
    p_validate.add_argument("--no-cache", action="store_true")
    p_validate.add_argument("--cache-dir")

    # clear-cache
    p_clear = sub.add_parser("clear-cache", help="Delete cached grid data")
    p_clear.add_argument("--cache-dir")

    return parser


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    commands = {
        "analyze": _cmd_analyze,
        "zones": _cmd_zones,
        "route": _cmd_route,
        "score": _cmd_score,
        "validate": _cmd_validate,
        "clear-cache": _cmd_clear_cache,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
