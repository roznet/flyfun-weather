#!/usr/bin/env python3
"""Build the dev labelling corpus from packs (#254).

Copies each selected pack's artifacts into ``$EVAL_CORPUS_DIR/<corpus_id>/`` and
writes an anonymized ``corpus_meta.json``. Existing golden labels
(``label.json``) are preserved across a re-pull. The heavy artifacts are
gitignored; only ``corpus_meta.json`` + ``label.json`` are committed.

This is the dev side of the prod->dev workflow: production only supplies the
data (a manifest from ``export_eval_candidates.py`` + the referenced pack dirs,
copied over once). All labelling lives on dev.

Usage:
    source venv/bin/activate
    # from a manifest produced by export_eval_candidates.py
    python scripts/pull_eval_corpus.py --manifest eval_candidates.json
    # or ingest everything labelable under a packs dir
    python scripts/pull_eval_corpus.py --from data/packs
    # or a single pack
    python scripts/pull_eval_corpus.py --pack data/packs/<user>/<flight>/<ts>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from weatherbrief.eval_workbench.config import eval_corpus_dir  # noqa: E402
from weatherbrief.eval_workbench.ingest import ingest_pack  # noqa: E402


def _pack_dirs(args) -> list[Path]:
    if args.pack:
        return [Path(args.pack)]
    if args.manifest:
        manifest = json.loads(Path(args.manifest).read_text())
        dirs = []
        for entry in manifest.get("packs", []):
            p = entry.get("pack_path")
            if not p:
                print(f"  ! manifest entry {entry.get('corpus_id')} has no pack_path")
                continue
            dirs.append(Path(p))
        return dirs
    # --from <packs_dir>
    packs_dir = Path(args.packs_dir)
    return sorted(
        p.parent for p in packs_dir.rglob("digest.json")
        if (p.parent / "briefing.json").exists()
        and (p.parent / "forecasts.json").exists()
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the dev labelling corpus")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--manifest", help="Candidate manifest from export_eval_candidates.py")
    src.add_argument("--pack", help="A single pack directory")
    ap.add_argument("--from", dest="packs_dir", default="data/packs",
                    help="Packs dir to ingest from when no manifest/pack given")
    ap.add_argument("--no-copy", action="store_true",
                    help="Write corpus_meta only, don't copy artifacts (testing)")
    ap.add_argument("--area", choices=("staging", "corpus"), default="staging",
                    help="Target area (default: staging — new briefings triage there)")
    args = ap.parse_args()

    from weatherbrief.eval_workbench.config import area_root

    target = area_root(args.area)
    pack_dirs = _pack_dirs(args)
    print(f"Ingesting {len(pack_dirs)} pack(s) into {target} [{args.area}] ...")

    ingested = 0
    skipped = 0
    for pd in pack_dirs:
        if not pd.exists():
            print(f"  ! missing: {pd}")
            skipped += 1
            continue
        try:
            cp = ingest_pack(pd, copy=not args.no_copy, area=args.area)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! failed {pd.name}: {exc}")
            skipped += 1
            continue
        if cp is None:
            skipped += 1
            continue
        ingested += 1
        tag = "labelled" if cp.is_labeled else "unlabelled"
        print(f"  + {cp.corpus_id}  [{tag}]  {', '.join(cp.meta.situations) or '-'}")

    print(f"\nIngested {ingested}, skipped {skipped}.")
    print(f"Area [{args.area}]: {target}")
    print("Label with the workbench: set WEATHERBRIEF_EVAL_WORKBENCH=1, start the "
          "dev server, open /eval.html")
    if args.area == "staging":
        print("These land in STAGING — triage/label there, then Promote to the "
              "corpus from the eval page.")
    else:
        print("\nWhen happy with the corpus shape, commit in the eval-set repo "
              "(payloads via LFS):\n  git add corpus/ && git commit && git push")


if __name__ == "__main__":
    main()
