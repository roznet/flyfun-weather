"""Ad-hoc CLI for Hewson snapshot precompute.

Wraps ``precompute.run_once`` with argparse flags so debugging and manual
reruns go through the exact same code path as the scheduler loop. No
separate implementation — any change to the pipeline takes effect in both
surfaces simultaneously.

Usage:
    python -m weatherbrief.hewson precompute
    python -m weatherbrief.hewson precompute --model ecmwf --dry-run
    python -m weatherbrief.hewson precompute --levels 925,850,700 --force
    python -m weatherbrief.hewson list
    python -m weatherbrief.hewson purge --retention-hours 24
    python -m weatherbrief.hewson era5-case \
        --case data/calibration/2023-11-02_era5_ciaran --label ciaran
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from weatherbrief.hewson.precompute import (
    DEFAULT_LEVELS,
    DEFAULT_MODELS,
    DEFAULT_RETENTION_HOURS,
    DEFAULT_STRIDE_HOURS,
    load_snapshot,
    purge_old_snapshots,
    resolve_output_dir,
    run_once,
)

logger = logging.getLogger(__name__)


def _parse_levels(s: str) -> list[int]:
    return [int(tok.strip()) for tok in s.split(",") if tok.strip()]


def _parse_models(s: str) -> list[str]:
    return [tok.strip() for tok in s.split(",") if tok.strip()]


def _cmd_precompute(args: argparse.Namespace) -> int:
    result = run_once(
        models=args.models,
        levels=args.levels,
        output_dir=args.output_dir,
        forecast_days=args.forecast_days,
        stride_hours=args.stride_hours,
        retention_hours=args.retention_hours,
        dry_run=args.dry_run,
        skip_existing=not args.force,
    )
    for model, path in result.snapshots.items():
        if path is None:
            print(f"  {model}: (dry-run, no file written)")
        else:
            print(f"  {model}: {path}")
    for model, reason in result.skipped.items():
        print(f"  {model}: skipped ({reason})")
    print(
        f"Done in {result.elapsed_seconds:.1f}s — "
        f"{len([p for p in result.snapshots.values() if p])} written, "
        f"{len(result.skipped)} skipped, {result.purged} purged, "
        f"{result.api_calls_total} Open-Meteo API calls "
        f"(not logged to DB — CLI runs do not hit ApiUsageRow)"
    )
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    root = resolve_output_dir(args.output_dir)
    if not root.exists():
        print(f"No snapshot root at {root}")
        return 0

    print(f"Snapshot root: {root}")
    total_bytes = 0
    total_count = 0
    for subdir in sorted(root.iterdir()):
        if not subdir.is_dir():
            continue
        entries = sorted(subdir.glob("*.npz"))
        if not entries:
            continue
        print(f"\n  {subdir.name}/")
        for path in entries:
            st = path.stat()
            size_kb = st.st_size / 1024
            total_bytes += st.st_size
            total_count += 1
            if args.verbose:
                try:
                    snap = load_snapshot(path)
                    n_time = len(snap["valid_times"])
                    levels = list(snap["levels"].tolist())
                    stride = int(snap["stride_hours"]) if "stride_hours" in snap else 1
                    print(
                        f"    {path.name}  {size_kb:6.1f} KB  "
                        f"({n_time} × {stride}h steps, levels={levels})"
                    )
                except Exception as e:
                    print(f"    {path.name}  {size_kb:6.1f} KB  (unreadable: {e})")
            else:
                print(f"    {path.name}  {size_kb:6.1f} KB")
    print(f"\nTotal: {total_count} snapshots, {total_bytes / (1024 * 1024):.1f} MB")
    return 0


def _cmd_era5_case(args: argparse.Namespace) -> int:
    from weatherbrief.hewson.era5_case import build_synoptic_from_case

    out_path = build_synoptic_from_case(
        case_dir=args.case,
        output_dir=args.output_dir,
        levels=args.levels,
    )
    print(f"Wrote {out_path}")
    return 0


def _cmd_purge(args: argparse.Namespace) -> int:
    removed = purge_old_snapshots(
        model=args.model,
        output_dir=args.output_dir,
        retention_hours=args.retention_hours,
    )
    print(f"Purged {removed} snapshot(s) older than {args.retention_hours} h")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m weatherbrief.hewson",
        description="Hewson diagnostic snapshot precompute and maintenance.",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        help="Python logging level (default: INFO)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # precompute
    p_pc = sub.add_parser(
        "precompute",
        help="Fetch Open-Meteo, compute diagnostics, write NPZ snapshots.",
    )
    p_pc.add_argument(
        "--models", type=_parse_models, default=list(DEFAULT_MODELS),
        help=f"Comma-separated model keys (default: {','.join(DEFAULT_MODELS)})",
    )
    p_pc.add_argument(
        "--levels", type=_parse_levels, default=list(DEFAULT_LEVELS),
        help=f"Comma-separated pressure levels in hPa (default: "
             f"{','.join(str(L) for L in DEFAULT_LEVELS)})",
    )
    p_pc.add_argument(
        "--output-dir", type=Path, default=None,
        help="Override snapshot root (default: ${DATA_DIR}/hewson)",
    )
    p_pc.add_argument(
        "--forecast-days", type=int, default=5,
        help="Forecast horizon in days (default: 5 per § 4.3)",
    )
    p_pc.add_argument(
        "--stride-hours", type=int, default=DEFAULT_STRIDE_HOURS,
        help=f"Decimation cadence — keep every Nth forecast hour "
             f"(default: {DEFAULT_STRIDE_HOURS}). Set to 1 to keep every hour.",
    )
    p_pc.add_argument(
        "--retention-hours", type=int, default=DEFAULT_RETENTION_HOURS,
        help=f"Purge snapshots older than N hours after writing "
             f"(default: {DEFAULT_RETENTION_HOURS})",
    )
    p_pc.add_argument(
        "--dry-run", action="store_true",
        help="Compute but do not write NPZ or purge old snapshots.",
    )
    p_pc.add_argument(
        "--force", action="store_true",
        help="Recompute even if a snapshot for the current init exists.",
    )
    p_pc.set_defaults(func=_cmd_precompute)

    # list
    p_ls = sub.add_parser("list", help="Show snapshots currently on disk.")
    p_ls.add_argument("--output-dir", type=Path, default=None)
    p_ls.add_argument(
        "-v", "--verbose", action="store_true",
        help="Also open each NPZ to show hours/levels.",
    )
    p_ls.set_defaults(func=_cmd_list)

    # purge
    p_pu = sub.add_parser("purge", help="Delete snapshots older than retention.")
    p_pu.add_argument("--output-dir", type=Path, default=None)
    p_pu.add_argument("--model", default=None, help="Restrict to one model.")
    p_pu.add_argument(
        "--retention-hours", type=int, default=DEFAULT_RETENTION_HOURS,
    )
    p_pu.set_defaults(func=_cmd_purge)

    # era5-case
    p_e5 = sub.add_parser(
        "era5-case",
        help="Build a synoptic snapshot from an existing ERA5 calibration Case "
             "(historical events, dev-only).",
    )
    p_e5.add_argument(
        "--case", type=Path, required=True,
        help="Path to a Case directory (must contain meta.json and raw/era5.npz).",
    )
    p_e5.add_argument(
        "--levels", type=_parse_levels, default=None,
        help="Restrict to a subset of levels (default: all in the case).",
    )
    p_e5.add_argument(
        "--output-dir", type=Path, default=None,
        help="Override snapshot root (default: ${DATA_DIR}/hewson)",
    )
    p_e5.set_defaults(func=_cmd_era5_case)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Match the web app: load .env so OPENMETEO_API_KEY, DATA_DIR, etc. are
    # picked up from the project root without requiring the caller to source
    # the env file manually. Without this the CLI runs as an anonymous
    # Open-Meteo caller and gets rate-limited hard.
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass  # dotenv is optional in minimal deployments

    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
