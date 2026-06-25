#!/usr/bin/env python3
"""Re-run advisories from saved pack data and diff vs the saved baseline (#252).

For each pack in an area, recompute all advisory logic from the stored
``route_analyses.json`` (+ cross_section / elevation) WITHOUT re-fetching and
WITHOUT overwriting the saved ``route_advisories.json``, then report which
advisories changed rating. A change-detector for advisory-code edits against
real briefings.

Only works on packs that still carry ``cross_section.json`` (stripped from prod
at T1 ~30 days) — pull packs fresh.

Usage:
    source venv/bin/activate
    python scripts/rerun_advisories_diff.py --area staging
    python scripts/rerun_advisories_diff.py --area corpus --id <corpus_id>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from weatherbrief.eval_workbench import corpus, rerun  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Re-run advisories and diff vs saved")
    ap.add_argument("--area", choices=("staging", "corpus"), default="staging")
    ap.add_argument("--id", dest="corpus_id", help="One corpus_id (default: all in area)")
    ap.add_argument("--deep", action="store_true",
                    help="Recompute soundings first (for sounding/analysis-layer changes "
                         "like assess_convective_thermo; a plain re-grade won't see those)")
    ap.add_argument("--debrief-outcome", help="Filter to packs whose debrief outcome matches "
                    "KEY=VALUE, e.g. TS=better")
    args = ap.parse_args()

    if args.corpus_id:
        packs = [corpus.load_pack(args.corpus_id, args.area)]
    else:
        packs = corpus.list_corpus(args.area)

    if args.debrief_outcome:
        import json as _json
        key, _, val = args.debrief_outcome.partition("=")
        kept = []
        for p in packs:
            df = corpus.pack_path(p.corpus_id, args.area) / "debrief.json"
            if not df.exists():
                continue
            outc = (_json.loads(df.read_text()).get("outcomes") or {})
            if outc.get(key) == val:
                kept.append(p)
        packs = kept
        print(f"(filtered to {len(packs)} packs with debrief outcome {args.debrief_outcome})")

    print(f"Re-running advisories for {len(packs)} pack(s) in [{args.area}] ...\n")
    changed_packs = 0
    errored = 0
    for p in packs:
        pack_dir = corpus.pack_path(p.corpus_id, args.area)
        try:
            result = rerun.rerun_diff(pack_dir, deep=args.deep)
        except RuntimeError as exc:
            errored += 1
            print(f"  ! {p.corpus_id}: {exc}")
            continue
        n = result["changed_count"]
        if n == 0:
            print(f"  = {p.corpus_id}: no change")
            continue
        changed_packs += 1
        print(f"  Δ {p.corpus_id}: {n} advisory change(s)")
        for c in result["changes"]:
            flag = " (airport-cond; recompute skipped)" if c["airport_conditions_flag"] else ""
            print(f"      {c['advisory_id']}: {c['saved']} -> {c['candidate']}{flag}")
            for model, mc in c["per_model_changes"].items():
                print(f"        [{model}] {mc['saved']} -> {mc['candidate']}")

    print(f"\n{changed_packs} pack(s) changed, {errored} could not re-run "
          f"(likely missing cross_section.json), {len(packs)} total.")


if __name__ == "__main__":
    main()
