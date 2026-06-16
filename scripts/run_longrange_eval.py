#!/usr/bin/env python3
"""Long-range outlook eval — haiku vs sonnet on the SAME long-range context.

Unlike ``run_digest_eval.py`` (which replays a saved context through one model
and compares the new GREEN/AMBER/RED assessment against the original), this
harness targets the long-range regime (>7 days): it builds the trimmed
long-range context for each far-out pack and runs it through BOTH the cheap
long-range model (haiku) and sonnet with the long-range prompt + LongRangeDigest
schema, then reports whether haiku's *outlook* tracks sonnet's. The question is
"is haiku good enough to replace sonnet for the early outlook?", not "did the
assessment change".

Usage:
    python scripts/run_longrange_eval.py --dry-run            # discover, no LLM
    python scripts/run_longrange_eval.py --limit 8 --output lr_eval.json
    python scripts/run_longrange_eval.py --max-days 10        # bookable zone only
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_digest_eval import load_snapshot_from_pack  # noqa: E402

from weatherbrief.digest.llm_config import (  # noqa: E402
    LLMConfig,
    create_chat_model,
    load_digest_config,
)
from weatherbrief.digest.llm_digest import (  # noqa: E402
    LongRangeDigest,
    build_confidence_note,
)
from weatherbrief.digest.prompt_builder import build_digest_context  # noqa: E402
from weatherbrief.models import RouteAdvisoriesManifest  # noqa: E402

PACKS_DIR = Path("data/packs")
SONNET = LLMConfig(provider="anthropic", model="claude-sonnet-4-6", temperature=0.0)


def _target_time(snapshot) -> datetime:
    if snapshot.departure_time and isinstance(snapshot.departure_time, datetime):
        return snapshot.departure_time
    return datetime.fromisoformat(f"{snapshot.target_date}T09:00:00")


def discover(min_days: int, max_days: int) -> list:
    """Long-range packs with the files needed to rebuild context, deduped to
    the latest fetch per (route, target_date, days_out)."""
    by_key: dict[str, dict] = {}
    for dj in PACKS_DIR.rglob("digest.json"):
        d = dj.parent
        if not (d / "briefing.json").exists() or not (d / "forecasts.json").exists():
            continue
        try:
            snap = load_snapshot_from_pack(d)
        except Exception:
            continue
        if snap is None or not (min_days <= snap.days_out <= max_days):
            continue
        route = " -> ".join(wp.icao for wp in snap.route.waypoints)
        key = f"{route}|{snap.target_date}|{snap.days_out}"
        prev = by_key.get(key)
        if prev is None or snap.fetch_date > prev["snap"].fetch_date:
            by_key[key] = {"dir": d, "snap": snap, "route": route}
    return sorted(by_key.values(), key=lambda r: (r["snap"].days_out, r["route"]))


def build_ctx(rec: dict) -> tuple[str, list[str]]:
    snap = rec["snap"]
    tt = _target_time(snap)
    adv_path = rec["dir"] / "route_advisories.json"
    advisories = None
    if adv_path.exists():
        advisories = RouteAdvisoriesManifest.model_validate(
            json.loads(adv_path.read_text())
        )
    note = build_confidence_note(snap, tt)
    ctx = build_digest_context(
        snap, tt, route_advisories=advisories, flight_rules="vfr_ifr",
        longrange=True, confidence_note=note,
    )
    models = sorted({wf.model.value for wf in snap.forecasts})
    return ctx, models


def run_model(llmcfg: LLMConfig, system_prompt: str, ctx: str) -> LongRangeDigest:
    llm = create_chat_model(llmcfg).with_structured_output(LongRangeDigest)
    return llm.invoke(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": ctx}]
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-days", type=int, default=8)
    ap.add_argument("--max-days", type=int, default=14)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--output", type=Path, default=None)
    args = ap.parse_args()

    recs = discover(args.min_days, args.max_days)
    print(f"Discovered {len(recs)} unique long-range packs "
          f"(D-{args.min_days}..D-{args.max_days})")
    dist: dict[int, int] = defaultdict(int)
    for r in recs:
        dist[r["snap"].days_out] += 1
    print("days_out:", {k: dist[k] for k in sorted(dist)})

    if args.limit:
        recs = recs[: args.limit]
    if args.dry_run:
        for r in recs:
            ctx, models = build_ctx(r)
            print(f"  D-{r['snap'].days_out:<2} {r['route']:<45} "
                  f"models={','.join(models):<30} ctx={len(ctx)}c")
        return

    cfg = load_digest_config("default")
    sys_prompt = cfg.load_prompt("briefer_longrange", locale="en")

    results = []
    agree = 0
    for i, r in enumerate(recs, 1):
        ctx, models = build_ctx(r)
        snap = r["snap"]
        h = run_model(cfg.longrange, sys_prompt, ctx)
        s = run_model(SONNET, sys_prompt, ctx)
        match = h.outlook == s.outlook
        agree += match
        print(f"[{i}/{len(recs)}] D-{snap.days_out} {r['route']} "
              f"({','.join(models)})")
        print(f"    haiku : {h.outlook:<18} | {h.outlook_reason}")
        print(f"    sonnet: {s.outlook:<18} | {s.outlook_reason}")
        print(f"    {'AGREE' if match else 'DIFFER <<<'}")
        results.append({
            "route": r["route"], "target_date": snap.target_date,
            "days_out": snap.days_out, "models": models,
            "haiku": h.model_dump(), "sonnet": s.model_dump(), "match": match,
        })

    n = len(results)
    print(f"\n=== outlook agreement: {agree}/{n} "
          f"({100*agree//n if n else 0}%) ===")
    for label in ("haiku", "sonnet"):
        d: dict[str, int] = defaultdict(int)
        for r in results:
            d[r[label]["outlook"]] += 1
        print(f"  {label}: {dict(d)}")
    if args.output:
        args.output.write_text(json.dumps(results, indent=2))
        print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
