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
        resolution = float(lat[1] - lat[0])
        print(f"Resolution: {resolution:.2f}°")
        print(f"Models: {', '.join(models)}")
        print(f"Zones: {len(ZONES)}")
        for name, bounds in ZONES.items():
            display = bounds["display"]
            n_lat = int((bounds["lat"][1] - bounds["lat"][0]) / resolution) + 1
            n_lon = int((bounds["lon"][1] - bounds["lon"][0]) / resolution) + 1
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

    # Download latest DWD reference charts alongside analysis
    print()
    _cmd_charts(argparse.Namespace(chart=None, output_dir=None))

    if args.plot:
        plot_model = args.model or _DEFAULT_MODELS[0]
        plot_hour = args.hour if args.hour is not None else 0
        _plot_analysis(result, plot_model, plot_hour, args.output)


def _cmd_zones(args: argparse.Namespace) -> None:
    model = args.model or _DEFAULT_MODELS[0]
    result = _run_analysis(
        [model],
        t_threshold=args.threshold,
        te_threshold=args.te_threshold,
    )
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

    result = _run_analysis(
        _DEFAULT_MODELS,
        t_threshold=args.threshold,
        te_threshold=args.te_threshold,
    )
    _print_route(result, zone_list, route_name)


def _detect_one_hour(
    fields: dict,
    lat: np.ndarray,
    lon: np.ndarray,
    terrain_mask: np.ndarray | None,
    mean_t_gradient: np.ndarray,
    mean_te_gradient: np.ndarray,
    t_gradient_threshold: float = 2.0,
    te_gradient_threshold: float = 4.0,
    anomaly_threshold: float = 1.0,
    absolute_floor: float = 2.0,
) -> tuple[np.ndarray, np.ndarray, dict, dict]:
    """Run the full detection pipeline for one forecast hour.

    Returns (filtered_mask, front_type_grid, regions, zones_result).
    """
    from weatherbrief.frontal.detect import classify_front_type, compute_frontal_zones_dual
    from weatherbrief.frontal.tracking import apply_anomaly_filter
    from weatherbrief.frontal.zones import find_fronts_in_regions

    zones_result = compute_frontal_zones_dual(
        fields["T850"], fields["theta_e"], lat, lon,
        terrain_mask=terrain_mask,
        t_gradient_threshold=t_gradient_threshold,
        te_gradient_threshold=te_gradient_threshold,
    )
    filtered_mask = apply_anomaly_filter(
        zones_result, mean_t_gradient, mean_te_gradient,
        anomaly_threshold, absolute_floor,
    )
    front_type_grid = classify_front_type(
        zones_result["dT_dx"], zones_result["dT_dy"],
        fields["u850"], fields["v850"],
        filtered_mask, detected_by=zones_result.get("detected_by"),
    )
    regions = find_fronts_in_regions(
        filtered_mask, front_type_grid, zones_result["gradient"],
        zones_result["front_orientation"], lat, lon,
        terrain_mask=terrain_mask,
    )
    return filtered_mask, front_type_grid, regions, zones_result


def _cmd_score(args: argparse.Namespace) -> None:
    """Score detection against expected zones from calibration dataset."""
    import yaml

    from weatherbrief.frontal.case import load_case

    case_dir = Path(args.case)
    expected_path = case_dir / "expected.yaml"
    if not expected_path.exists():
        print(f"Error: {expected_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(expected_path) as f:
        expected_cases = yaml.safe_load(f)

    case = load_case(case_dir)
    lat, lon = case.lat, case.lon
    terrain_mask = build_terrain_mask(lat, lon)

    # If the user didn't pass --models, default to what's in the case
    models_to_score = args.models if args.models else case.models

    for model_key in models_to_score:
        if model_key not in case.models:
            print(f"  Skipping {model_key}: not present in case (has {case.models})")
            continue

        all_fields = case.fields_all_hours(model_key)
        if not all_fields:
            print(f"  Skipping {model_key}: no usable hours in raw data")
            continue

        # Build full timeseries (includes anomaly filtering + persistence filter)
        zone_ts = build_zone_timeseries(
            all_fields, lat, lon,
            hours=sorted(all_fields.keys()),
            terrain_mask=terrain_mask,
            t_gradient_threshold=args.threshold,
            te_gradient_threshold=args.te_threshold,
            anomaly_threshold=args.anomaly,
            absolute_floor=args.floor,
        )

        # Index timeseries by hour for quick lookup
        ts_by_hour: dict[str, dict[int, dict]] = {}
        for zone_name, entries in zone_ts.items():
            ts_by_hour[zone_name] = {e["hour"]: e for e in entries}

        print(f"\n{'='*72}")
        print(f"  {model_key.upper()} — threshold={args.threshold} θe={args.te_threshold} "
              f"anomaly={args.anomaly} floor={args.floor}")
        print(f"{'='*72}")

        total_hits = 0
        total_misses = 0
        total_false_alarms = 0
        total_correct_neg = 0
        type_correct = 0
        type_total = 0

        for expected_entry in expected_cases:
            hour_offset = expected_entry.get("hour_offset", 0)
            time_str = expected_entry["time"]
            expected_zones = expected_entry.get("zones", {})

            if hour_offset < 0:
                continue  # before model init, skip

            if hour_offset not in all_fields:
                print(f"\n  {time_str} (T+{hour_offset}h): no data")
                continue

            # Build regions dict from timeseries at this hour
            regions = {}
            for zone_name in ZONES:
                entry = ts_by_hour.get(zone_name, {}).get(hour_offset)
                if entry and entry["present"]:
                    regions[zone_name] = {
                        "present": True,
                        "type": entry.get("type"),
                        "intensity": entry.get("intensity"),
                        "orientation": entry.get("orientation"),
                    }
                else:
                    regions[zone_name] = {"present": False}

            scores = _score_one_time(expected_zones, regions)

            total_hits += scores["hits"]
            total_misses += scores["misses"]
            total_false_alarms += scores["false_alarms"]
            total_correct_neg += len(ZONES) - scores["hits"] - scores["misses"] - scores["false_alarms"]
            if scores["hits"] > 0:
                type_correct += round(scores["type_acc"] * scores["hits"])
                type_total += scores["hits"]

            # Print per-time results
            status = "✓" if not scores["misses"] and not scores["false_alarms"] else "~" if scores["hits"] else "✗"
            print(f"\n  {status} {time_str} (T+{hour_offset}h):")
            for zone_name in ZONES:
                exp_type = expected_zones.get(zone_name)
                det = regions.get(zone_name, {})
                det_present = det.get("present", False)
                det_type = det.get("type")

                if exp_type and det_present:
                    match = "✓" if exp_type == "occluded" or exp_type == det_type else "✗"
                    print(f"      HIT  {ZONES[zone_name]['display']:<32} "
                          f"expected={exp_type:<10} detected={det_type} {match}")
                elif exp_type and not det_present:
                    print(f"      MISS {ZONES[zone_name]['display']:<32} "
                          f"expected={exp_type}")
                elif not exp_type and det_present:
                    print(f"      FA   {ZONES[zone_name]['display']:<32} "
                          f"detected={det_type} (not expected)")

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

    from weatherbrief.frontal.tracking import _compute_mean_gradient

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
        t_threshold=args.threshold,
        te_threshold=args.te_threshold,
    )

    lat = result["lat"]
    lon = result["lon"]
    terrain_mask = result["terrain_mask"]

    # Compute mean gradients per model (both T and θe for per-channel anomaly)
    model_mean_t_gradients = {}
    model_mean_te_gradients = {}
    for model_key in models_to_run:
        hours_range = range(len(result["timestamps"].get(model_key, [])))
        model_mean_t_gradients[model_key] = _compute_mean_gradient(
            result["model_fields"][model_key], lat, lon,
            hours_range, terrain_mask, field_name="T850",
        )
        model_mean_te_gradients[model_key] = _compute_mean_gradient(
            result["model_fields"][model_key], lat, lon,
            hours_range, terrain_mask, field_name="theta_e",
        )

    # Map chart times to hour offsets
    ecmwf_init = result["model_init_times"].get("ecmwf", 0)
    if ecmwf_init:
        init_dt = datetime.fromtimestamp(ecmwf_init, tz=timezone.utc)
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

            _, _, regions, zones_result = _detect_one_hour(
                fields, lat, lon, terrain_mask,
                model_mean_t_gradients[model_key],
                model_mean_te_gradients[model_key],
                t_gradient_threshold=args.threshold,
                te_gradient_threshold=args.te_threshold,
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
                datetime.fromtimestamp(init_time, tz=timezone.utc).strftime("%HZ")
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


def _cmd_diagnose(args: argparse.Namespace) -> None:
    """Deep diagnostic of detection pipeline for a specific zone/hour/model."""
    from weatherbrief.frontal.detect import (
        classify_front_type,
        compute_frontal_zones,
        compute_frontal_zones_dual,
    )
    from weatherbrief.frontal.tracking import _compute_mean_gradient
    from weatherbrief.frontal.zones import (
        _MIN_FRONTAL_FRACTION,
        _MIN_FRONTAL_POINTS,
        find_fronts_in_regions,
    )

    case_dir = Path(args.case)
    model_key = args.model
    hour = args.hour
    zone_name = args.zone

    if zone_name not in ZONES:
        print(f"Unknown zone '{zone_name}'. Available:", file=sys.stderr)
        for z in sorted(ZONES):
            print(f"  {z:<28} {ZONES[z]['display']}", file=sys.stderr)
        sys.exit(1)

    zone_bounds = ZONES[zone_name]
    print(f"\n{'='*72}")
    print(f"  DIAGNOSE: {model_key.upper()} T+{hour}h — {zone_bounds['display']}")
    print(f"  Zone bounds: lat {zone_bounds['lat']}, lon {zone_bounds['lon']}")
    print(f"  Thresholds: T={args.threshold} θe={args.te_threshold} "
          f"anomaly={args.anomaly} floor={args.floor}")
    print(f"{'='*72}")

    # Load raw data via unified Case
    from weatherbrief.frontal.case import load_case
    case = load_case(case_dir)
    if model_key not in case.models:
        print(
            f"Error: model '{model_key}' not in case (available: {case.models})",
            file=sys.stderr,
        )
        sys.exit(1)

    lat, lon = case.lat, case.lon
    terrain_mask = build_terrain_mask(lat, lon)

    all_fields = case.fields_all_hours(model_key)
    if hour not in all_fields:
        avail = sorted(all_fields.keys())
        print(f"Error: no data at hour {hour} (available: {avail[:10]}{'...' if len(avail) > 10 else ''})")
        sys.exit(1)

    fields = all_fields[hour]

    # Zone mask
    lat_grid, lon_grid = np.meshgrid(lat, lon, indexing="ij")
    zone_mask = (
        (lat_grid >= zone_bounds["lat"][0])
        & (lat_grid <= zone_bounds["lat"][1])
        & (lon_grid >= zone_bounds["lon"][0])
        & (lon_grid <= zone_bounds["lon"][1])
    )
    valid_zone = zone_mask & terrain_mask if terrain_mask is not None else zone_mask
    n_zone = int(zone_mask.sum())
    n_valid = int(valid_zone.sum())

    # ── Step 1: Raw fields ──
    print(f"\n── Raw fields in zone ({n_valid} valid / {n_zone} total points) ──")
    for name in ("T850", "Td850", "theta_e", "u850", "v850"):
        vals = fields[name][valid_zone]
        print(f"  {name:<10} min={vals.min():7.1f}  mean={vals.mean():7.1f}  "
              f"max={vals.max():7.1f}  std={vals.std():5.1f}")

    # ── Step 2: T850 gradient ──
    t_result = compute_frontal_zones(
        fields["T850"], lat, lon,
        terrain_mask=terrain_mask,
        gradient_threshold=args.threshold,
    )
    t_grad_zone = t_result["gradient"][valid_zone]
    t_mask_zone = t_result["frontal_mask"][valid_zone]
    n_t_frontal = int(t_mask_zone.sum())

    print(f"\n── T850 gradient (threshold={args.threshold} K/100km) ──")
    print(f"  Gradient    min={t_grad_zone.min():5.2f}  mean={t_grad_zone.mean():5.2f}  "
          f"max={t_grad_zone.max():5.2f}  std={t_grad_zone.std():5.2f}")
    print(f"  Points > threshold: {n_t_frontal} / {n_valid} "
          f"({n_t_frontal/n_valid:.1%})")

    # ── Step 3: θe gradient ──
    te_result = compute_frontal_zones(
        fields["theta_e"], lat, lon,
        terrain_mask=terrain_mask,
        gradient_threshold=args.te_threshold,
    )
    te_grad_zone = te_result["gradient"][valid_zone]
    te_mask_zone = te_result["frontal_mask"][valid_zone]
    n_te_frontal = int(te_mask_zone.sum())

    print(f"\n── θe gradient (threshold={args.te_threshold} K/100km) ──")
    print(f"  Gradient    min={te_grad_zone.min():5.2f}  mean={te_grad_zone.mean():5.2f}  "
          f"max={te_grad_zone.max():5.2f}  std={te_grad_zone.std():5.2f}")
    print(f"  Points > threshold: {n_te_frontal} / {n_valid} "
          f"({n_te_frontal/n_valid:.1%})")

    # ── Step 4: Dual detection (union) ──
    dual_result = compute_frontal_zones_dual(
        fields["T850"], fields["theta_e"], lat, lon,
        terrain_mask=terrain_mask,
        t_gradient_threshold=args.threshold,
        te_gradient_threshold=args.te_threshold,
    )
    combined_zone = dual_result["frontal_mask"][valid_zone]
    detected_by_zone = dual_result["detected_by"][valid_zone]
    n_combined = int(combined_zone.sum())
    n_t_only = int(((detected_by_zone == 1) & combined_zone).sum())
    n_te_only = int(((detected_by_zone == 2) & combined_zone).sum())
    n_both = int(((detected_by_zone == 3) & combined_zone).sum())

    print(f"\n── Dual detection (union) ──")
    print(f"  Combined:   {n_combined} / {n_valid} ({n_combined/n_valid:.1%})")
    print(f"  T only:     {n_t_only}   θe only: {n_te_only}   Both: {n_both}")

    # ── Step 5: Background mean gradients (per-channel) ──
    # Use the case's actual available hours rather than assuming a
    # contiguous 0..96 range (ERA5 cases have 6-hourly, not hourly, data).
    hours_range = sorted(all_fields.keys())
    mean_t_gradient = _compute_mean_gradient(
        all_fields, lat, lon, hours_range, terrain_mask, field_name="T850",
    )
    mean_te_gradient = _compute_mean_gradient(
        all_fields, lat, lon, hours_range, terrain_mask, field_name="theta_e",
    )
    mean_t_zone = mean_t_gradient[valid_zone]
    mean_te_zone = mean_te_gradient[valid_zone]

    print(f"\n── Background mean gradient (72h) ──")
    print(f"  T850        min={mean_t_zone.min():5.2f}  mean={mean_t_zone.mean():5.2f}  "
          f"max={mean_t_zone.max():5.2f}")
    print(f"  θe          min={mean_te_zone.min():5.2f}  mean={mean_te_zone.mean():5.2f}  "
          f"max={mean_te_zone.max():5.2f}")

    # ── Step 6: Per-channel anomaly filtering ──
    t_anomaly = dual_result["gradient"] - mean_t_gradient
    te_anomaly = dual_result["te_gradient"] - mean_te_gradient
    t_anomaly_zone = t_anomaly[valid_zone]
    te_anomaly_zone = te_anomaly[valid_zone]
    raw_t_grad_zone = dual_result["gradient"][valid_zone]
    raw_te_grad_zone = dual_result["te_gradient"][valid_zone]

    te_anom_thresh = args.anomaly * 2.0
    te_floor_val = args.floor * 2.0
    t_anom_pass = t_anomaly_zone > args.anomaly
    t_floor_pass = raw_t_grad_zone > args.floor
    te_anom_pass = te_anomaly_zone > te_anom_thresh
    te_floor_pass = raw_te_grad_zone > te_floor_val

    print(f"\n── Per-channel anomaly ──")
    print(f"  T850  (anomaly>{args.anomaly}, floor>{args.floor})")
    print(f"        anomaly min={t_anomaly_zone.min():+5.2f}  "
          f"mean={t_anomaly_zone.mean():+5.2f}  max={t_anomaly_zone.max():+5.2f}")
    print(f"        pass anomaly: {int(t_anom_pass.sum()):3d}  "
          f"pass floor: {int(t_floor_pass.sum()):3d}  "
          f"pass both: {int((t_anom_pass & t_floor_pass).sum()):3d}")
    print(f"  θe    (anomaly>{te_anom_thresh}, floor>{te_floor_val})")
    print(f"        anomaly min={te_anomaly_zone.min():+5.2f}  "
          f"mean={te_anomaly_zone.mean():+5.2f}  max={te_anomaly_zone.max():+5.2f}")
    print(f"        pass anomaly: {int(te_anom_pass.sum()):3d}  "
          f"pass floor: {int(te_floor_pass.sum()):3d}  "
          f"pass both: {int((te_anom_pass & te_floor_pass).sum()):3d}")

    # ── Step 7: Filtered mask (dual ∩ per-channel anomaly) ──
    from weatherbrief.frontal.tracking import apply_anomaly_filter
    filtered_mask = apply_anomaly_filter(
        dual_result, mean_t_gradient, mean_te_gradient,
        args.anomaly, args.floor,
    )
    filtered_zone = filtered_mask[valid_zone]
    n_filtered = int(filtered_zone.sum())

    print(f"\n── Filtered mask (dual ∩ anomaly) ──")
    print(f"  Frontal points surviving: {n_filtered} / {n_valid} "
          f"({n_filtered/n_valid:.1%})")
    print(f"  Lost to anomaly filter: {n_combined - n_filtered} "
          f"(had {n_combined} from dual)")

    # ── Step 8: Zone fraction check ──
    frac = n_filtered / n_valid if n_valid > 0 else 0
    passes_fraction = frac >= _MIN_FRONTAL_FRACTION
    passes_points = n_filtered >= _MIN_FRONTAL_POINTS
    detected = passes_fraction and passes_points

    print(f"\n── Zone threshold check ──")
    print(f"  Fraction:   {frac:.3f} (need ≥ {_MIN_FRONTAL_FRACTION})  "
          f"{'PASS' if passes_fraction else 'FAIL'}")
    print(f"  Points:     {n_filtered} (need ≥ {_MIN_FRONTAL_POINTS})     "
          f"{'PASS' if passes_points else 'FAIL'}")
    print(f"  ⇒ DETECTED: {'YES' if detected else 'NO'}")

    # ── Step 9: Classification (if detected) ──
    if detected:
        front_type_grid = classify_front_type(
            dual_result["dT_dx"], dual_result["dT_dy"],
            fields["u850"], fields["v850"],
            filtered_mask, detected_by=dual_result.get("detected_by"),
        )
        types_in_zone = front_type_grid[filtered_mask & zone_mask]
        n_cold = int((types_in_zone == 1).sum())
        n_warm = int((types_in_zone == 2).sum())
        n_indet = int((types_in_zone == 3).sum())
        dominant = "cold" if n_cold >= n_warm and n_cold >= n_indet else (
            "warm" if n_warm >= n_indet else "indeterminate"
        )

        # Cross-front wind detail
        grad_mag = np.sqrt(dual_result["dT_dx"]**2 + dual_result["dT_dy"]**2)
        grad_norm = np.where(grad_mag > 1e-10, grad_mag, 1e-10)
        cross_front = (fields["u850"] * dual_result["dT_dx"]
                       + fields["v850"] * dual_result["dT_dy"]) / grad_norm
        cf_zone = cross_front[filtered_mask & zone_mask]

        print(f"\n── Front classification ──")
        print(f"  Cold: {n_cold}  Warm: {n_warm}  Indeterminate: {n_indet}")
        print(f"  ⇒ Dominant type: {dominant}")
        print(f"  Cross-front wind  min={cf_zone.min():5.1f}  mean={cf_zone.mean():5.1f}  "
              f"max={cf_zone.max():5.1f} km/h")
    else:
        print(f"\n── Front classification ──")
        print(f"  (skipped — not detected)")

    # ── Step 10: What killed detection? (if not detected) ──
    if not detected and n_combined > 0:
        print(f"\n── Diagnosis: why not detected? ──")
        if n_combined > 0 and n_filtered == 0:
            print(f"  Dual detection found {n_combined} points, but ALL were removed")
            print(f"  by per-channel anomaly filtering.")
            print(f"  T850: bg={mean_t_zone.mean():.2f}  current={raw_t_grad_zone.mean():.2f}  "
                  f"max_anomaly={t_anomaly_zone.max():+.2f} (need >{args.anomaly})")
            print(f"  θe:   bg={mean_te_zone.mean():.2f}  current={raw_te_grad_zone.mean():.2f}  "
                  f"max_anomaly={te_anomaly_zone.max():+.2f} (need >{args.anomaly * 2.0})")
        elif n_filtered > 0 and not passes_fraction:
            print(f"  {n_filtered} points survived all filters, but fraction "
                  f"{frac:.3f} < {_MIN_FRONTAL_FRACTION}")
            print(f"  The front is too narrow or at the zone boundary.")
        elif n_filtered > 0 and not passes_points:
            print(f"  {n_filtered} points survived, but fewer than {_MIN_FRONTAL_POINTS}")
    elif not detected and n_combined == 0:
        print(f"\n── Diagnosis: why not detected? ──")
        print(f"  No points exceeded EITHER gradient threshold.")
        print(f"  Max T850 gradient in zone:  {t_grad_zone.max():.2f} K/100km "
              f"(need > {args.threshold})")
        print(f"  Max θe gradient in zone:    {te_grad_zone.max():.2f} K/100km "
              f"(need > {args.te_threshold})")

    # ── Optional: point-level detail for top gradient locations ──
    if args.verbose:
        print(f"\n── Top 10 gradient points in zone (sorted by max channel gradient) ──")
        # Sort by max of T and θe gradient within zone
        max_grad = np.maximum(dual_result["gradient"], dual_result["te_gradient"])
        max_grad[~valid_zone] = 0
        flat_idx = np.argsort(max_grad.ravel())[::-1]
        printed = 0
        for idx in flat_idx:
            if printed >= 10:
                break
            i, j = np.unravel_index(idx, max_grad.shape)
            if not valid_zone[i, j]:
                continue
            g = dual_result["gradient"][i, j]
            te_g = dual_result["te_gradient"][i, j]
            t_bg = mean_t_gradient[i, j]
            te_bg = mean_te_gradient[i, j]
            t_a = t_anomaly[i, j]
            te_a = te_anomaly[i, j]
            fm = "F" if filtered_mask[i, j] else "."
            db = dual_result["detected_by"][i, j]
            src = {0: "none", 1: "T", 2: "θe", 3: "T+θe"}.get(db, "?")
            print(f"  [{fm}] ({lat[i]:5.1f}°N, {lon[j]:6.1f}°E)  "
                  f"T={g:5.2f}(bg{t_bg:4.2f} a{t_a:+5.2f})  "
                  f"θe={te_g:5.2f}(bg{te_bg:4.2f} a{te_a:+5.2f})  "
                  f"src={src}")
            printed += 1

    # ── Optional plot ──
    if args.plot:
        _plot_diagnose(
            dual_result, mean_t_gradient, mean_te_gradient,
            t_anomaly, te_anomaly, filtered_mask,
            lat, lon, terrain_mask, zone_bounds, zone_name,
            model_key, hour, args.output,
        )


def _cmd_plot_hewson(args: argparse.Namespace) -> None:
    """Render the full set of Hewson 1998 / Hewson & Titley 2010 diagnostic fields."""
    from weatherbrief.frontal.case import load_case
    from weatherbrief.frontal.detect import (
        classify_front_type,
        compute_frontal_zones_dual,
        compute_hewson_diagnostics,
    )

    case_dir = Path(args.case)
    case = load_case(case_dir)

    model_key = args.model
    if model_key not in case.models:
        print(
            f"Error: model '{model_key}' not in case (available: {case.models})",
            file=sys.stderr,
        )
        sys.exit(1)

    hour = args.hour
    field_name = args.field

    lat, lon = case.lat, case.lon
    terrain_mask = build_terrain_mask(lat, lon)

    all_fields = case.fields_all_hours(model_key)
    if hour not in all_fields:
        avail = sorted(all_fields.keys())
        print(
            f"Error: no data at hour {hour}. Available: {avail[:10]}"
            f"{'...' if len(avail) > 10 else ''}",
            file=sys.stderr,
        )
        sys.exit(1)

    fields = all_fields[hour]

    # Hewson diagnostics on chosen τ
    diag = compute_hewson_diagnostics(
        fields[field_name], lat, lon,
        u=fields["u850"], v=fields["v850"],
        terrain_mask=terrain_mask,
    )

    # Time tendency from nearest available adjacent hours.
    # Step size is implicit in the hour labels (hourly for Open-Meteo,
    # 6-hourly for ERA5 smoke).
    avail_hours = sorted(all_fields.keys())
    prev_h = max((h for h in avail_hours if h < hour), default=None)
    next_h = min((h for h in avail_hours if h > hour), default=None)
    if prev_h is not None and next_h is not None:
        dt = next_h - prev_h
        tendency = (all_fields[next_h][field_name] - all_fields[prev_h][field_name]) / dt
        tendency_label = f"∂τ/∂t (centered Δt={dt}h, K/h)"
    elif next_h is not None:
        dt = next_h - hour
        tendency = (all_fields[next_h][field_name] - fields[field_name]) / dt
        tendency_label = f"∂τ/∂t (forward Δt={dt}h, K/h)"
    elif prev_h is not None:
        dt = hour - prev_h
        tendency = (fields[field_name] - all_fields[prev_h][field_name]) / dt
        tendency_label = f"∂τ/∂t (backward Δt={dt}h, K/h)"
    else:
        tendency = None
        tendency_label = "∂τ/∂t (unavailable)"

    # Current production detection mask (for overlay on each panel)
    dual = compute_frontal_zones_dual(
        fields["T850"], fields["theta_e"], lat, lon,
        terrain_mask=terrain_mask,
        t_gradient_threshold=args.threshold,
        te_gradient_threshold=args.te_threshold,
    )
    classified = classify_front_type(
        dual["dT_dx"], dual["dT_dy"],
        fields["u850"], fields["v850"],
        dual["frontal_mask"],
        detected_by=dual["detected_by"],
    )

    # Expected zones for this case/hour (if annotated) — guard against typos
    expected_fronts = _load_expected_fronts(case_dir, hour_offset=hour)
    unknown = sorted(set(expected_fronts) - set(ZONES))
    if unknown:
        print(
            f"Warning: expected.yaml references unknown zones (ignored): {', '.join(unknown)}",
            file=sys.stderr,
        )
        expected_fronts = {k: v for k, v in expected_fronts.items() if k in ZONES}

    output = args.output
    if not output:
        output = str(case_dir / f"hewson_{model_key}_{field_name}_T{hour}.png")

    _plot_hewson(
        field_name=field_name, field_raw=fields[field_name],
        diag=diag, tendency=tendency, tendency_label=tendency_label,
        frontal_mask=dual["frontal_mask"], classified=classified,
        expected_fronts=expected_fronts,
        lat=lat, lon=lon, terrain_mask=terrain_mask,
        model_key=model_key, hour=hour, output=output,
    )


def _plot_hewson(
    field_name: str,
    field_raw: np.ndarray,
    diag: dict,
    tendency: np.ndarray | None,
    tendency_label: str,
    frontal_mask: np.ndarray,
    classified: np.ndarray,
    expected_fronts: dict[str, str],
    lat: np.ndarray,
    lon: np.ndarray,
    terrain_mask: np.ndarray | None,
    model_key: str,
    hour: int,
    output: str,
) -> None:
    """Render a 2×3 Hewson diagnostics figure."""
    try:
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
        import matplotlib.patches as mpatches
        import matplotlib.pyplot as plt
    except ImportError:
        print(
            "Error: cartopy required. Install: pip install -e '.[frontal-dev]'",
            file=sys.stderr,
        )
        return

    lon_grid, lat_grid = np.meshgrid(lon, lat)
    extent = [-20, 28, 35, 62]

    field_label = "θe" if field_name == "theta_e" else "T850"

    # Adaptive percentile range — works for both θe (K, ~280-340) and
    # T850 (°C, ~-40 to +20). Fixed bounds would let us compare
    # across hours at a glance, but data-adaptive keeps the colormap
    # full-range for whatever snapshot is being shown.
    field_min = float(np.nanpercentile(diag["field_smooth"], 5))
    field_max = float(np.nanpercentile(diag["field_smooth"], 95))

    fig, axes = plt.subplots(
        2, 3, figsize=(22, 12),
        subplot_kw={"projection": ccrs.PlateCarree()},
    )

    def _decorate(ax):
        ax.add_feature(cfeature.COASTLINE, linewidth=0.7)
        ax.add_feature(cfeature.BORDERS, linewidth=0.4, linestyle=":")
        ax.set_extent(extent, crs=ccrs.PlateCarree())
        # Zone boxes
        for zone_name, bounds in ZONES.items():
            lat0, lat1 = bounds["lat"]
            lon0, lon1 = bounds["lon"]
            ls = "-" if zone_name in expected_fronts else "--"
            lw = 1.2 if zone_name in expected_fronts else 0.5
            ec = "black" if zone_name in expected_fronts else "gray"
            ax.plot(
                [lon0, lon1, lon1, lon0, lon0],
                [lat0, lat0, lat1, lat1, lat0],
                color=ec, linewidth=lw, linestyle=ls,
                transform=ccrs.PlateCarree(),
            )

    def _mask_overlay(ax):
        """Current-production mask as a red contour + classified dots."""
        ax.contour(
            lon_grid, lat_grid, frontal_mask.astype(float),
            levels=[0.5], colors="red", linewidths=1.2,
            transform=ccrs.PlateCarree(),
        )
        # Classified points: cold=blue, warm=magenta, indeterminate=gray
        for cls_val, color, marker in [(1, "#1f5fd9", "v"), (2, "#d93131", "^"), (3, "gray", ".")]:
            ys, xs = np.where(classified == cls_val)
            if len(xs) == 0:
                continue
            ax.scatter(
                lon[xs], lat[ys],
                s=8, c=color, marker=marker, alpha=0.75,
                edgecolors="none", transform=ccrs.PlateCarree(),
            )

    # Panel 1 — τ field
    ax = axes[0, 0]
    im = ax.pcolormesh(
        lon_grid, lat_grid, diag["field_smooth"],
        cmap="RdYlBu_r", vmin=field_min, vmax=field_max,
        transform=ccrs.PlateCarree(), alpha=0.8,
    )
    plt.colorbar(im, ax=ax, shrink=0.7, label=f"{field_label}")
    _mask_overlay(ax)
    _decorate(ax)
    ax.set_title(f"1. τ = {field_label} (smoothed)", fontweight="bold")

    # Panel 2 — |∇τ|  (the current detector signal)
    ax = axes[0, 1]
    grad_max = 10.0 if field_name == "theta_e" else 5.0
    im = ax.pcolormesh(
        lon_grid, lat_grid, diag["gradient"],
        cmap="YlOrRd", vmin=0, vmax=grad_max,
        transform=ccrs.PlateCarree(), alpha=0.85,
    )
    plt.colorbar(im, ax=ax, shrink=0.7, label="K / 100km")
    _mask_overlay(ax)
    _decorate(ax)
    ax.set_title(f"2. |∇τ| — gradient magnitude  [mask criterion]", fontweight="bold")

    # Panel 3 — −∇²τ  (Hewson concavity mask, currently unused)
    ax = axes[0, 2]
    lap = diag["neg_laplacian"]
    vlim = float(np.nanpercentile(np.abs(lap), 98))
    im = ax.pcolormesh(
        lon_grid, lat_grid, lap,
        cmap="RdBu_r", vmin=-vlim, vmax=vlim,
        transform=ccrs.PlateCarree(), alpha=0.85,
    )
    plt.colorbar(im, ax=ax, shrink=0.7, label="K / (100km)²")
    # Positive contour line — Hewson's warm-side inflection
    ax.contour(
        lon_grid, lat_grid, lap,
        levels=[0], colors="black", linewidths=0.6,
        transform=ccrs.PlateCarree(),
    )
    _mask_overlay(ax)
    _decorate(ax)
    ax.set_title(f"3. −∇²τ  [Hewson concavity, NOT IN DETECTION]", fontweight="bold")

    # Panel 4 — TFP with zero-crossings
    ax = axes[1, 0]
    tfp = diag["tfp"]
    vlim_tfp = float(np.nanpercentile(np.abs(tfp), 98))
    im = ax.pcolormesh(
        lon_grid, lat_grid, tfp,
        cmap="PuOr_r", vmin=-vlim_tfp, vmax=vlim_tfp,
        transform=ccrs.PlateCarree(), alpha=0.85,
    )
    plt.colorbar(im, ax=ax, shrink=0.7, label="K / (100km)²")
    ax.contour(
        lon_grid, lat_grid, tfp,
        levels=[0], colors="black", linewidths=0.8, linestyles="dashed",
        transform=ccrs.PlateCarree(),
    )
    _mask_overlay(ax)
    _decorate(ax)
    ax.set_title(f"4. TFP(τ)  — zero-crossings = Hewson locator", fontweight="bold")

    # Panel 5 — advection −V·∇τ
    ax = axes[1, 1]
    adv = diag["advection"]
    vlim_adv = float(np.nanpercentile(np.abs(adv), 98))
    im = ax.pcolormesh(
        lon_grid, lat_grid, adv,
        cmap="RdBu_r", vmin=-vlim_adv, vmax=vlim_adv,
        transform=ccrs.PlateCarree(), alpha=0.85,
    )
    plt.colorbar(im, ax=ax, shrink=0.7, label="K / h  (red=warm adv, blue=cold adv)")
    _mask_overlay(ax)
    _decorate(ax)
    ax.set_title(f"5. −V · ∇τ  [classification candidate]", fontweight="bold")

    # Panel 6 — time tendency ∂τ/∂t
    ax = axes[1, 2]
    if tendency is not None:
        vlim_t = float(np.nanpercentile(np.abs(tendency), 98))
        im = ax.pcolormesh(
            lon_grid, lat_grid, tendency,
            cmap="RdBu_r", vmin=-vlim_t, vmax=vlim_t,
            transform=ccrs.PlateCarree(), alpha=0.85,
        )
        plt.colorbar(im, ax=ax, shrink=0.7, label="K / h")
    _mask_overlay(ax)
    _decorate(ax)
    ax.set_title(f"6. {tendency_label}  [mobile-front filter]", fontweight="bold")

    # Legend for the scatter overlay
    legend_handles = [
        mpatches.Patch(color="#1f5fd9", label="Detected: cold (▼)"),
        mpatches.Patch(color="#d93131", label="Detected: warm (▲)"),
        mpatches.Patch(color="gray", label="Detected: indeterminate"),
        mpatches.Patch(edgecolor="black", facecolor="none", label="Expected-front zone (solid box)"),
    ]
    fig.legend(
        handles=legend_handles, loc="lower center",
        bbox_to_anchor=(0.5, -0.02), ncol=4, fontsize=10,
    )

    fig.suptitle(
        f"Hewson diagnostics — {model_key.upper()} T+{hour}h — τ = {field_label}",
        fontsize=15, fontweight="bold",
    )
    plt.tight_layout(rect=[0, 0.02, 1, 0.96])
    fig.savefig(output, dpi=130, bbox_inches="tight")
    print(f"Plot saved to {output}")


def _plot_diagnose(
    dual_result: dict,
    mean_t_gradient: np.ndarray,
    mean_te_gradient: np.ndarray,
    t_anomaly: np.ndarray,
    te_anomaly: np.ndarray,
    filtered_mask: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    terrain_mask: np.ndarray | None,
    zone_bounds: dict,
    zone_name: str,
    model_key: str,
    hour: int,
    output: str | None,
) -> None:
    """Generate 4-panel diagnostic plot: T/θe gradients + anomalies."""
    try:
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
        import matplotlib.patches as mpatches
        import matplotlib.pyplot as plt
    except ImportError:
        print(
            "Error: cartopy required. Install: pip install -e '.[frontal-dev]'",
            file=sys.stderr,
        )
        return

    lon_grid, lat_grid = np.meshgrid(lon, lat)
    z_lat = zone_bounds["lat"]
    z_lon = zone_bounds["lon"]
    extent = [z_lon[0] - 5, z_lon[1] + 5, z_lat[0] - 3, z_lat[1] + 3]

    panels = [
        (dual_result["gradient"], "T850 gradient (K/100km)", "YlOrRd", 0, 5.0),
        (t_anomaly, "T850 anomaly", "RdBu_r", -3.0, 3.0),
        (dual_result["te_gradient"], "θe gradient (K/100km)", "YlOrRd", 0, 10.0),
        (te_anomaly, "θe anomaly", "RdBu_r", -5.0, 5.0),
    ]

    fig, axes = plt.subplots(
        2, 2, figsize=(18, 14),
        subplot_kw={"projection": ccrs.PlateCarree()},
    )

    for ax, (field, title, cmap, vmin, vmax) in zip(axes.flat, panels):
        im = ax.pcolormesh(
            lon_grid, lat_grid, field,
            cmap=cmap, vmin=vmin, vmax=vmax,
            transform=ccrs.PlateCarree(), alpha=0.8,
        )
        plt.colorbar(im, ax=ax, shrink=0.7)

        # Dual mask (before anomaly)
        ax.contour(
            lon_grid, lat_grid,
            dual_result["frontal_mask"].astype(float),
            levels=[0.5], colors="blue", linewidths=1.0, linestyles="--",
            transform=ccrs.PlateCarree(),
        )
        # Filtered mask (after per-channel anomaly)
        ax.contour(
            lon_grid, lat_grid,
            filtered_mask.astype(float),
            levels=[0.5], colors="red", linewidths=1.5,
            transform=ccrs.PlateCarree(),
        )

        rect = mpatches.Rectangle(
            (z_lon[0], z_lat[0]), z_lon[1] - z_lon[0], z_lat[1] - z_lat[0],
            facecolor="none", edgecolor="black", linewidth=2,
            transform=ccrs.PlateCarree(),
        )
        ax.add_patch(rect)

        ax.add_feature(cfeature.COASTLINE, linewidth=0.8)
        ax.add_feature(cfeature.BORDERS, linewidth=0.5, linestyle=":")
        ax.set_extent(extent, crs=ccrs.PlateCarree())
        ax.set_title(title, fontsize=11, fontweight="bold")

    fig.suptitle(
        f"Diagnose: {model_key.upper()} T+{hour}h — {ZONES[zone_name]['display']}\n"
        f"Blue dashed = dual mask, Red solid = filtered (post per-channel anomaly)",
        fontsize=13, fontweight="bold",
    )
    plt.tight_layout(rect=[0, 0, 1, 0.95])

    out = output or f"data/diagnose_{model_key}_{zone_name}_T{hour}.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"\nPlot saved to {out}")


# ---------------------------------------------------------------------------
# DWD chart download and georeferencing
# ---------------------------------------------------------------------------

_DWD_CHART_BASE = "https://www.dwd.de/DWD/wetter/wv_spez/hobbymet/wetterkarten"
_DWD_CHARTS = {
    "analysis": "bwk_bodendruck_na_ana.png",
    "icon_036": "ico_tkboden_na_036.png",
    "icon_048": "ico_tkboden_na_048.png",
    "icon_060": "ico_tkboden_na_060.png",
    "icon_084": "ico_tkboden_na_084.png",
    "icon_108": "ico_tkboden_na_108.png",
}
_DWD_CHART_DIR = Path("data/dwd_charts")


def _download_dwd_chart(name: str, filename: str, output_dir: Path) -> Path | None:
    """Download a DWD chart with If-Modified-Since caching.

    Returns the path to the (possibly cached) file, or None on failure.
    """
    import email.utils
    import time

    import httpx

    url = f"{_DWD_CHART_BASE}/{filename}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check for existing cached file and its timestamp
    cached = output_dir / filename
    headers = {}
    if cached.exists():
        mtime = cached.stat().st_mtime
        headers["If-Modified-Since"] = email.utils.formatdate(
            mtime, usegmt=True,
        )

    try:
        resp = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
        if resp.status_code == 304:
            lm = time.strftime(
                "%Y-%m-%d %H:%MZ", time.gmtime(cached.stat().st_mtime),
            )
            print(f"  {name:<12} unchanged (cached {lm})")
            return cached

        resp.raise_for_status()
        cached.write_bytes(resp.content)

        # Set file mtime from Last-Modified header
        last_mod = resp.headers.get("Last-Modified")
        if last_mod:
            import os
            ts = email.utils.parsedate_to_datetime(last_mod).timestamp()
            os.utime(cached, (ts, ts))
            lm = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(ts))
            print(f"  {name:<12} downloaded ({lm}, {len(resp.content)//1024}KB)")
        else:
            print(f"  {name:<12} downloaded ({len(resp.content)//1024}KB)")

        return cached

    except httpx.HTTPError as e:
        print(f"  {name:<12} FAILED: {e}", file=sys.stderr)
        return None


def _dwd_lonlat_to_pixel(
    lon: float, lat: float, chart_type: str = "analysis",
) -> tuple[int, int]:
    """Convert lon/lat to pixel coordinates on a DWD chart.

    Uses polar stereographic projections with homography transforms
    calibrated from user-provided gridline intersection points.

    chart_type: "analysis" (4389×3114) or "icon" (800×653).
    """
    import pyproj

    # Each chart type has its own projection and homography, calibrated
    # from 7 lat/lon gridline intersections identified by the user.
    if chart_type == "icon":
        # ICON forecast chart (800×653)
        # Projection: stere lat_0=90 lat_ts=60 lon_0=5
        # Max calibration error: 1.2px
        proj = pyproj.Proj(proj="stere", lat_0=90, lat_ts=60, lon_0=5)
        H = [
            0.00010064544583772085, 8.021406574208543e-06, 505.2081110061641,
            8.455010167934436e-06, -0.00010106842893807126, -114.44765418753545,
            -3.747121446686481e-10, -3.0137722186545355e-10,
        ]
    else:
        # Analysis chart (4389×3114)
        # Projection: stere lat_0=90 lat_ts=90 lon_0=10
        # Max calibration error: 3.0px
        proj = pyproj.Proj(proj="stere", lat_0=90, lat_ts=90, lon_0=10)
        H = [
            0.0005145316041850823, 5.082539130306949e-06, 2793.4808547432303,
            1.1777669359815649e-06, -0.0005125590704322343, -606.7192569143913,
            8.930427981271965e-10, 1.7297125807436028e-09,
        ]

    a, b, c, d, e, f, g, h = H
    x, y = proj(lon, lat)
    denom = g * x + h * y + 1
    px = (a * x + b * y + c) / denom
    py = (d * x + e * y + f) / denom

    return int(px), int(py)


_FRONT_FILL_RGBA = {
    "cold": (0, 100, 255, 90),
    "warm": (255, 40, 40, 90),
    "occluded": (170, 50, 200, 90),
    "stationary": (120, 120, 120, 90),
}


def _load_label_font(size: int):
    """Load a bold TTF for zone labels, fall back to PIL default."""
    from PIL import ImageFont

    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _draw_zones_on_dwd(
    img_path: str | Path, output_path: str | Path,
    chart_type: str = "analysis",
    expected_fronts: dict[str, str] | None = None,
) -> None:
    """Draw zone boxes with labels on a DWD chart image.

    expected_fronts : optional mapping zone_name → front type
        ('cold', 'warm', 'occluded', 'stationary'). Zones present in
        the mapping are filled with a semi-transparent color-coded
        overlay for quick visual verification against the reference
        chart.
    """
    from PIL import Image, ImageDraw

    img = Image.open(img_path).convert("RGBA")
    line_width = 4 if chart_type == "analysis" else 2
    font_size = 48 if chart_type == "analysis" else 16
    font = _load_label_font(font_size)

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    expected_fronts = expected_fronts or {}

    for zone_name, bounds in ZONES.items():
        lat0, lat1 = bounds["lat"]
        lon0, lon1 = bounds["lon"]

        # Sample the zone polygon along each edge (projection curves it)
        points = []
        n_edge = 10
        for i in range(n_edge):
            t = i / n_edge
            points.append(_dwd_lonlat_to_pixel(
                lon0 + t * (lon1 - lon0), lat0, chart_type,
            ))
        for i in range(n_edge):
            t = i / n_edge
            points.append(_dwd_lonlat_to_pixel(
                lon1, lat0 + t * (lat1 - lat0), chart_type,
            ))
        for i in range(n_edge):
            t = i / n_edge
            points.append(_dwd_lonlat_to_pixel(
                lon1 - t * (lon1 - lon0), lat1, chart_type,
            ))
        for i in range(n_edge):
            t = i / n_edge
            points.append(_dwd_lonlat_to_pixel(
                lon0, lat1 - t * (lat1 - lat0), chart_type,
            ))
        points.append(points[0])

        front_type = expected_fronts.get(zone_name)
        if front_type:
            fill = _FRONT_FILL_RGBA.get(front_type, _FRONT_FILL_RGBA["stationary"])
            draw.polygon(points, fill=fill)

        draw.line(points, fill=(220, 0, 0, 255), width=line_width)

        # Centered label with a white halo box for legibility
        cx, cy = _dwd_lonlat_to_pixel(
            (lon0 + lon1) / 2, (lat0 + lat1) / 2, chart_type,
        )
        label = bounds["display"]
        if front_type:
            label = f"{label} ({front_type[0].upper()})"
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = max(4, font_size // 8)
        draw.rectangle(
            (cx - tw // 2 - pad, cy - th // 2 - pad,
             cx + tw // 2 + pad, cy + th // 2 + pad),
            fill=(255, 255, 255, 200),
        )
        draw.text(
            (cx - tw // 2, cy - th // 2 - bbox[1]),
            label, fill=(180, 0, 0, 255), font=font,
        )

    Image.alpha_composite(img, overlay).convert("RGB").save(output_path)


def _load_expected_fronts(case_dir: Path, hour_offset: int = 0) -> dict[str, str]:
    """Read expected.yaml and return zone→front_type for the given hour_offset.

    Returns an empty dict if the file is missing, the entry is absent,
    or its zones block is empty.
    """
    import yaml

    expected_path = case_dir / "expected.yaml"
    if not expected_path.exists():
        return {}
    with open(expected_path) as f:
        entries = yaml.safe_load(f) or []
    for entry in entries:
        if entry.get("hour_offset") == hour_offset:
            return entry.get("zones") or {}
    return {}


def _cmd_new_case(args: argparse.Namespace) -> None:
    """Create a new calibration case from either live Open-Meteo or an ERA5 GRIB."""
    from datetime import datetime, timezone

    from weatherbrief.frontal.case import (
        build_case_from_era5,
        build_case_from_open_meteo,
    )

    if args.source == "era5":
        _new_case_era5(args)
        return

    # Open-Meteo path (default)
    from weatherbrief.fetch.model_status import fetch_model_metadata

    case_name = args.name
    if not case_name:
        meta = fetch_model_metadata(models=["ecmwf"])
        if "ecmwf" in meta:
            init_dt = datetime.fromtimestamp(
                meta["ecmwf"].last_init_time, tz=timezone.utc,
            )
            case_name = init_dt.strftime("%Y-%m-%d_%HZ")
        else:
            case_name = datetime.now(timezone.utc).strftime("%Y-%m-%d_%HZ")

    case_dir = Path("data/calibration") / case_name
    if case_dir.exists() and not args.force:
        print(f"Case {case_dir} already exists. Use --force to overwrite.")
        return

    print(f"Creating Open-Meteo calibration case: {case_dir}")

    cache_dir = Path(args.cache_dir) if args.cache_dir else cache._DEFAULT_CACHE_DIR
    models = ["ecmwf", "gfs", "icon"]

    # Fetch via existing pipeline — cached for reuse
    result = _run_analysis(
        models, use_cache=True, cache_dir=cache_dir,
        t_threshold=args.threshold, te_threshold=args.te_threshold,
    )

    # Convert raw cached JSON → per-model NPZ under case dir
    raw_responses: dict[str, dict] = {}
    import json
    for model_key in models:
        init_time = result["model_init_times"].get(model_key)
        if not init_time:
            print(f"  {model_key}: no init_time — skipping")
            continue
        raw_path = cache_dir / f"{model_key}_{init_time}.json"
        if not raw_path.exists():
            print(f"  {model_key}: cache not found at {raw_path}")
            continue
        raw_responses[model_key] = json.loads(raw_path.read_text())
        init_str = datetime.fromtimestamp(init_time, tz=timezone.utc).strftime("%Y-%m-%d %HZ")
        print(f"  {model_key}: {init_str}")

    build_case_from_open_meteo(
        case_dir,
        raw_responses=raw_responses,
        init_times={m: result["model_init_times"].get(m, 0) for m in raw_responses},
        timestamps={m: result["timestamps"].get(m, []) for m in raw_responses},
        lat=result["lat"], lon=result["lon"],
        terrain_mask=result["terrain_mask"],
        case_name=case_name,
    )

    # Reference charts + skeleton
    ref_dir = case_dir / "reference"
    print("\nDownloading DWD reference charts:")
    expected_fronts = _load_expected_fronts(case_dir, hour_offset=0)
    for name, filename in _DWD_CHARTS.items():
        path = _download_dwd_chart(name, filename, ref_dir)
        if path and name == "analysis":
            overlay_path = ref_dir / "analysis_with_zones.png"
            _draw_zones_on_dwd(
                path, overlay_path, "analysis",
                expected_fronts=expected_fronts,
            )

    ecmwf_init = result["model_init_times"].get("ecmwf", 0)
    init_dt = datetime.fromtimestamp(ecmwf_init, tz=timezone.utc)
    _write_expected_yaml_skeleton(case_dir, init_dt, force=args.force)

    print(f"\nCase ready at {case_dir}/")
    print(f"  1. Open {ref_dir / 'analysis_with_zones.png'} — identify fronts per zone")
    print(f"  2. Edit {case_dir / 'expected.yaml'} — annotate zones")
    print(f"  3. Score: python -m weatherbrief.frontal.cli score --case {case_dir}")


def _new_case_era5(args: argparse.Namespace) -> None:
    """Build a calibration case from an ERA5 GRIB file."""
    from datetime import datetime, timedelta, timezone

    from weatherbrief.frontal.case import build_case_from_era5
    from weatherbrief.frontal.grid import build_grid_coords, build_terrain_mask

    if not args.grib:
        print("Error: --grib is required when --source era5", file=sys.stderr)
        sys.exit(1)
    if not args.date:
        print("Error: --date YYYY-MM-DD is required when --source era5", file=sys.stderr)
        sys.exit(1)

    grib_path = Path(args.grib)
    if not grib_path.exists():
        print(f"Error: GRIB not found at {grib_path}", file=sys.stderr)
        sys.exit(1)

    case_name = args.name or f"{args.date}_era5"
    case_dir = Path("data/calibration") / case_name
    if case_dir.exists() and not args.force:
        print(f"Case {case_dir} already exists. Use --force to overwrite.")
        return

    print(f"Creating ERA5 calibration case: {case_dir}")
    print(f"  GRIB:  {grib_path}")
    print(f"  Date:  {args.date}")
    print(f"  Level: {args.level} hPa")

    lat, lon = build_grid_coords()
    terrain_mask = build_terrain_mask(lat, lon)

    build_case_from_era5(
        case_dir, grib_path,
        case_name=case_name,
        level_hPa=args.level,
        terrain_mask=terrain_mask,
    )

    # Reference charts for the annotation-anchor time (00Z of --date)
    ref_dir = case_dir / "reference"
    print("\nDownloading DWD reference charts:")
    expected_fronts = _load_expected_fronts(case_dir, hour_offset=0)
    for name, filename in _DWD_CHARTS.items():
        path = _download_dwd_chart(name, filename, ref_dir)
        if path and name == "analysis":
            overlay_path = ref_dir / "analysis_with_zones.png"
            _draw_zones_on_dwd(
                path, overlay_path, "analysis",
                expected_fronts=expected_fronts,
            )

    # Anchor timestamp: midnight of the requested date
    anchor_dt = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    _write_expected_yaml_skeleton(case_dir, anchor_dt, force=args.force)

    print(f"\nCase ready at {case_dir}/")
    print(f"  1. Open {ref_dir / 'analysis_with_zones.png'} — identify fronts per zone")
    print(f"  2. Edit {case_dir / 'expected.yaml'} — annotate zones")
    print(f"  3. Plot: python -m weatherbrief.frontal.cli plot-hewson --case {case_dir} --model era5 --hour 0")


def _write_expected_yaml_skeleton(
    case_dir: Path, anchor_dt: "datetime", force: bool = False,
) -> None:
    """Write a skeleton expected.yaml with one entry per DWD chart offset."""
    from datetime import timedelta

    expected_path = case_dir / "expected.yaml"
    if expected_path.exists() and not force:
        print(f"\n{expected_path} already exists (not overwritten)")
        return

    skeleton = (
        f"# Calibration case anchor: {anchor_dt.strftime('%Y-%m-%d %HZ')}\n"
        f"# Reference: DWD Bodenwetterkarte + ICON forecast charts\n"
        f"#\n"
        f"# Annotate zones by visual inspection of DWD analysis chart.\n"
        f"# Types: cold, warm, occluded, stationary\n"
        f"# Only list zones where a front is clearly drawn.\n"
        f"# Zone reference: reference/analysis_with_zones.png\n"
        f"\n"
        f"# DWD analysis chart (surface analysis at anchor time)\n"
        f"- time: \"{anchor_dt.strftime('%d/%m %HZ')}\"\n"
        f"  hour_offset: 0\n"
        f"  notes: >\n"
        f"    TODO: describe synoptic situation from DWD analysis chart\n"
        f"  zones: {{}}\n"
        f"    # example: uk_south: cold\n"
    )
    for name, filename in _DWD_CHARTS.items():
        if not name.startswith("icon_"):
            continue
        hours = int(name.split("_")[1])
        forecast_dt = anchor_dt + timedelta(hours=hours)
        skeleton += (
            f"\n"
            f"# ICON forecast T+{hours}h ({filename})\n"
            f"- time: \"{forecast_dt.strftime('%d/%m %HZ')}\"\n"
            f"  hour_offset: {hours}\n"
            f"  notes: >\n"
            f"    TODO: describe from ICON forecast chart\n"
            f"  zones: {{}}\n"
        )
    expected_path.write_text(skeleton)
    print(f"\nCreated skeleton: {expected_path}")


def _cmd_charts(args: argparse.Namespace) -> None:
    """Download DWD weather charts (analysis + ICON forecasts)."""
    output_dir = Path(args.output_dir) if args.output_dir else _DWD_CHART_DIR

    if args.chart:
        charts = {args.chart: _DWD_CHARTS[args.chart]}
    else:
        charts = _DWD_CHARTS

    print(f"Downloading DWD charts to {output_dir}/")
    for name, filename in charts.items():
        _download_dwd_chart(name, filename, output_dir)

    # Optionally overlay zones
    if args.zones:
        analysis_path = output_dir / _DWD_CHARTS["analysis"]
        if analysis_path.exists():
            out = output_dir / "analysis_with_zones.png"
            _draw_zones_on_dwd(analysis_path, out, "analysis")
            print(f"  Zone overlay → {out}")


def _cmd_redraw_zones(args: argparse.Namespace) -> None:
    """Redraw analysis_with_zones.png for a case using its current expected.yaml."""
    case_dir = Path(args.case)
    ref_dir = case_dir / "reference"
    analysis_path = ref_dir / _DWD_CHARTS["analysis"]
    if not analysis_path.exists():
        print(f"Error: {analysis_path} not found", file=sys.stderr)
        sys.exit(1)

    expected_fronts = _load_expected_fronts(case_dir, hour_offset=args.hour_offset)
    unknown = sorted(set(expected_fronts) - set(ZONES))
    if unknown:
        print(
            f"Warning: expected.yaml references unknown zones (ignored): {', '.join(unknown)}",
            file=sys.stderr,
        )
        expected_fronts = {k: v for k, v in expected_fronts.items() if k in ZONES}
    overlay_path = ref_dir / "analysis_with_zones.png"
    _draw_zones_on_dwd(
        analysis_path, overlay_path, "analysis",
        expected_fronts=expected_fronts,
    )
    if expected_fronts:
        print(f"Redrew {overlay_path} with {len(expected_fronts)} annotated zone(s):")
        for zone, ftype in sorted(expected_fronts.items()):
            print(f"  {zone}: {ftype}")
    else:
        print(f"Redrew {overlay_path} (no annotated zones at hour_offset={args.hour_offset})")


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
    p_zones.add_argument(
        "--threshold", type=float, default=2.0,
        help="T850 gradient threshold (K/100km, default: 2.0)",
    )
    p_zones.add_argument(
        "--te-threshold", type=float, default=4.0,
        help="θe gradient threshold (K/100km, default: 4.0)",
    )

    # route
    p_route = sub.add_parser("route", help="Show route frontal table")
    p_route.add_argument("--template", help="Route template name")
    p_route.add_argument(
        "--threshold", type=float, default=2.0,
        help="T850 gradient threshold (K/100km, default: 2.0)",
    )
    p_route.add_argument(
        "--te-threshold", type=float, default=4.0,
        help="θe gradient threshold (K/100km, default: 4.0)",
    )

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
        "--models", nargs="+", default=None,
        help="Models to score (default: all models present in the case)",
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
    p_validate.add_argument(
        "--threshold", type=float, default=2.0,
        help="T850 gradient threshold (K/100km, default: 2.0)",
    )
    p_validate.add_argument(
        "--te-threshold", type=float, default=4.0,
        help="θe gradient threshold (K/100km, default: 4.0)",
    )
    p_validate.add_argument("--no-cache", action="store_true")
    p_validate.add_argument("--cache-dir")

    # diagnose
    p_diag = sub.add_parser(
        "diagnose",
        help="Deep diagnostic of detection pipeline for a specific zone/hour",
    )
    p_diag.add_argument(
        "--case", required=True,
        help="Calibration case dir (e.g. data/calibration/2026-04-16_12Z)",
    )
    p_diag.add_argument("--model", default="ecmwf",
                        help="Model key from the case (ecmwf/gfs/icon/era5)")
    p_diag.add_argument("--hour", type=int, required=True, help="Forecast hour offset")
    p_diag.add_argument("--zone", required=True, help="Zone name (e.g. uk_south)")
    p_diag.add_argument(
        "--threshold", type=float, default=2.0,
        help="T850 gradient threshold (K/100km)",
    )
    p_diag.add_argument(
        "--te-threshold", type=float, default=4.0,
        help="θe gradient threshold (K/100km)",
    )
    p_diag.add_argument("--anomaly", type=float, default=1.0)
    p_diag.add_argument("--floor", type=float, default=2.0)
    p_diag.add_argument("--plot", action="store_true", help="Generate diagnostic plot")
    p_diag.add_argument("--output", help="Plot output path")
    p_diag.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show top gradient points with coordinates",
    )

    # new-case
    p_new = sub.add_parser(
        "new-case",
        help="Create a new calibration case from Open-Meteo live data or an ERA5 GRIB",
    )
    p_new.add_argument(
        "--source", choices=["open_meteo", "era5"], default="open_meteo",
        help="Data source (default: open_meteo fetches the latest live models)",
    )
    p_new.add_argument(
        "--name", help="Case name (default: auto from ECMWF init time or --date)",
    )
    p_new.add_argument(
        "--force", action="store_true",
        help="Overwrite existing case",
    )
    # Open-Meteo-only options
    p_new.add_argument("--cache-dir")
    p_new.add_argument(
        "--threshold", type=float, default=2.0,
        help="T850 gradient threshold (K/100km) — Open-Meteo flow only",
    )
    p_new.add_argument(
        "--te-threshold", type=float, default=4.0,
        help="θe gradient threshold (K/100km) — Open-Meteo flow only",
    )
    # ERA5-only options
    p_new.add_argument(
        "--grib",
        help="Path to ERA5 GRIB file (required for --source era5)",
    )
    p_new.add_argument(
        "--date",
        help="Case anchor date YYYY-MM-DD (required for --source era5)",
    )
    p_new.add_argument(
        "--level", type=int, default=850,
        help="Pressure level hPa to extract from ERA5 GRIB (default 850)",
    )

    # charts
    p_charts = sub.add_parser(
        "charts", help="Download DWD weather charts (analysis + ICON forecasts)",
    )
    p_charts.add_argument(
        "--chart", choices=list(_DWD_CHARTS.keys()),
        help="Download a specific chart (default: all)",
    )
    p_charts.add_argument(
        "--output-dir", help=f"Output directory (default: {_DWD_CHART_DIR})",
    )
    p_charts.add_argument(
        "--zones", action="store_true",
        help="Overlay zone boxes on the analysis chart",
    )

    # plot-hewson
    p_hewson = sub.add_parser(
        "plot-hewson",
        help="Render full Hewson 1998 diagnostic fields (τ, |∇τ|, −∇²τ, TFP, advection, tendency)",
    )
    p_hewson.add_argument(
        "--case", required=True,
        help="Calibration case dir (e.g. data/calibration/2026-04-24_00Z)",
    )
    p_hewson.add_argument(
        "--model", default="ecmwf",
        help="Model key as present in the case (ecmwf/gfs/icon for open_meteo, era5 for era5)",
    )
    p_hewson.add_argument("--hour", type=int, default=0)
    p_hewson.add_argument(
        "--field", default="theta_e", choices=["theta_e", "T850"],
        help="Thermal field τ to diagnose (default: theta_e — Hewson's primary quantity)",
    )
    p_hewson.add_argument(
        "--threshold", type=float, default=2.0,
        help="T850 gradient threshold for the overlaid production mask",
    )
    p_hewson.add_argument(
        "--te-threshold", type=float, default=4.0,
        help="θe gradient threshold for the overlaid production mask",
    )
    p_hewson.add_argument("--output", help="Output PNG path (default: under case dir)")

    # redraw-zones
    p_redraw = sub.add_parser(
        "redraw-zones",
        help="Regenerate analysis_with_zones.png from the case's expected.yaml",
    )
    p_redraw.add_argument(
        "--case", required=True,
        help="Calibration case dir (e.g. data/calibration/2026-04-24_00Z)",
    )
    p_redraw.add_argument(
        "--hour-offset", type=int, default=0,
        help="Which expected.yaml entry to visualize (default: 0 = analysis)",
    )

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
        "diagnose": _cmd_diagnose,
        "new-case": _cmd_new_case,
        "charts": _cmd_charts,
        "redraw-zones": _cmd_redraw_zones,
        "plot-hewson": _cmd_plot_hewson,
        "clear-cache": _cmd_clear_cache,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
