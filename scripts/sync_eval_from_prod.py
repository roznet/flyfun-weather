#!/usr/bin/env python3
"""Re-pull missing heavy artifacts for eval packs from production (#252).

The local ``data/packs`` is often a partial sync (no cross_section / route_analyses
/ elevation), so corpus packs built from it are "label-only". Prod keeps the full
pack, so this rsyncs the missing heavy files back from prod into the corpus pack,
then gzips the cross-section (the committed master).

Source path per pack comes from the ``_source.json`` breadcrumb written at ingest,
or from a candidate ``--manifest`` (for packs ingested before that existed). The
prod path is the same ``packs/<user>/<flight>/<ts>`` tail under the prod data dir.

Usage:
    python scripts/sync_eval_from_prod.py --area staging          # all missing
    python scripts/sync_eval_from_prod.py --area staging --manifest eval_candidates.json
    python scripts/sync_eval_from_prod.py --area staging --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from weatherbrief.eval_workbench import corpus  # noqa: E402
from weatherbrief.eval_workbench.ingest import compact_corpus_pack  # noqa: E402

PROD = "brice@161.35.35.15"
PROD_DATA = "/mnt/flyfun_data/weather/data"
# Heavy artifacts to recover (the derived/raw tier stripped from the local sync).
HEAVY = (
    "cross_section.json",
    "route_analyses.json",
    "elevation_profile.json",
    "route_points.json",
    "sounding_profiles.json.gz",
)


def _prod_tail(source_pack_dir: str) -> str | None:
    """Extract the ``packs/<user>/<flight>/<ts>`` tail from a local source path."""
    parts = Path(source_pack_dir).parts
    if "packs" not in parts:
        return None
    return "/".join(parts[parts.index("packs"):])


def _source_for(corpus_id: str, area: str, manifest: dict) -> str | None:
    pack_dir = corpus.pack_path(corpus_id, area)
    sidecar = pack_dir / "_source.json"
    if sidecar.exists():
        return json.loads(sidecar.read_text()).get("source_pack_dir")
    return manifest.get(corpus_id)


def main() -> None:
    ap = argparse.ArgumentParser(description="Re-pull missing heavy artifacts from prod")
    ap.add_argument("--area", choices=("staging", "corpus"), default="staging")
    ap.add_argument("--manifest", help="Candidate manifest (corpus_id -> source pack_path)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    manifest: dict[str, str] = {}
    if args.manifest:
        for e in json.loads(Path(args.manifest).read_text()).get("packs", []):
            manifest[e["corpus_id"]] = e.get("pack_path", "")

    recovered = skipped = failed = 0
    for p in corpus.list_corpus(args.area):
        pd = corpus.pack_path(p.corpus_id, args.area)
        if (pd / "cross_section.json").exists() or (pd / "cross_section.json.gz").exists():
            continue  # already full
        src = _source_for(p.corpus_id, args.area, manifest)
        tail = _prod_tail(src) if src else None
        if not tail:
            print(f"  ? {p.corpus_id}: no source path (skip)")
            skipped += 1
            continue
        remote = f"{PROD}:{PROD_DATA}/{tail}/"
        print(f"  ↓ {p.corpus_id}")
        if args.dry_run:
            print(f"      rsync {remote} -> {pd}/  (files: {', '.join(HEAVY)})")
            continue
        includes = []
        for f in HEAVY:
            includes += ["--include", f]
        cmd = ["rsync", "-az", "--ignore-existing", *includes, "--exclude", "*", remote, f"{pd}/"]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
        except subprocess.CalledProcessError as exc:
            print(f"      ! rsync failed: {exc.stderr.strip()[:120]}")
            failed += 1
            continue
        if not (pd / "cross_section.json").exists():
            print("      ! cross_section.json not on prod for this pack")
            failed += 1
            continue
        compact_corpus_pack(pd)  # gzip the recovered cross_section
        recovered += 1
        print("      ✓ recovered + compacted")

    print(f"\nRecovered {recovered}, skipped {skipped}, failed {failed}.")


if __name__ == "__main__":
    main()
