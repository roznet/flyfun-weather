#!/usr/bin/env python3
"""Select interesting packs for golden labelling (#254).

Scans a packs directory, tags each pack by meteorological situation, and writes
a manifest of the most valuable label candidates — coverage-cell fillers first,
then the AMBER-bias / red-flexibility / trend cases (see
``weatherbrief.eval_workbench.candidates``).

Run this where the packs live (prod or a local DATA_DIR), copy the manifest +
referenced packs to dev, then build the corpus with ``pull_eval_corpus.py``.

Usage:
    source venv/bin/activate
    python scripts/export_eval_candidates.py --from data/packs --limit 40 \
        --output eval_candidates.json
    python scripts/export_eval_candidates.py --from data/packs --list
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from weatherbrief.eval_workbench.candidates import select_candidates  # noqa: E402
from weatherbrief.eval_workbench.ingest import build_corpus_meta  # noqa: E402
from weatherbrief.eval_workbench.situations import SITUATION_VOCAB  # noqa: E402


def find_packs(packs_dir: Path) -> list[Path]:
    """Pack dirs that have the files needed to label them."""
    return sorted(
        p.parent for p in packs_dir.rglob("digest.json")
        if (p.parent / "briefing.json").exists()
        and (p.parent / "forecasts.json").exists()
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Select golden-label candidates")
    ap.add_argument("--from", dest="packs_dir", default="data/packs",
                    help="Directory of packs to scan (default: data/packs)")
    ap.add_argument("--limit", type=int, default=40, help="Max candidates")
    ap.add_argument("--min-per-cell", type=int, default=1,
                    help="Target labelled packs per situation cell")
    ap.add_argument("--output", default="eval_candidates.json",
                    help="Manifest output path")
    ap.add_argument("--list", action="store_true",
                    help="Print the selection, don't write a manifest")
    args = ap.parse_args()

    packs_dir = Path(args.packs_dir)
    packs = find_packs(packs_dir)
    print(f"Scanning {len(packs)} packs under {packs_dir} ...")

    metas = []
    path_by_id: dict[str, str] = {}
    for pd in packs:
        try:
            meta = build_corpus_meta(pd)
        except Exception as exc:  # noqa: BLE001 — one bad pack shouldn't abort
            print(f"  ! skip {pd.name}: {exc}")
            continue
        if meta is None:
            continue
        metas.append(meta)
        path_by_id[meta.corpus_id] = str(pd)

    print(f"{len(metas)} labelable packs (skipped long-range / unbuildable)")
    selected = select_candidates(
        metas, limit=args.limit, min_per_cell=args.min_per_cell
    )
    for s in selected:
        s["pack_path"] = path_by_id.get(s["corpus_id"], "")

    # Coverage summary over the matrix.
    covered: Counter[str] = Counter()
    for s in selected:
        covered.update(s["situations"])
    print(f"\nSelected {len(selected)} candidates. Coverage:")
    for cell in SITUATION_VOCAB:
        flag = " " if covered[cell] >= args.min_per_cell else "!"
        print(f"  [{flag}] {cell:22} x{covered[cell]}")

    print("\nTop candidates:")
    for s in selected:
        reasons = ",".join(s["reasons"]) or "-"
        print(f"  {s['score']:>3}  [{(s['assessment'] or '-'):5}] "
              f"{s['route']:38} d{s['days_out']:<2} {reasons}")

    if args.list:
        return

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_packs_dir": str(packs_dir),
        "count": len(selected),
        "packs": selected,
    }
    Path(args.output).write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nWrote {len(selected)} candidates -> {args.output}")
    print("Next: copy the manifest + referenced packs to dev, then run "
          "scripts/pull_eval_corpus.py --manifest " + args.output)


if __name__ == "__main__":
    main()
