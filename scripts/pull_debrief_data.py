#!/usr/bin/env python3
"""Attach pilot debrief data (ground truth) to eval packs (#252).

Prod's ``flight_debriefs`` table holds the pilot's post-flight judgement
(flown / cancelled / monitoring + condition tags + note) — the strongest ground
truth the eval has. This dumps those rows from prod and, for every eval pack
whose flight has a debrief, writes a ``debrief.json`` sidecar and flips
``debriefed`` / ``debrief_decision`` in corpus_meta.

A pack's flight id comes from its ``_source.json`` breadcrumb (the flight dir
segment of the prod path).

Usage:
    python scripts/pull_debrief_data.py                 # both areas, query prod
    python scripts/pull_debrief_data.py --from-json tmp/prod_debriefs.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from weatherbrief.eval_workbench import corpus  # noqa: E402

PROD = "brice@161.35.35.15"


def dump_prod_debriefs() -> list[dict]:
    """SSH prod and dump flight_debriefs as JSON via the app container engine."""
    remote = (
        "cd flyfun-weather && docker compose exec -T weatherbrief python -c "
        "\"import json;from flyfun_common.db import get_engine;from sqlalchemy import text;"
        "e=get_engine();c=e.connect();"
        "rows=c.execute(text('SELECT flight_id,decision,reasons_json,outcomes_json,note FROM flight_debriefs')).mappings().all();"
        "print('JSON_START');print(json.dumps([dict(r) for r in rows]));print('JSON_END')\""
    )
    out = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=15", PROD, remote],
        check=True, capture_output=True, text=True, timeout=120,
    ).stdout
    cap, buf = False, []
    for line in out.splitlines():
        if "JSON_START" in line:
            cap = True
            continue
        if "JSON_END" in line:
            break
        if cap:
            buf.append(line)
    return json.loads("".join(buf))


def flight_id_from_source(source_pack_dir: str) -> str | None:
    """Extract the prod flight id from a pack's source path."""
    p = Path(source_pack_dir)
    if "__" in p.name:  # temp prod-pull layout: <user>__<flight>__<ts>
        parts = p.name.split("__")
        if len(parts) >= 3:
            return parts[-2]
    parts = p.parts  # .../packs/<user>/<flight>/<ts>
    if "packs" in parts:
        i = parts.index("packs")
        if len(parts) > i + 2:
            return parts[i + 2]
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Attach debrief ground truth to eval packs")
    ap.add_argument("--from-json", help="Use a saved debrief dump instead of querying prod")
    args = ap.parse_args()

    if args.from_json:
        debriefs = json.loads(Path(args.from_json).read_text())
    else:
        debriefs = dump_prod_debriefs()
    by_flight = {d["flight_id"]: d for d in debriefs}
    print(f"{len(by_flight)} debriefs available.\n")

    attached = no_source = no_match = 0
    for area in ("staging", "corpus"):
        for p in corpus.list_corpus(area):
            pd = corpus.pack_path(p.corpus_id, area)
            sc = pd / "_source.json"
            if not sc.exists():
                no_source += 1
                continue
            fid = flight_id_from_source(json.loads(sc.read_text()).get("source_pack_dir", ""))
            d = by_flight.get(fid) if fid else None
            if not d:
                no_match += 1
                continue
            # Write the full debrief sidecar (parse the json blobs for convenience).
            rec = {
                "flight_id": fid,
                "decision": d["decision"],
                "reasons": json.loads(d["reasons_json"]) if d.get("reasons_json") else None,
                "outcomes": json.loads(d["outcomes_json"]) if d.get("outcomes_json") else None,
                "note": d.get("note") or "",
            }
            (pd / "debrief.json").write_text(
                json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            graded = bool(rec["reasons"] or rec["outcomes"])
            meta = corpus.load_corpus_meta(p.corpus_id, area)
            meta.debriefed = True
            meta.debrief_decision = d["decision"]
            meta.debrief_graded = graded
            corpus.save_corpus_meta(meta, area)
            attached += 1
            star = " ★graded" if graded else ""
            print(f"  ✓ [{area}] {p.corpus_id} — {d['decision']}{star}")

    print(f"\nAttached {attached} debriefs. ({no_match} packs no debrief, {no_source} no source.)")


if __name__ == "__main__":
    main()
