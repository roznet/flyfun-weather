#!/usr/bin/env python3
"""Extract LLM digest eval dataset from existing pack data.

For each pack that has both a digest.json and the input data needed to
reconstruct the LLM context string, this script saves a compact fixture
containing:
  - context.txt  — the exact string sent to the LLM as the user message
  - digest.json  — the LLM's structured output (for comparison)
  - meta.json    — route, date, days_out, advisory summary, model counts

Usage:
    python scripts/extract_digest_eval.py [--output-dir tests/eval_data/digests]
    python scripts/extract_digest_eval.py --list          # just list, don't extract
    python scripts/extract_digest_eval.py --dedupe        # one per flight (latest)
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from weatherbrief.digest.prompt_builder import build_digest_context
from weatherbrief.models import ForecastSnapshot, RouteAdvisoriesManifest


PACKS_DIR = Path("data/packs")
DEFAULT_OUTPUT = Path("tests/eval_data/digests")


def load_snapshot_from_pack(pack_dir: Path) -> ForecastSnapshot | None:
    """Load a ForecastSnapshot from a pack directory."""
    briefing_path = pack_dir / "briefing.json"
    forecasts_path = pack_dir / "forecasts.json"
    if not briefing_path.exists() or not forecasts_path.exists():
        return None

    briefing = json.loads(briefing_path.read_text())
    forecasts = json.loads(forecasts_path.read_text())
    briefing["forecasts"] = forecasts.get("forecasts", [])
    return ForecastSnapshot.model_validate(briefing)


def extract_advisory_summary(adv_path: Path) -> dict:
    """Extract a compact advisory summary for meta.json."""
    if not adv_path.exists():
        return {"has_advisories": False}

    advs = json.loads(adv_path.read_text())
    summary = {"has_advisories": True, "advisories": []}

    for adv in advs.get("advisories", []):
        aid = adv.get("advisory_id", "")
        if aid == "model_agreement":
            continue
        model_statuses = defaultdict(int)
        for m in adv.get("per_model", []):
            model_statuses[m.get("status", "unknown")] += 1
        summary["advisories"].append({
            "id": aid,
            "aggregate": adv.get("aggregate_status", "unknown"),
            "models": dict(model_statuses),
        })

    green = sum(1 for a in summary["advisories"] if a["aggregate"] == "green")
    amber = sum(1 for a in summary["advisories"] if a["aggregate"] == "amber")
    red = sum(1 for a in summary["advisories"] if a["aggregate"] == "red")
    summary["counts"] = {"green": green, "amber": amber, "red": red}

    return summary


def find_packs() -> list[Path]:
    """Find all pack directories that have digest.json."""
    return sorted(
        p.parent for p in PACKS_DIR.rglob("digest.json")
        if (p.parent / "briefing.json").exists()
        and (p.parent / "forecasts.json").exists()
    )


def extract_one(pack_dir: Path) -> dict | None:
    """Extract eval data from a single pack. Returns meta dict or None."""
    snapshot = load_snapshot_from_pack(pack_dir)
    if snapshot is None:
        return None

    adv_path = pack_dir / "route_advisories.json"
    advisories = None
    if adv_path.exists():
        advisories = RouteAdvisoriesManifest.model_validate(
            json.loads(adv_path.read_text())
        )

    # Determine target time
    if snapshot.departure_time and isinstance(snapshot.departure_time, datetime):
        target_time = snapshot.departure_time
    else:
        target_time = datetime.fromisoformat(f"{snapshot.target_date}T09:00:00")

    # Build context string (the exact LLM input)
    context = build_digest_context(
        snapshot, target_time,
        route_advisories=advisories,
        flight_rules="vfr_ifr",
    )

    # Load existing digest output
    digest = json.loads((pack_dir / "digest.json").read_text())

    # Build metadata
    route = " -> ".join(wp.icao for wp in snapshot.route.waypoints)
    adv_summary = extract_advisory_summary(adv_path)

    meta = {
        "pack_path": str(pack_dir),
        "route": route,
        "target_date": snapshot.target_date,
        "fetch_date": snapshot.fetch_date,
        "days_out": snapshot.days_out,
        "assessment": digest.get("assessment"),
        "assessment_reason": digest.get("assessment_reason"),
        "advisory_summary": adv_summary,
        "context_chars": len(context),
    }

    return {"context": context, "digest": digest, "meta": meta}


def make_fixture_id(meta: dict) -> str:
    """Generate a compact fixture directory name."""
    route = meta["route"].replace(" -> ", "_").lower()
    date = meta["target_date"]
    days = f"d{meta['days_out']}"
    fetch = meta["fetch_date"]
    return f"{route}_{date}_{days}_{fetch}"


def main():
    parser = argparse.ArgumentParser(description="Extract digest eval dataset")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--list", action="store_true", help="List packs, don't extract")
    parser.add_argument(
        "--dedupe", action="store_true",
        help="Keep only one pack per (flight, days_out) — the latest fetch",
    )
    parser.add_argument(
        "--prune", action="store_true",
        help="Remove redundant same-assessment fixtures (keep endpoints + transitions)",
    )
    args = parser.parse_args()

    packs = find_packs()
    print(f"Found {len(packs)} packs with digest + input data")

    if args.list:
        for p in packs:
            digest = json.loads((p / "digest.json").read_text())
            adv_path = p / "route_advisories.json"
            has_adv = "+" if adv_path.exists() else "-"
            print(f"  [{digest.get('assessment', '?'):5}] {has_adv}adv  {p.relative_to(Path.cwd())}")
        return

    # Extract all
    results = []
    errors = 0
    for pack in packs:
        try:
            data = extract_one(pack)
            if data:
                results.append(data)
        except Exception as e:
            errors += 1
            print(f"  SKIP {pack.name}: {e}")

    print(f"Extracted {len(results)} fixtures ({errors} errors)")

    # Dedupe: keep latest fetch per (route, target_date, days_out)
    if args.dedupe:
        by_key: dict[str, dict] = {}
        for r in results:
            m = r["meta"]
            key = f"{m['route']}_{m['target_date']}_d{m['days_out']}"
            existing = by_key.get(key)
            if existing is None or m["fetch_date"] > existing["meta"]["fetch_date"]:
                by_key[key] = r
        results = list(by_key.values())
        print(f"After dedup: {len(results)} unique (route, date, days_out)")

    # Prune redundant fixtures in two passes:
    # 1) Per flight (route+target_date): keep all if assessments vary,
    #    otherwise keep only endpoints (min/max days_out)
    # 2) Per route across flights: if multiple same-assessment flights,
    #    keep only the most interesting one (most advisories or unique features)
    if args.prune:
        # Pass 1: per-flight pruning
        by_flight: dict[str, list[dict]] = defaultdict(list)
        for r in results:
            m = r["meta"]
            flight_key = f"{m['route']}_{m['target_date']}"
            by_flight[flight_key].append(r)

        after_flight: list[dict] = []
        for flight_key, group in sorted(by_flight.items()):
            assessments = {r["meta"]["assessment"] for r in group}
            if len(assessments) > 1 or len(group) <= 2:
                after_flight.extend(group)
            else:
                group.sort(key=lambda r: r["meta"]["days_out"])
                after_flight.append(group[0])
                if len(group) > 1:
                    after_flight.append(group[-1])

        # Pass 2: per-route pruning across flights
        by_route: dict[str, list[dict]] = defaultdict(list)
        for r in after_flight:
            by_route[r["meta"]["route"]].append(r)

        pruned: list[dict] = []
        for route, group in sorted(by_route.items()):
            assessments = {r["meta"]["assessment"] for r in group}
            if len(assessments) > 1:
                # Route has assessment variation — keep all
                pruned.extend(group)
            elif len(group) <= 2:
                pruned.extend(group)
            else:
                # Multiple same-assessment flights for this route —
                # keep the one with advisories (prefer most advisory diversity),
                # plus one without advisories if it exists (different context shape)
                with_adv = [r for r in group if r["meta"]["advisory_summary"].get("has_advisories")]
                without_adv = [r for r in group if not r["meta"]["advisory_summary"].get("has_advisories")]

                if with_adv:
                    # Pick the one with most non-green advisories (most interesting)
                    with_adv.sort(
                        key=lambda r: r["meta"]["advisory_summary"].get("counts", {}).get("amber", 0)
                        + r["meta"]["advisory_summary"].get("counts", {}).get("red", 0),
                        reverse=True,
                    )
                    pruned.append(with_adv[0])
                if without_adv:
                    pruned.append(without_adv[0])
                if not with_adv and not without_adv:
                    pruned.append(group[0])

        before = len(results)
        results = pruned
        print(f"After prune: {len(results)} fixtures (from {before})")

    # Write output
    if args.output_dir.exists():
        shutil.rmtree(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Write index
    index = []
    for r in sorted(results, key=lambda x: (x["meta"]["route"], x["meta"]["target_date"])):
        fid = make_fixture_id(r["meta"])
        fixture_dir = args.output_dir / fid
        fixture_dir.mkdir(parents=True, exist_ok=True)

        (fixture_dir / "context.txt").write_text(r["context"])
        (fixture_dir / "digest.json").write_text(
            json.dumps(r["digest"], indent=2, ensure_ascii=False)
        )
        (fixture_dir / "meta.json").write_text(
            json.dumps(r["meta"], indent=2, ensure_ascii=False)
        )

        idx_entry = {
            "id": fid,
            "assessment": r["meta"]["assessment"],
            "route": r["meta"]["route"],
            "days_out": r["meta"]["days_out"],
            "has_advisories": r["meta"]["advisory_summary"].get("has_advisories", False),
            "advisory_counts": r["meta"]["advisory_summary"].get("counts", {}),
            "context_chars": r["meta"]["context_chars"],
        }
        index.append(idx_entry)

    (args.output_dir / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False)
    )

    # Print summary
    from collections import Counter
    assess_counts = Counter(r["meta"]["assessment"] for r in results)
    total_kb = sum(r["meta"]["context_chars"] for r in results) / 1024
    print(f"\nDataset written to {args.output_dir}/")
    print(f"  {len(results)} fixtures, {total_kb:.0f}KB total context")
    print(f"  Assessment distribution: {dict(assess_counts)}")


if __name__ == "__main__":
    main()
