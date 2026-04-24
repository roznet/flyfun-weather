#!/usr/bin/env python3
"""
Smoke test for ERA5 Hewson download.

Downloads a single day (default: 2023-11-02, Storm Ciarán) of all fields
needed for Hewson diagnostics:
  - temperature           (t,  MARS param 130)
  - specific_humidity     (q,  MARS param 133) — ERA5 has no dewpoint on
                                                  pressure levels; the
                                                  loader derives Td from
                                                  T + q + p via MetPy
  - u_component_of_wind   (u,  MARS param 131)
  - v_component_of_wind   (v,  MARS param 132)
at 3 pressure levels (925, 850, 700 hPa) across the frontal-detection
domain (35-60N, 20W-28E) at 0.25°, 4 synoptic times (00/06/12/18 UTC).

Purpose: verify the exact CDS request shape — variable names,
pressure-level format, grid/area spec — on a small request before we
commit to a monthly bulk fetch. Past CDS runs tripped on variable
naming, so isolate that failure mode here.

Expected size: ~3-5 MB GRIB. Typical queue time: 2-10 min.

Runs on the ERA5 server (or anywhere with cdsapi + credentials):
    python smoke_era5_hewson.py --output-dir ./data/

Requires:
    - cdsapi >= 0.7.0
    - ~/.cdsapirc configured with a CDS Personal Access Token
    - Optional: eccodes `grib_ls` binary on PATH for the post-download
      field listing. Without it, we just report file size.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cdsapi

# Fields required for Hewson diagnostics.
# ERA5's pressure-level dataset has no dewpoint — we request specific
# humidity and derive dewpoint downstream from T + q + pressure.
_VARIABLES = [
    "temperature",
    "specific_humidity",
    "u_component_of_wind",
    "v_component_of_wind",
]

# Matching GRIB shortNames for the post-download sanity check.
_EXPECTED_SHORT_NAMES = {"t", "q", "u", "v"}

_PRESSURE_LEVELS = ["925", "850", "700"]

# Matches frontal/grid.py domain (35-60N, 20W-28E)
_AREA = [60, -20, 35, 28]  # N, W, S, E
_GRID = [0.25, 0.25]

_TIMES = ["00:00", "06:00", "12:00", "18:00"]


def run_smoke(date_str: str, output_dir: Path) -> int:
    """Submit the smoke-test request and verify the returned GRIB.

    Returns 0 on success, 1 on failure.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"era5_hewson_smoke_{date_str}.grib"

    if target.exists():
        print(f"Target already exists: {target}")
        print("Delete it and re-run if you want to re-download.")
        return _verify(target)

    year, month, day = date_str.split("-")

    print(f"ERA5 Hewson smoke test — {date_str}")
    print(f"  Variables:   {_VARIABLES}")
    print(f"  Levels:      {_PRESSURE_LEVELS} hPa")
    print(f"  Area:        N={_AREA[0]}, W={_AREA[1]}, S={_AREA[2]}, E={_AREA[3]}")
    print(f"  Grid:        {_GRID[0]}° × {_GRID[1]}°")
    print(f"  Times:       {_TIMES}")
    print(f"  Output:      {target}")
    print()

    client = cdsapi.Client()

    start = time.time()
    print("Submitting request to CDS (blocks until complete)...")

    try:
        client.retrieve(
            "reanalysis-era5-pressure-levels",
            {
                "product_type": "reanalysis",
                "variable": _VARIABLES,
                "pressure_level": _PRESSURE_LEVELS,
                "year": year,
                "month": month,
                "day": day,
                "time": _TIMES,
                "area": _AREA,
                "grid": _GRID,
                "data_format": "grib",
            },
            str(target),
        )
    except Exception as e:
        print(f"\n❌ CDS request failed: {e}")
        print("   Likely causes:")
        print("   - A variable name mismatch (CDS rejects the name silently "
              "vs. explicitly in different SDK versions)")
        print("   - Missing or expired ~/.cdsapirc credentials")
        print("   - Queue backlog; re-run later")
        return 1

    elapsed = time.time() - start
    minutes, seconds = divmod(int(elapsed), 60)

    size_mb = target.stat().st_size / 1024 / 1024
    print()
    print(f"✅ Download complete")
    print(f"   File: {target}")
    print(f"   Size: {size_mb:.2f} MB")
    print(f"   Time: {minutes}m {seconds}s")
    print()

    return _verify(target)


def _verify(target: Path) -> int:
    """Inspect the GRIB and confirm it contains what we asked for.

    Uses eccodes `grib_ls` if available. Otherwise just reports size.
    """
    size_mb = target.stat().st_size / 1024 / 1024

    if size_mb < 1.0:
        print(f"⚠️  File is suspiciously small ({size_mb:.2f} MB). "
              f"Expected 3-5 MB. Probably missing fields.")
        return 1

    grib_ls = shutil.which("grib_ls")
    if grib_ls is None:
        print("ℹ️  grib_ls not on PATH — skipping GRIB content inspection.")
        print("   Install eccodes tools (apt: eccodes / brew: eccodes) for "
              "per-message listing.")
        return 0

    print("── grib_ls output (short names + levels) ──")
    try:
        result = subprocess.run(
            [grib_ls, "-p", "shortName,typeOfLevel,level,dataDate,dataTime", str(target)],
            check=True, capture_output=True, text=True,
        )
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"❌ grib_ls failed: {e.stderr}")
        return 1

    # Sanity check — count messages
    expected_msgs = len(_VARIABLES) * len(_PRESSURE_LEVELS) * len(_TIMES)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    # Drop header + trailer rows; grib_ls output is `<header> <messages> <count-line>`
    # A heuristic: count lines whose first token is an expected shortName.
    msg_count = sum(
        1 for line in lines
        if line.split() and line.split()[0] in _EXPECTED_SHORT_NAMES
    )

    print(f"Messages found (t/q/u/v): {msg_count}  (expected: {expected_msgs})")
    if msg_count == expected_msgs:
        print("✅ All expected fields present.")
        return 0
    else:
        print(f"⚠️  Field count mismatch — "
              f"{expected_msgs - msg_count} missing or named differently.")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--date", default="2023-11-02",
        help="Date to fetch (YYYY-MM-DD). Default: 2023-11-02 (Storm Ciarán)",
    )
    parser.add_argument(
        "--output-dir", default="./data/",
        help="Directory to write the GRIB into (default: ./data/)",
    )
    args = parser.parse_args()

    return run_smoke(args.date, Path(args.output_dir))


if __name__ == "__main__":
    sys.exit(main())
