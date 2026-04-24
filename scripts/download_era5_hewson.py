#!/usr/bin/env python3
"""Download ERA5 pressure-level data for Hewson diagnostic calibration.

Produces monthly GRIB files covering the frontal-detection domain and
all fields required by ``compute_hewson_diagnostics``:

    - temperature (t)
    - specific_humidity (q)   ERA5 has no native dewpoint on pressure
                              levels; the loader derives Td from T+q+p
    - u_component_of_wind (u)
    - v_component_of_wind (v)

at 925 / 850 / 700 hPa, four synoptic times per day (00/06/12/18 UTC),
on the regular 0.25° lat-lon grid (60N–35N, 20W–28E — aligned with
Open-Meteo's 0.25° delivery so the two sources land on identical grid
points).

The script is **resumable**: monthly files that already exist in the
output directory are skipped. Re-run it to retry failed downloads.

Monthly size target: ~50-70 MB compressed (48 messages × 30 days × 4×/day
× 4 vars × 3 levels). 6-month total: ~350 MB.

Usage (on the ERA5 server):
    python download_era5_hewson.py \\
        --output-dir ./data/hewson/ \\
        --from-month 2024-10 \\
        --to-month 2025-03 \\
        --max-concurrent 6

Requires:
    - cdsapi >= 0.7.0
    - ~/.cdsapirc configured with a CDS Personal Access Token

Companion: scripts/smoke_era5_hewson.py (single-day shakeout).
"""

from __future__ import annotations

import argparse
import calendar
import sys
import time
from pathlib import Path

import cdsapi


# Matches scripts/smoke_era5_hewson.py + frontal/grid.py
_VARIABLES = [
    "temperature",
    "specific_humidity",
    "u_component_of_wind",
    "v_component_of_wind",
]
_PRESSURE_LEVELS = ["925", "850", "700"]
_AREA = [60, -20, 35, 28]                     # N, W, S, E
_GRID = [0.25, 0.25]
_TIMES = ["00:00", "06:00", "12:00", "18:00"]


def iter_months(from_month: str, to_month: str) -> list[tuple[int, int]]:
    """Inclusive month range from 'YYYY-MM' to 'YYYY-MM'."""
    y0, m0 = (int(x) for x in from_month.split("-"))
    y1, m1 = (int(x) for x in to_month.split("-"))
    months = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def submit_era5_downloads(
    output_dir: Path,
    months: list[tuple[int, int]],
    max_concurrent: int,
) -> int:
    """Submit monthly ERA5 Hewson downloads; poll and save as they complete.

    Returns a non-zero count of failed downloads (0 means all good).
    """
    client = cdsapi.Client(wait_until_complete=False)
    output_dir.mkdir(parents=True, exist_ok=True)

    pending: list[tuple] = []
    submitted = 0
    downloaded = 0
    failed = 0

    total_months = len(months)

    for year, month in months:
        target = output_dir / f"era5_hewson_{year}_{month:02d}.grib"
        if target.exists():
            downloaded += 1
            print(f"  Skipping {target.name} (already exists)")
            continue

        while len(pending) >= max_concurrent:
            pending, dl, fl = _poll_and_download(pending)
            downloaded += dl
            failed += fl
            if len(pending) >= max_concurrent:
                time.sleep(30)

        _, last_day = calendar.monthrange(year, month)
        try:
            result = client.retrieve(
                "reanalysis-era5-pressure-levels",
                {
                    "product_type": "reanalysis",
                    "variable": _VARIABLES,
                    "pressure_level": _PRESSURE_LEVELS,
                    "year": str(year),
                    "month": f"{month:02d}",
                    "day": [f"{d:02d}" for d in range(1, last_day + 1)],
                    "time": _TIMES,
                    "area": _AREA,
                    "grid": _GRID,
                    "data_format": "grib",
                },
            )
            pending.append((result, str(target)))
            submitted += 1
            print(
                f"  Submitted {target.name} "
                f"({submitted + downloaded}/{total_months}, "
                f"{len(pending)} pending)"
            )
        except Exception as e:
            print(f"  ERROR submitting {target.name}: {e}")
            failed += 1

    # Drain the queue
    print(f"\nAll requests submitted. Waiting for {len(pending)} remaining...")
    while pending:
        pending, dl, fl = _poll_and_download(pending)
        downloaded += dl
        failed += fl
        if pending:
            print(f"  {len(pending)} still pending...")
            time.sleep(30)

    print(f"\nDone! Downloaded: {downloaded}, Failed: {failed}")
    if failed > 0:
        print("Re-run the script to retry failed downloads (skips completed files).")
    return failed


def _poll_and_download(pending: list[tuple]) -> tuple[list[tuple], int, int]:
    """Check pending requests, download completed ones."""
    still_pending: list[tuple] = []
    downloaded = 0
    failed = 0

    for result, target in pending:
        try:
            result.update()
            state = result.reply.get("state", "unknown")

            if state == "completed":
                result.download(target)
                size_mb = Path(target).stat().st_size / 1024 / 1024
                print(f"  Downloaded {Path(target).name} ({size_mb:.1f} MB)")
                downloaded += 1
            elif state == "failed":
                error = result.reply.get("error", {}).get("message", "unknown error")
                print(f"  FAILED {Path(target).name}: {error}")
                failed += 1
            else:
                still_pending.append((result, target))
        except Exception as e:
            print(f"  ERROR polling {Path(target).name}: {e}")
            still_pending.append((result, target))

    return still_pending, downloaded, failed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download ERA5 monthly GRIB files for Hewson calibration",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Directory to save monthly GRIB files",
    )
    parser.add_argument(
        "--from-month", required=True,
        help="First month to fetch (YYYY-MM)",
    )
    parser.add_argument(
        "--to-month", required=True,
        help="Last month to fetch, inclusive (YYYY-MM)",
    )
    parser.add_argument(
        "--max-concurrent", type=int, default=6,
        help="Max parallel CDS requests (default: 6). CDS limits vary — "
             "start conservative and raise if queue times are short.",
    )
    args = parser.parse_args()

    months = iter_months(args.from_month, args.to_month)

    # 4 vars × 3 levels × 4 times per day × ~30 days × ~40 KB compressed ≈ 58 MB/month
    size_estimate_mb = len(months) * 58

    print("ERA5 Hewson monthly download")
    print(f"  Output:         {args.output_dir}")
    print(f"  Months:         {args.from_month} → {args.to_month} ({len(months)} months)")
    print(f"  Variables:      {_VARIABLES}")
    print(f"  Levels:         {_PRESSURE_LEVELS} hPa")
    print(f"  Area:           N={_AREA[0]}, W={_AREA[1]}, S={_AREA[2]}, E={_AREA[3]}")
    print(f"  Grid:           {_GRID[0]}° × {_GRID[1]}°")
    print(f"  Times:          {_TIMES}")
    print(f"  Max concurrent: {args.max_concurrent}")
    print(f"  Size estimate:  ~{size_estimate_mb:.0f} MB total")
    print()

    failed = submit_era5_downloads(Path(args.output_dir), months, args.max_concurrent)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
