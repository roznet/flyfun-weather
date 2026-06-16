#!/usr/bin/env python3
"""Extract LLM digest eval dataset from existing pack data.

For each pack that has both a digest.json and the input data needed to
reconstruct the LLM context string, this script saves a compact fixture
containing:
  - context.txt  — the exact string sent to the LLM as the user message
  - digest.json  — the LLM's structured output (for comparison)
  - meta.json    — route, date, days_out, advisory summary, situations,
                   and fidelity (faithful vs reconstructed)

Context fidelity (#254): the LLM is sent more than the snapshot can rebuild —
text forecasts (NWS AFD / DWD), DWD translations, and the previous digest. So
we prefer the *persisted* ``digest_context.txt`` written into the pack at digest
generation time (byte-faithful). When that's absent (older back-catalog packs),
we reconstruct best-effort: rebuild the context from the snapshot and splice in
the DWD overview if the pack saved one. Reconstructed fixtures are tagged
``faithful=False`` because the text-forecast/previous-digest sections the LLM
actually saw cannot be fully recovered — the eval treats them more leniently.

Usage:
    python scripts/extract_digest_eval.py [--output-dir tests/eval_data/digests]
    python scripts/extract_digest_eval.py --list          # just list, don't extract
    python scripts/extract_digest_eval.py --dedupe        # one per flight (latest)
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from weatherbrief.digest.prompt_builder import build_digest_context
from weatherbrief.models import ForecastSnapshot, RouteAdvisoriesManifest


logger = logging.getLogger(__name__)

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


# Map an advisory_id to the situation tag it contributes when its aggregate is
# not green. Several ids fold into one tag (icing_escape + fiki_icing + freezing
# _level → "icing"). Unmapped ids are ignored for tagging.
_ADVISORY_SITUATION: dict[str, str] = {
    "icing_escape": "icing",
    "fiki_icing": "icing",
    "freezing_level": "icing",
    "convective": "convective",
    "turbulence": "turbulence",
    "mountain_wind": "turbulence",
    "cloud_top": "cloud",
    "vmc_cruise": "cloud",
    "airport_wind": "wind",
    "flight_category": "low_category",
    "vfr_feasibility": "vfr_marginal",
    "ifr_feasibility": "ifr_marginal",
}

# The coverage matrix we want the curated set to fill (parent #252 / #254).
# A fixture may carry several tags; these are the cells we report on.
SITUATION_VOCAB: tuple[str, ...] = (
    "all_green", "single_red", "multi_red",
    "icing", "convective", "icing_plus_convective", "cloud", "turbulence",
    "wind", "low_category", "vfr_marginal", "ifr_marginal",
    "d0", "metar", "sigmet", "previous_digest",
    "us_route", "alpine", "channel_crossing",
)


def _load_dwd_translated(pack_dir: Path) -> list | None:
    """Reconstruct the ``dwd_translated`` list from a saved ``dwd_overview.json``.

    Returns ``[(DWDDayBlock, english), ...]`` so a reconstructed context can
    splice the DWD section back in, or ``None`` if the pack has no overview.
    """
    overview_path = pack_dir / "dwd_overview.json"
    if not overview_path.exists():
        return None
    try:
        from weatherbrief.fetch.dwd_text import DWDDayBlock

        overview = json.loads(overview_path.read_text(encoding="utf-8"))
        out = []
        for entry in overview.get("entries", []):
            iso = entry.get("date_iso")
            block = DWDDayBlock(
                day_name_de=entry.get("day_name_de", ""),
                date_iso=date.fromisoformat(iso) if iso else None,
                text=entry.get("text_de", "") or "",
                source=entry.get("source", "") or "",
            )
            out.append((block, entry.get("text_en", "") or ""))
        return out or None
    except Exception:
        # e.g. a DWDDayBlock schema change — fall back to a context without the
        # DWD section, but leave a breadcrumb so it's diagnosable.
        logger.debug("_load_dwd_translated failed for %s", pack_dir, exc_info=True)
        return None


def _classify_situations(
    snapshot: ForecastSnapshot, adv_summary: dict, context: str, days_out: int
) -> list[str]:
    """Tag a fixture with the meteorological/structural situations it covers."""
    tags: set[str] = set()

    counts = adv_summary.get("counts", {})
    reds = counts.get("red", 0)
    non_green = [
        a for a in adv_summary.get("advisories", []) if a["aggregate"] != "green"
    ]
    if adv_summary.get("has_advisories") and not non_green:
        tags.add("all_green")
    if reds == 1:
        tags.add("single_red")
    elif reds >= 2:
        tags.add("multi_red")

    for adv in non_green:
        tag = _ADVISORY_SITUATION.get(adv["id"])
        if tag:
            tags.add(tag)
    if "icing" in tags and "convective" in tags:
        tags.add("icing_plus_convective")

    # Structural / context-derived tags.
    if days_out == 0:
        tags.add("d0")
    if "=== METAR/TAF OBSERVATIONS" in context:
        tags.add("metar")
    if "=== SIGMETs ALONG ROUTE" in context:
        tags.add("sigmet")
    if "=== PREVIOUS DIGEST" in context:
        tags.add("previous_digest")

    # Route/region tags from *airport* ICAOs only. Route waypoints also include
    # navaids/fixes (3-letter "DVR", 5-letter "KONAN") that would false-match
    # region prefixes (e.g. "KONAN" -> us_route). Real ICAO airport codes are
    # exactly 4 letters, so filter to those.
    icaos = [
        (wp.icao or "").upper()
        for wp in snapshot.route.waypoints
        if len(wp.icao or "") == 4
    ]
    if any(c.startswith(("K", "PA", "PH")) for c in icaos):
        tags.add("us_route")
    # Switzerland (LS) / Austria (LO) prefixes are a coarse Alpine proxy.
    if any(c.startswith(("LS", "LO")) for c in icaos):
        tags.add("alpine")
    if any(c.startswith("EG") for c in icaos) and any(
        c.startswith("LF") for c in icaos
    ):
        tags.add("channel_crossing")

    return sorted(tags)


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

    # Load the digest and skip long-range outlooks *before* touching the
    # context — they are a different output type (LongRangeDigest:
    # outlook/outlook_reason, no GREEN/AMBER/RED assessment) with its own
    # contract, so the WeatherDigest eval/guardrails don't apply. Doing the
    # skip first avoids reading/rebuilding a context we're about to discard
    # (outputs.py persists digest_context.txt for longrange packs too).
    # Long-range gets its own eval track later (see project_longrange_outlook).
    digest = json.loads((pack_dir / "digest.json").read_text())
    if "outlook" in digest or digest.get("assessment") not in (
        "GREEN", "AMBER", "RED"
    ):
        return None

    # Prefer the persisted context (byte-faithful to what the LLM saw). Fall
    # back to best-effort reconstruction for older packs that predate
    # digest_context.txt — splicing the DWD overview back in when available.
    persisted = pack_dir / "digest_context.txt"
    if persisted.exists():
        context = persisted.read_text(encoding="utf-8")
        faithful = True
        context_source = "persisted"
    else:
        context = build_digest_context(
            snapshot, target_time,
            route_advisories=advisories,
            dwd_translated=_load_dwd_translated(pack_dir),
            flight_rules="vfr_ifr",
        )
        faithful = False
        context_source = "reconstructed"

    # Build metadata
    route = " -> ".join(wp.icao for wp in snapshot.route.waypoints)
    adv_summary = extract_advisory_summary(adv_path)
    situations = _classify_situations(
        snapshot, adv_summary, context, snapshot.days_out
    )

    meta = {
        "pack_path": str(pack_dir),
        "route": route,
        "target_date": snapshot.target_date,
        "fetch_date": snapshot.fetch_date,
        "days_out": snapshot.days_out,
        "assessment": digest.get("assessment"),
        "assessment_reason": digest.get("assessment_reason"),
        "advisory_summary": adv_summary,
        "situations": situations,
        "faithful": faithful,
        "context_source": context_source,
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


def _read_preserved_fixtures(output_dir: Path) -> list[dict]:
    """Read fixtures that must survive a regenerate verbatim.

    Two kinds qualify (by ``meta.json`` flag):
    * ``synthetic: true`` — hand-authored, not derived from a pack.
    * ``curated: true`` — a pack-derived fixture a human has golden-labelled
      (see ``scripts/label_digest_eval.py``). We freeze it so re-extraction
      doesn't drop the human label, and so the committed golden context stays
      exactly what was labelled.

    Returns ``[{"id": dir_name, "files": {filename: text}}, ...]``.
    """
    preserved: list[dict] = []
    if not output_dir.exists():
        return preserved
    for child in sorted(output_dir.iterdir()):
        meta_path = child / "meta.json"
        if not child.is_dir() or not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not (meta.get("synthetic") or meta.get("curated")):
            continue
        files = {
            f.name: f.read_text(encoding="utf-8")
            for f in child.iterdir() if f.is_file()
        }
        preserved.append({"id": child.name, "files": files})
    return preserved


def preserved_index(preserved: list[dict]):
    """Yield index entries for preserved (synthetic/curated) fixtures."""
    for p in preserved:
        # meta.json is guaranteed present by _read_preserved_fixtures (it only
        # includes dirs where meta_path.exists()); the default is just defensive.
        meta = json.loads(p["files"].get("meta.json", "{}"))
        yield {
            "id": p["id"],
            "assessment": meta.get("assessment"),
            "route": meta.get("route"),
            "days_out": meta.get("days_out"),
            "has_advisories": meta.get("advisory_summary", {}).get(
                "has_advisories", False
            ),
            "advisory_counts": meta.get("advisory_summary", {}).get("counts", {}),
            "situations": meta.get("situations", []),
            "faithful": meta.get("faithful", True),
            "synthetic": bool(meta.get("synthetic")),
            "curated": bool(meta.get("curated")),
            "golden": meta.get("golden"),
            "context_chars": meta.get("context_chars", len(p["files"].get("context.txt", ""))),
        }


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

    # Write output. Preserve fixtures flagged synthetic (hand-authored) or
    # curated (human-golden-labelled) across a regenerate — rmtree would
    # otherwise destroy the injection fixture and any golden labels we've added.
    preserved = _read_preserved_fixtures(args.output_dir)
    if preserved:
        print(f"Preserving {len(preserved)} synthetic/curated fixture(s): "
              f"{', '.join(p['id'] for p in preserved)}")
    if args.output_dir.exists():
        shutil.rmtree(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for p in preserved:
        dst = args.output_dir / p["id"]
        dst.mkdir(parents=True, exist_ok=True)
        for name, content in p["files"].items():
            (dst / name).write_text(content, encoding="utf-8")

    # Write index — preserved (frozen) fixtures first. A curated fixture is also
    # pack-derived, so skip its id in the results loop below: the frozen copy
    # (with its golden label) must win over a fresh re-extraction.
    index = list(preserved_index(preserved))
    preserved_ids = {p["id"] for p in preserved}
    for r in sorted(results, key=lambda x: (x["meta"]["route"], x["meta"]["target_date"])):
        fid = make_fixture_id(r["meta"])
        if fid in preserved_ids:
            continue
        fixture_dir = args.output_dir / fid
        fixture_dir.mkdir(parents=True, exist_ok=True)

        (fixture_dir / "context.txt").write_text(r["context"], encoding="utf-8")
        (fixture_dir / "digest.json").write_text(
            json.dumps(r["digest"], indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (fixture_dir / "meta.json").write_text(
            json.dumps(r["meta"], indent=2, ensure_ascii=False), encoding="utf-8"
        )

        idx_entry = {
            "id": fid,
            "assessment": r["meta"]["assessment"],
            "route": r["meta"]["route"],
            "days_out": r["meta"]["days_out"],
            "has_advisories": r["meta"]["advisory_summary"].get("has_advisories", False),
            "advisory_counts": r["meta"]["advisory_summary"].get("counts", {}),
            "situations": r["meta"].get("situations", []),
            "faithful": r["meta"].get("faithful", False),
            "context_chars": r["meta"]["context_chars"],
        }
        index.append(idx_entry)

    (args.output_dir / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # Print summary over the combined index (pack-derived + preserved synthetic)
    # so counts match what was actually written.
    assess_counts = Counter(e.get("assessment") for e in index)
    total_kb = sum(e.get("context_chars", 0) for e in index) / 1024
    faithful_n = sum(1 for e in index if e.get("faithful"))
    print(f"\nDataset written to {args.output_dir}/")
    print(f"  {len(index)} fixtures ({len(preserved)} synthetic/curated), "
          f"{total_kb:.0f}KB total context")
    print(f"  Assessment distribution: {dict(assess_counts)}")
    print(f"  Fidelity: {faithful_n} faithful / "
          f"{len(index) - faithful_n} reconstructed")

    # Coverage matrix — how many fixtures cover each situation cell, so we can
    # see which cells are thin and need targeted sourcing (parent #252 / #254).
    # Accumulate from the combined index so preserved synthetic fixtures count
    # too — otherwise a cell only covered by a synthetic fixture reads "empty".
    sit_counts: Counter[str] = Counter()
    for entry in index:
        sit_counts.update(entry.get("situations", []))
    print("  Situation coverage:")
    for cell in SITUATION_VOCAB:
        n = sit_counts.get(cell, 0)
        flag = "  <-- empty" if n == 0 else ("  <-- thin" if n < 2 else "")
        print(f"    {cell:24} {n:>3}{flag}")


if __name__ == "__main__":
    main()
