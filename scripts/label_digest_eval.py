#!/usr/bin/env python3
"""Interactive golden-labelling tool for the LLM digest eval set.

A fixture's ``digest.json`` is only what the model *did* output — possibly
wrong. A **golden label** is the *correct* assessment a human (the SME) assigns
to the fixture, per guidance preset, plus a one-line rationale. The golden set
is what the eval scores against to measure correctness (not just self-
consistency) and to gate model/prompt changes (#252 / #254).

This walks fixtures under ``tests/eval_data/digests/``, shows the route, the
non-green advisories, the model's digest, and (on demand) the full context,
then prompts you for the golden assessments + rationale. It writes ``golden``
and ``curated: true`` into the fixture's ``meta.json`` so the label survives a
re-extraction (``extract_digest_eval.py`` preserves curated fixtures).

Usage:
    source venv/bin/activate
    # label all icing fixtures that aren't labelled yet
    python scripts/label_digest_eval.py --situation icing --unlabeled

    # label a specific fixture / a subset
    python scripts/label_digest_eval.py --filter egtf_lfat
    python scripts/label_digest_eval.py --assessment RED

    # just list candidates + their label status, no prompting
    python scripts/label_digest_eval.py --situation icing --list

At each fixture, enter the assessments as one to three letters from {G,A,R}:
    "A"       -> AMBER for all three guidance presets
    "R A A"   -> conservative=RED, balanced=AMBER, tolerant=AMBER
Other keys: [v]iew full context, [s]kip, [q]uit (saves nothing for current).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

EVAL_DIR = Path("tests/eval_data/digests")
GUIDANCES = ("conservative", "balanced", "tolerant")
_LETTER = {"G": "GREEN", "A": "AMBER", "R": "RED"}
PROSE_FIELDS = (
    "assessment_reason", "synoptic", "specific_concerns", "trend", "watch_items",
)


def _load(d: Path) -> tuple[dict, dict, str]:
    digest = json.loads((d / "digest.json").read_text(encoding="utf-8"))
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    ctx_path = d / "context.txt"
    context = ctx_path.read_text(encoding="utf-8") if ctx_path.exists() else ""
    return digest, meta, context


def _candidates(args) -> list[Path]:
    if not EVAL_DIR.exists():
        return []
    out = []
    for child in sorted(EVAL_DIR.iterdir()):
        if not child.is_dir() or not (child / "meta.json").exists():
            continue
        meta = json.loads((child / "meta.json").read_text(encoding="utf-8"))
        if args.situation and args.situation not in meta.get("situations", []):
            continue
        if args.assessment and meta.get("assessment") != args.assessment.upper():
            continue
        if args.filter and args.filter not in child.name:
            continue
        if args.unlabeled and meta.get("golden"):
            continue
        out.append(child)
    return out


def _print_fixture(d: Path, digest: dict, meta: dict) -> None:
    adv = meta.get("advisory_summary", {})
    counts = adv.get("counts", {})
    faithful = "faithful" if meta.get("faithful") else "RECONSTRUCTED"
    print("\n" + "=" * 78)
    print(f"{d.name}")
    print(f"  Route: {meta.get('route')}   D-{meta.get('days_out')}   "
          f"target {meta.get('target_date')}   [{faithful}]")
    print(f"  Situations: {', '.join(meta.get('situations', [])) or '-'}")
    if counts:
        print(f"  Advisories: G={counts.get('green',0)} "
              f"A={counts.get('amber',0)} R={counts.get('red',0)}")
        for a in adv.get("advisories", []):
            if a.get("aggregate") != "green":
                models = " ".join(f"{k}={v}" for k, v in a.get("models", {}).items())
                print(f"    [{a['aggregate'].upper():5}] {a['id']:16} {models}")
    print(f"\n  --- MODEL DIGEST (what it produced — NOT ground truth) ---")
    print(f"  assessment: {digest.get('assessment')}")
    for f in PROSE_FIELDS:
        v = str(digest.get(f) or "").strip()
        if v:
            print(f"  {f}: {v}")
    existing = meta.get("golden")
    if existing:
        print(f"\n  >>> EXISTING golden label: {existing.get('assessments')}  "
              f"({existing.get('rationale','')})")


def _parse_assessments(raw: str) -> dict[str, str] | None:
    toks = raw.upper().split()
    if len(toks) == 1 and toks[0] in _LETTER:
        return {g: _LETTER[toks[0]] for g in GUIDANCES}
    if len(toks) == 3 and all(t in _LETTER for t in toks):
        return {g: _LETTER[t] for g, t in zip(GUIDANCES, toks)}
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Golden-label digest fixtures")
    parser.add_argument("--situation", help="Only fixtures tagged with this situation")
    parser.add_argument("--assessment", help="Only fixtures with this model assessment")
    parser.add_argument("--filter", help="Only fixtures whose id contains this substring")
    parser.add_argument("--unlabeled", action="store_true", help="Skip already-golden fixtures")
    parser.add_argument("--list", action="store_true", help="List candidates + status, no prompting")
    parser.add_argument("--by", default="", help="Label this 'labeled_by' name into the record")
    args = parser.parse_args()

    cands = _candidates(args)
    print(f"{len(cands)} candidate fixture(s)")
    if not cands:
        return

    if args.list:
        for d in cands:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            g = meta.get("golden")
            status = f"golden={g['assessments']}" if g else "UNLABELLED"
            print(f"  [{meta.get('assessment'):5}] {status:30} {d.name}")
        return

    labelled: list[Path] = []
    for i, d in enumerate(cands):
        digest, meta, context = _load(d)
        _print_fixture(d, digest, meta)
        while True:
            raw = input(
                f"\n[{i+1}/{len(cands)}] golden assessments "
                "(G/A/R, one for all or three=cons bal tol) | "
                "[v]iew context [s]kip [q]uit: "
            ).strip()
            if raw.lower() == "q":
                print("Quitting.")
                _summary(labelled)
                return
            if raw.lower() == "s":
                break
            if raw.lower() == "v":
                print("\n" + "-" * 78 + "\n" + context + "\n" + "-" * 78)
                continue
            parsed = _parse_assessments(raw)
            if not parsed:
                print("  ! enter one letter (e.g. 'A') or three (e.g. 'R A A'), "
                      "or v/s/q")
                continue
            rationale = input("  rationale (one line): ").strip()
            golden = {
                "assessments": parsed,
                "rationale": rationale,
                "labeled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            if args.by:
                golden["labeled_by"] = args.by
            meta["golden"] = golden
            meta["curated"] = True
            (d / "meta.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"  ✓ saved {parsed}")
            labelled.append(d)
            break

    _summary(labelled)


def _summary(labelled: list[Path]) -> None:
    if not labelled:
        print("\nNo fixtures labelled.")
        return
    print(f"\nLabelled {len(labelled)} fixture(s). They are gitignored — stage with:")
    print("  git add -f " + " ".join(str(d) for d in labelled))


if __name__ == "__main__":
    main()
