#!/usr/bin/env python3
"""Recompute CAT layers (Ri + E-Shear) from stored packs, and diff two runs (#861).

A layer-grouping change moves layer edges without necessarily moving a grade,
so the advisory diff (``rerun_advisories_diff.py --deep``) can read "no change"
while the cross-section and per-altitude rows did change. This dumps the CAT
layers the CURRENT checkout computes for each pack, so running it once on
``main`` and once on a branch, then ``--compare``, isolates exactly what the
branch changed (the saved ``route_analyses.json`` also carries every earlier
code change since the pack was built, so diffing against it is noisier).

Needs packs that still carry ``cross_section.json`` + ``route_points.json``
(pull them fresh: ``scripts/ops/fetch_pack.py --prod <flight>``, or the
staging corpus). Never modifies a pack.

Usage:
    git checkout main
    python scripts/replay_cat_layers.py --area staging --out /tmp/cat_main.json
    git checkout <branch>
    python scripts/replay_cat_layers.py --area staging --out /tmp/cat_branch.json
    python scripts/replay_cat_layers.py --compare /tmp/cat_main.json /tmp/cat_branch.json

    # or explicit pack directories instead of --area
    python scripts/replay_cat_layers.py PACK_DIR [PACK_DIR ...] --out /tmp/cat.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _layers(layers) -> list[list]:
    return [
        [round(la.base_ft), round(la.top_ft), la.risk.value,
         la.base_pressure_hpa, la.top_pressure_hpa]
        for la in layers
    ]


def dump_pack(pack_dir: Path) -> dict:
    """CAT layers per point and model, as computed by this checkout."""
    from weatherbrief.eval_workbench.situations import load_snapshot_from_pack
    from weatherbrief.tasks.analyze import run_analysis_from_pack

    snap = load_snapshot_from_pack(pack_dir)
    if snap is None:
        raise RuntimeError("no snapshot")
    analysis = run_analysis_from_pack(pack_dir, snap.route, snap.departure_time)
    manifest = analysis.route_analyses_manifest
    if manifest is None:
        raise RuntimeError("analysis produced no manifest")

    points: dict[str, dict] = {}
    for rpa in manifest.analyses:
        for model, snd in rpa.sounding.items():
            vm = snd.vertical_motion
            if vm is None:
                continue
            points[f"{rpa.point_index}/{model}"] = {
                "ri": _layers(vm.cat_risk_layers),
                "e_shear": _layers(vm.e_shear_layers),
            }
    return {"cruise_ft": manifest.cruise_altitude_ft, "points": points}


def _covers(layers: list[list], alt_ft: float) -> bool:
    return any(base <= alt_ft <= top for base, top, *_ in layers)


def compare(a_path: Path, b_path: Path) -> int:
    a = json.loads(a_path.read_text())
    b = json.loads(b_path.read_text())
    totals = {"packs": 0, "packs_changed": 0, "soundings_changed": 0,
              "cruise_newly_in_layer": 0, "cruise_no_longer_in_layer": 0}
    for pack, a_pack in sorted(a.items()):
        b_pack = b.get(pack)
        if b_pack is None or "error" in a_pack or "error" in b_pack:
            print(f"{pack}: skipped ({a_pack.get('error') or (b_pack or {}).get('error') or 'missing in B'})")
            continue
        totals["packs"] += 1
        cruise = a_pack["cruise_ft"]
        lines = []
        for key, a_pt in sorted(a_pack["points"].items()):
            b_pt = b_pack["points"].get(key, {"ri": [], "e_shear": []})
            for kind in ("ri", "e_shear"):
                if a_pt[kind] == b_pt[kind]:
                    continue
                totals["soundings_changed"] += 1
                was, now = _covers(a_pt[kind], cruise), _covers(b_pt[kind], cruise)
                flag = ""
                if now and not was:
                    totals["cruise_newly_in_layer"] += 1
                    flag = f"  ← cruise {cruise} ft now inside"
                elif was and not now:
                    totals["cruise_no_longer_in_layer"] += 1
                    flag = f"  ← cruise {cruise} ft no longer inside"
                lines.append(f"  {key} {kind}: {a_pt[kind]} → {b_pt[kind]}{flag}")
        if lines:
            totals["packs_changed"] += 1
            print(f"{pack} (cruise {cruise} ft)")
            print("\n".join(lines))
    print(json.dumps(totals, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("packs", nargs="*", type=Path, help="Pack directories")
    ap.add_argument("--area", choices=("staging", "corpus"),
                    help="Every pack in an eval-workbench area")
    ap.add_argument("--out", type=Path, help="Write the dump here (JSON)")
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("A", "B"),
                    help="Diff two dumps (A = baseline, B = candidate)")
    args = ap.parse_args()

    if args.compare:
        return compare(*args.compare)

    pack_dirs = list(args.packs)
    if args.area:
        from weatherbrief.eval_workbench import corpus
        pack_dirs += [corpus.pack_path(p.corpus_id, args.area)
                      for p in corpus.list_corpus(args.area)]
    if not pack_dirs or not args.out:
        ap.error("give pack dirs or --area, and --out")

    dump: dict[str, dict] = {}
    for pack_dir in pack_dirs:
        try:
            dump[str(pack_dir)] = dump_pack(pack_dir)
            print(f"ok   {pack_dir}", file=sys.stderr)
        except Exception as exc:  # report and keep going over the batch
            dump[str(pack_dir)] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"fail {pack_dir}: {exc}", file=sys.stderr)
    args.out.write_text(json.dumps(dump, indent=1))
    print(f"wrote {args.out} ({len(dump)} packs)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
