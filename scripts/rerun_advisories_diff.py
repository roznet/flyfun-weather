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
    python scripts/rerun_advisories_diff.py --area staging --check-invariants
    python scripts/rerun_advisories_diff.py --area staging --altitude-profile

Every run ends with the cruise-altitude profile of the packs it replayed: a
clean sweep is only as broad as the altitudes in it, and this corpus is
low-level heavy (#578).
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
    ap.add_argument("--check-invariants", action="store_true",
                    help="Also check the published-extent invariants over each re-run "
                         "manifest, and count aggregates that mask a flagged model "
                         "(the affordable real-pack sweep of #578's predicates)")
    ap.add_argument("--altitude-profile", action="store_true",
                    help="Print the cruise-altitude profile of the selected packs "
                         "and exit — what a clean replay does and does not cover")
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

    if args.altitude_profile:
        print(corpus.format_altitude_profile(packs, area=args.area))
        return

    print(f"Re-running advisories for {len(packs)} pack(s) in [{args.area}] ...\n")
    changed_packs = 0
    errored = 0
    violating_packs = 0
    masked_total = 0
    for p in packs:
        pack_dir = corpus.pack_path(p.corpus_id, args.area)
        try:
            result = rerun.rerun_diff(
                pack_dir, deep=args.deep, check_invariants=args.check_invariants,
            )
        except RuntimeError as exc:
            errored += 1
            print(f"  ! {p.corpus_id}: {exc}")
            continue
        if args.check_invariants:
            violations = result["invariant_violations"]
            masked = result["masked_flagged"]
            masked_total += len(masked)
            if violations:
                violating_packs += 1
                print(f"  X {p.corpus_id}: {len(violations)} invariant violation(s)")
                for v in violations:
                    print(f"      {v}")
            for m in masked:
                # Not a violation — MAJORITY's documented behaviour. Printed so
                # the count is measured rather than rediscovered by replay.
                print(f"  ~ {p.corpus_id}: aggregate calmer than {m}")

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
    if args.check_invariants:
        print(f"{violating_packs} pack(s) broke an extent invariant; "
              f"{masked_total} (advisory, model) pair(s) flagged under a calmer "
              f"aggregate.")
    # Every replay states its own altitude coverage. "201 packs, no change" and
    # "201 packs, 71% of them below 10,000 ft" are different claims, and the
    # second one is the true one (#578).
    print()
    print(corpus.format_altitude_profile(packs, area=args.area))


if __name__ == "__main__":
    main()
