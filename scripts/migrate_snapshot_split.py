#!/usr/bin/env python3
"""Migrate snapshot.json files into split briefing.json + forecasts.json.

Walks all pack directories under data/packs/ and splits each snapshot.json
into two purpose-oriented files:

- briefing.json  — route, analyses, observations, metadata (no forecasts)
- forecasts.json — route, metadata, raw forecasts

Usage:
    python scripts/migrate_snapshot_split.py [--dry-run] [--data-dir DATA_DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

METADATA_KEYS = {"route", "target_date", "fetch_date", "days_out"}


def migrate_pack(pack_dir: Path, *, dry_run: bool) -> dict:
    """Migrate a single pack directory. Returns stats dict."""
    snapshot_path = pack_dir / "snapshot.json"
    briefing_path = pack_dir / "briefing.json"
    forecasts_path = pack_dir / "forecasts.json"

    if not snapshot_path.exists():
        return {"status": "skip", "reason": "no snapshot.json"}

    if briefing_path.exists():
        return {"status": "skip", "reason": "already migrated"}

    try:
        raw = snapshot_path.read_text()
        data = json.loads(raw)
    except Exception as e:
        return {"status": "error", "reason": f"read error: {e}"}

    original_size = snapshot_path.stat().st_size

    # Build briefing: everything except forecasts and cross_sections
    briefing = {k: v for k, v in data.items() if k not in ("forecasts", "cross_sections")}

    # Build forecasts: route + metadata + forecasts
    forecasts = {k: data[k] for k in METADATA_KEYS if k in data}
    forecasts["forecasts"] = data.get("forecasts", [])

    briefing_json = json.dumps(briefing, indent=2)
    forecasts_json = json.dumps(forecasts, indent=2)
    new_size = len(briefing_json) + len(forecasts_json)

    if dry_run:
        return {
            "status": "would_migrate",
            "original_size": original_size,
            "briefing_size": len(briefing_json),
            "forecasts_size": len(forecasts_json),
            "saved": original_size - new_size,
        }

    try:
        briefing_path.write_text(briefing_json)
        forecasts_path.write_text(forecasts_json)
        snapshot_path.unlink()
    except Exception as e:
        return {"status": "error", "reason": f"write error: {e}"}

    return {
        "status": "migrated",
        "original_size": original_size,
        "briefing_size": len(briefing_json),
        "forecasts_size": len(forecasts_json),
        "saved": original_size - new_size,
    }


def find_pack_dirs(data_dir: Path) -> list[Path]:
    """Find all directories that contain a snapshot.json."""
    pack_dirs = []
    packs_root = data_dir / "packs"
    if packs_root.exists():
        for snapshot_path in packs_root.rglob("snapshot.json"):
            pack_dirs.append(snapshot_path.parent)
    # Also check legacy forecasts/ layout
    forecasts_root = data_dir / "forecasts"
    if forecasts_root.exists():
        for snapshot_path in forecasts_root.rglob("snapshot.json"):
            pack_dirs.append(snapshot_path.parent)
    return sorted(set(pack_dirs))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview changes without writing files",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data"),
        help="Data directory (default: data)",
    )
    args = parser.parse_args()

    if not args.data_dir.exists():
        print(f"Data directory not found: {args.data_dir}", file=sys.stderr)
        sys.exit(1)

    pack_dirs = find_pack_dirs(args.data_dir)
    if not pack_dirs:
        print("No snapshot.json files found.")
        return

    print(f"Found {len(pack_dirs)} pack(s) with snapshot.json")
    if args.dry_run:
        print("DRY RUN — no files will be changed\n")

    migrated = 0
    skipped = 0
    errors = 0
    total_saved = 0

    for pack_dir in pack_dirs:
        result = migrate_pack(pack_dir, dry_run=args.dry_run)
        status = result["status"]
        rel_path = pack_dir.relative_to(args.data_dir)

        if status in ("migrated", "would_migrate"):
            saved_kb = result["saved"] / 1024
            print(
                f"  {'[would migrate]' if args.dry_run else '[migrated]'} {rel_path}"
                f"  ({result['original_size'] / 1024:.0f} KB → "
                f"briefing {result['briefing_size'] / 1024:.0f} KB + "
                f"forecasts {result['forecasts_size'] / 1024:.0f} KB, "
                f"saved {saved_kb:.0f} KB)"
            )
            migrated += 1
            total_saved += result["saved"]
        elif status == "skip":
            skipped += 1
        elif status == "error":
            print(f"  [ERROR] {rel_path}: {result['reason']}", file=sys.stderr)
            errors += 1

    print(f"\nSummary: {migrated} migrated, {skipped} skipped, {errors} errors")
    if total_saved > 0:
        print(f"Total space saved: {total_saved / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
