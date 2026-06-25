#!/usr/bin/env python3
"""Regenerate the gitignored derived cache for eval packs (#252).

The committed corpus stores ``cross_section.json.gz`` (the raw master) but not
``route_analyses.json`` (which the cross-section view + advisory re-run need) —
that's a gitignored, regenerable cache. After a fresh clone (or a re-pull), run
this to rebuild it from the cross-section, ~3.5s/pack, no network fetch.

Usage:
    python scripts/rehydrate_eval_cache.py --area corpus
    python scripts/rehydrate_eval_cache.py --area staging --id <corpus_id>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from weatherbrief.eval_workbench import corpus  # noqa: E402
from weatherbrief.eval_workbench.situations import load_snapshot_from_pack  # noqa: E402
from weatherbrief.tasks.analyze import run_analysis  # noqa: E402
from weatherbrief.tasks.artifacts import load_cross_sections, load_elevation_profile  # noqa: E402


def rehydrate_one(pack_dir: Path) -> str:
    """Regenerate route_analyses.json from the cross-section. Returns a status."""
    if (pack_dir / "route_analyses.json").exists():
        return "exists"
    cs = load_cross_sections(pack_dir)  # reads .json or .json.gz
    if not cs:
        return "no-cross-section"
    snap = load_snapshot_from_pack(pack_dir)
    if snap is None:
        return "no-snapshot"
    rps = cs[0].route_points
    elev = load_elevation_profile(pack_dir)
    # pack_dir set => run_analysis persists route_analyses.json to disk.
    run_analysis(snap.route, snap.departure_time, snap.forecasts or [], cs, rps,
                 pack_dir=pack_dir, elevation=elev)
    return "rehydrated"


def main() -> None:
    ap = argparse.ArgumentParser(description="Rebuild the derived eval cache")
    ap.add_argument("--area", choices=("staging", "corpus"), default="corpus")
    ap.add_argument("--id", dest="corpus_id")
    args = ap.parse_args()

    packs = (
        [corpus.load_pack(args.corpus_id, args.area)]
        if args.corpus_id
        else corpus.list_corpus(args.area)
    )
    counts: dict[str, int] = {}
    for p in packs:
        status = rehydrate_one(corpus.pack_path(p.corpus_id, args.area))
        counts[status] = counts.get(status, 0) + 1
        if status == "rehydrated":
            print(f"  ✓ {p.corpus_id}")
    print(f"\n{dict(counts)}")


if __name__ == "__main__":
    main()
