#!/usr/bin/env python3
"""Collect complete (full-fidelity) packs from prod into the eval staging area (#252).

Prod keeps a pack's heavy artifacts (cross_section, route_analyses, ...) past the
normal 30-day T1 retention only when the flight has a **debrief** — the pilot's
flown/cancelled judgement. So "complete AND older than 30 days" is a strong proxy
for "debriefed flight" — high-value ground truth for the eval set.

This finds those packs on prod, rsyncs each into a temp dir, and ingests it into
staging (builds corpus_meta, gzips the cross-section). Idempotent: re-ingesting a
pack already in staging preserves its label.

Usage:
    python scripts/pull_prod_complete_packs.py --min-age-days 30 --dry-run
    python scripts/pull_prod_complete_packs.py --min-age-days 30
"""

from __future__ import annotations

import argparse
import functools
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from weatherbrief.eval_workbench.ingest import ingest_pack  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "ops"))
from hosts import server_values  # noqa: E402


@functools.cache
def _prod() -> tuple[str, str]:
    """(ssh target, host packs dir), resolved and checked from deploy/hosts.json."""
    v = server_values("SERVER_SSH", "HOST_DATA_DIR")
    return v["SERVER_SSH"], f"{v['HOST_DATA_DIR']}/packs"


def list_prod_complete(min_age_days: int) -> list[str]:
    """Prod pack dirs (relative tails) with cross_section.json older than N days."""
    prod, prod_data = _prod()
    cmd = [
        "ssh", "-o", "ConnectTimeout=15", prod,
        f"find {prod_data} -name cross_section.json -mtime +{min_age_days}",
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120).stdout
    tails = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # .../packs/<user>/<flight>/<ts>/cross_section.json -> <user>/<flight>/<ts>
        rel = line.replace(f"{prod_data}/", "").rsplit("/cross_section.json", 1)[0]
        tails.append(rel)
    return tails


def main() -> None:
    ap = argparse.ArgumentParser(description="Pull complete prod packs into staging")
    ap.add_argument("--min-age-days", type=int, default=30)
    ap.add_argument("--area", choices=("staging", "corpus"), default="staging")
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--tails-file", help="File of prod pack tails (<user>/<flight>/<ts>), "
                    "one per line — pull exactly these instead of the age scan")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.tails_file:
        tails = [ln.strip() for ln in Path(args.tails_file).read_text().splitlines() if ln.strip()]
    else:
        tails = list_prod_complete(args.min_age_days)
    if args.limit:
        tails = tails[: args.limit]
    print(f"Found {len(tails)} complete prod packs >{args.min_age_days}d.\n")

    ingested = skipped = failed = 0
    tmp_root = Path(tempfile.mkdtemp(prefix="prod_packs_"))
    try:
        for tail in tails:
            name = tail.replace("/", "__")
            print(f"  ↓ {tail}")
            if args.dry_run:
                continue
            local = tmp_root / name
            local.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.run(
                    ["rsync", "-az", "--timeout=120", f"{_prod()[0]}:{_prod()[1]}/{tail}/", f"{local}/"],
                    check=True, capture_output=True, text=True, timeout=300,
                )
                cp = ingest_pack(local, area=args.area)
            except subprocess.CalledProcessError as exc:
                print(f"      ! rsync failed: {exc.stderr.strip()[:100]}")
                failed += 1
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"      ! ingest failed: {exc}")
                failed += 1
                continue
            finally:
                shutil.rmtree(local, ignore_errors=True)
            if cp is None:
                print("      - not labelable (skipped)")
                skipped += 1
                continue
            tag = "labelled" if cp.is_labeled else "new"
            print(f"      ✓ {cp.corpus_id} [{tag}]")
            ingested += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    print(f"\nIngested {ingested}, skipped {skipped}, failed {failed} into {args.area}.")


if __name__ == "__main__":
    main()
