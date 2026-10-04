#!/usr/bin/env python
"""Regenerate the AROME 0.025° domain band table from a delivered message (#529).

The table (``src/weatherbrief/fetch/grib/arome_domain_bands.py``) says which
latitude/longitude spans of AROME's 717×1121 lat/lon array actually carry
data. It is derived from the missing-value bitmap of any AROME 0.025° message
— they all share one domain — so run this whenever Météo-France changes the
domain, or when the decode logs a finite-cell fraction far from 0.828.

Usage::

    # newest 00z run's first IP2 group, downloaded to a temp file (~146 MB)
    python scripts/regen_arome_domain.py --run 2026-10-04T00:00:00Z

    # or any AROME 0.025° GRIB2 file already on disk
    python scripts/regen_arome_domain.py --file arome__0025__IP2__00H06H__....grib2

    # print the table without writing it
    python scripts/regen_arome_domain.py --file ... --dry-run

The script refuses to write a table that rejects any of the issue's verified
inside points or accepts any verified outside point.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "src" / "weatherbrief" / "fetch" / "grib" / "arome_domain_bands.py"

# Verified on issue #529 (2026-07-30).
INSIDE = [(51.5, -0.4), (48.9, 2.4), (52.5, 13.4), (43.5, 5.2)]
OUTSIDE = [(37.55, 2.0), (45.0, -11.95), (45.0, 15.95), (56.0, -3.2)]


def _first_message_mask(path: Path):
    import eccodes
    import numpy as np

    from weatherbrief.fetch.grib.decode import _d2_read_message_grid

    with open(path, "rb") as f:
        gid = eccodes.codes_grib_new_from_file(f)
        if gid is None:
            raise SystemExit(f"{path}: no GRIB message")
        try:
            grid = _d2_read_message_grid(gid)
            label = (
                f"{eccodes.codes_get(gid, 'shortName')} "
                f"{eccodes.codes_get(gid, 'dataDate')}"
                f"{int(eccodes.codes_get(gid, 'dataTime')):04d}"
            )
        finally:
            eccodes.codes_release(gid)
    if grid is None:
        raise SystemExit(f"{path}: first message is not a regular lat/lon grid")
    lats, lons, values = grid
    return lats, lons, np.isfinite(values), label


def _download(run: str, dest: Path) -> None:
    import requests

    from weatherbrief.fetch.grib.arome_fetch import arome_package_url

    init = datetime.strptime(run, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    url = arome_package_url(init.strftime("%Y%m%d"), init.hour, "00H06H")
    print(f"downloading {url}", file=sys.stderr)
    with requests.get(url, stream=True, timeout=(10, 60)) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", type=Path, help="local AROME 0.025° GRIB2 file")
    src.add_argument("--run", help="run token, e.g. 2026-10-04T00:00:00Z")
    ap.add_argument("--band-deg", type=float, default=0.1)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from weatherbrief.fetch.grib.arome_domain import (
        AROME_EXPECTED_FINITE_FRACTION,
        derive_domain_bands,
        point_in_arome_domain,
        render_bands_module,
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = args.file
        if path is None:
            path = Path(tmp) / "arome.grib2"
            _download(args.run, path)
        lats, lons, finite, label = _first_message_mask(path)

    frac = float(finite.mean())
    print(
        f"grid {finite.shape[0]}x{finite.shape[1]}, "
        f"lat {lats[0]:.3f}..{lats[-1]:.3f}, lon {lons[0]:.3f}..{lons[-1]:.3f}, "
        f"finite {frac:.4f} (expected ~{AROME_EXPECTED_FINITE_FRACTION})",
        file=sys.stderr,
    )
    bands = derive_domain_bands(lats, lons, finite, band_deg=args.band_deg)
    if not bands:
        print("no bands derived — refusing to write", file=sys.stderr)
        return 1

    bad = [p for p in INSIDE if not point_in_arome_domain(*p, bands)]
    bad += [p for p in OUTSIDE if point_in_arome_domain(*p, bands)]
    if bad:
        print(f"verified points misclassified: {bad} — refusing to write", file=sys.stderr)
        return 1

    source = f"derived from AROME 0.025 message {label}, finite {frac:.4f}"
    text = render_bands_module(bands, source)
    if args.dry_run:
        print(text)
    else:
        OUT.write_text(text)
        print(f"wrote {len(bands)} bands to {OUT.relative_to(REPO)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
