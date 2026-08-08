#!/usr/bin/env python3
"""Run the LLM digest eval against the labelled eval-set repo.

Replays each entry's saved ``digest_context.txt`` through the briefer prompt and
scores the resulting GREEN/AMBER/RED against the SME's golden label for the
guidance preset in play.

The eval set lives in a separate repo, located via ``EVAL_CORPUS_DIR`` /
``EVAL_STAGING_DIR`` (see ``weatherbrief.eval_workbench.config``). ``corpus`` is
the curated, labelled area; ``staging`` is raw pulled data, mostly unlabelled.
Maintaining that set — pulling packs, attaching debriefs, labelling, promoting
staging->corpus — is the ``eval-workbench`` skill. This script only replays.

Three things are deliberately excluded from scoring rather than silently
skipped, and every exclusion is reported:

* **Long-range entries (days_out > 7).** Production sends these to
  ``briefer_longrange``, which emits ``LongRangeDigest.outlook``
  (TRENDING_SETTLED / TRENDING_UNSETTLED / MIXED_SIGNALS) — a deliberately
  different scale from GREEN/AMBER/RED. ``CorpusLabel.assessments`` has no
  vocabulary for it, so these cannot be scored at all. Replaying them through
  the short-range prompt (as the old fixture set did for 74 of its 114 entries)
  produces numbers that look meaningful and are not.
* **Entries with no ``digest_context.txt``.** Contexts are gitignored and
  re-pullable via ``scripts/pull_eval_corpus.py``; absence means "not pulled
  yet", not "no data".
* **Unlabelled entries.** Scoring against the model's own prior output measures
  self-consistency, not correctness.

Usage:
    source venv/bin/activate

    python scripts/run_digest_eval.py --dry-run            # what would run, no LLM calls
    python scripts/run_digest_eval.py                      # corpus, every labelled guidance
    python scripts/run_digest_eval.py --guidance balanced  # one preset only
    python scripts/run_digest_eval.py --area both
    python scripts/run_digest_eval.py --prompt configs/weather_digest/prompts/briefer_v3.md
    python scripts/run_digest_eval.py --show <corpus_id>   # print a context, no LLM call
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (works regardless of cwd)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from weatherbrief.digest.llm_config import DigestConfig, create_llm, load_digest_config
from weatherbrief.digest.llm_digest import (
    WeatherDigest,
    _system_content,
    ecmwf_grib_horizon_days,
)
from weatherbrief.eval_workbench.corpus import CorpusPack, list_corpus, pack_path

_ASSESSMENT_ORDER = {"GREEN": 0, "AMBER": 1, "RED": 2}
GUIDANCES = ("balanced", "conservative", "tolerant")


# --- eval-set loading -------------------------------------------------------


def context_path(pack: CorpusPack) -> Path:
    return pack_path(pack.corpus_id, pack.area) / "digest_context.txt"


def is_long_range(pack: CorpusPack) -> bool:
    """Mirror ``llm_digest.is_long_range`` — strictly beyond the GRIB horizon."""
    return pack.meta.days_out > ecmwf_grib_horizon_days()


def collect(areas: list[str]) -> list[CorpusPack]:
    packs: list[CorpusPack] = []
    for area in areas:
        try:
            packs.extend(list_corpus(area))
        except FileNotFoundError:
            print(f"  (area {area!r} not found on disk — skipped)")
    return sorted(packs, key=lambda p: (p.meta.days_out, p.corpus_id))


def partition(packs: list[CorpusPack]) -> tuple[list[CorpusPack], Counter]:
    """Split into runnable packs and a tally of why the rest were excluded."""
    runnable, excluded = [], Counter()
    for p in packs:
        if is_long_range(p):
            excluded["long-range (d>7) — outlook scale, not GREEN/AMBER/RED"] += 1
        elif not context_path(p).exists():
            excluded["no digest_context.txt (re-pull with pull_eval_corpus.py)"] += 1
        elif not p.is_labeled:
            excluded["no golden label"] += 1
        else:
            runnable.append(p)
    return runnable, excluded


# --- LLM ---------------------------------------------------------------------


def cached_system_content(system_prompt: str, config: DigestConfig):
    """The eval's system block, with a 5-minute cache breakpoint on it.

    Split out of ``run_one`` purely so a unit test can exercise this call
    without standing up an LLM — ``_system_content`` is production code and has
    changed signature under the eval before (briefer_v3 moved it to a head/tail
    pair, and this call site was missed, breaking every cached eval run).

    The whole rendered prompt goes in the ``head`` with an empty ``tail``, which
    is deliberately *not* what production does. Prod splits at the guidance so
    its three presets share one entry across pilots; an eval renders each preset
    once and runs its jobs grouped by preset, so caching the lot in one block is
    both simpler and strictly better here — nothing else would land in the tail.
    """
    return _system_content(system_prompt, "", config.llm, longrange=False, ttl="5m")


def run_one(
    context: str,
    system_prompt: str,
    config: DigestConfig,
    cache: bool = False,
) -> tuple[WeatherDigest, dict]:
    """Run a single short-range digest call. Returns (digest, info).

    ``cache=True`` puts a 5-minute prompt-cache breakpoint on the system block.
    An eval is the ideal caching workload — every call in a run shares a
    byte-identical prefix and they land seconds apart — so one write carries the
    whole run. Five minutes rather than production's ``DIGEST_CACHE_TTL``
    because nothing expires inside a burst and the write premium is 1.25x
    instead of 2x; the override is only legitimate here because an eval never
    writes to the cost ledger, which prices writes at the production TTL.
    Off by default so importers (tests/test_digest_assertions.py) are unaffected;
    the CLI turns it on unless ``--no-cache`` is passed.

    The prompt is passed whole as the cached head with an empty tail: the eval
    renders one prompt per run (guidance included), so there is no per-caller
    tail to keep outside the breakpoint the way production has.
    """
    llm = create_llm(config)
    structured_llm = llm.with_structured_output(WeatherDigest, include_raw=True)

    system_content = (
        cached_system_content(system_prompt, config)
        if cache else system_prompt
    )

    t0 = time.time()
    raw_result = structured_llm.invoke([
        {"role": "system", "content": system_content},
        {"role": "user", "content": context},
    ])
    info = {"elapsed_s": round(time.time() - t0, 1)}

    digest: WeatherDigest = raw_result["parsed"]
    raw_msg = raw_result.get("raw")
    if raw_msg:
        usage = getattr(raw_msg, "usage_metadata", None)
        if usage:
            info["input_tokens"] = usage.get("input_tokens", 0)
            info["output_tokens"] = usage.get("output_tokens", 0)
            details = usage.get("input_token_details") or {}
            info["cache_read"] = details.get("cache_read") or 0
    return digest, info


def compare_assessment(old: str, new: str) -> str:
    if old == new:
        return "="
    return "^" if _ASSESSMENT_ORDER.get(new, -1) > _ASSESSMENT_ORDER.get(old, -1) else "v"


# --- CLI ---------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the digest eval against the eval-set repo")
    parser.add_argument("--area", choices=("corpus", "staging", "both"), default="corpus")
    parser.add_argument("--guidance", choices=(*GUIDANCES, "all"), default="all",
                        help="Guidance preset to score, or 'all' labelled presets (default)")
    parser.add_argument("--prompt", type=str, help="Path to an alternative system prompt file")
    parser.add_argument("--config", type=str, default="default", help="LLM config name")
    parser.add_argument("--filter", type=str, help="Only entries whose corpus_id contains this")
    parser.add_argument("--priority", type=int, choices=(1, 2, 3, 4),
                        help="Only entries at this SME triage priority")
    parser.add_argument("--situation", type=str, help="Only entries carrying this situation tag")
    parser.add_argument("--limit", type=int, help="Max entries (applied after filtering)")
    parser.add_argument("--dry-run", action="store_true", help="List what would run, no LLM calls")
    parser.add_argument("--show", type=str, help="Print one entry's context and exit")
    parser.add_argument("--output", type=str, help="Save results JSON here")
    parser.add_argument("--baseline", type=str,
                        help="A previous --output JSON; also report drift against it")
    parser.add_argument("--no-cache", action="store_true",
                        help="Disable the 5-minute prompt cache (on by default)")
    args = parser.parse_args()

    areas = ["corpus", "staging"] if args.area == "both" else [args.area]
    packs = collect(areas)
    if not packs:
        print("No eval entries found. Is EVAL_CORPUS_DIR set and the eval-set repo present?")
        return 1

    if args.show:
        match = [p for p in packs if p.corpus_id == args.show]
        if not match:
            print(f"No entry {args.show!r}")
            return 1
        p = match[0]
        cp = context_path(p)
        print(f"=== {p.corpus_id} ({p.area}) ===")
        print(f"Route: {p.meta.route}  target={p.meta.target_date}  d{p.meta.days_out}")
        print(f"Model said: {p.meta.assessment}  |  golden: "
              f"{p.label.assessments if p.label else '(unlabelled)'}")
        if p.meta.debriefed:
            print(f"Debrief: {p.meta.debrief_decision} (graded={p.meta.debrief_graded})")
        print(f"Situations: {', '.join(p.meta.situations) or '-'}")
        print(f"\n--- context ({cp}) ---")
        print(cp.read_text() if cp.exists() else "(no digest_context.txt — re-pull it)")
        return 0

    # Filters apply before partitioning so exclusion counts describe the selection.
    if args.filter:
        packs = [p for p in packs if args.filter in p.corpus_id]
    if args.priority is not None:
        packs = [p for p in packs if p.label and p.label.priority == args.priority]
    if args.situation:
        packs = [p for p in packs if args.situation in p.meta.situations]

    runnable, excluded = partition(packs)
    if args.limit:
        runnable = runnable[:args.limit]

    # One job per (entry, guidance) the golden label actually covers.
    wanted = GUIDANCES if args.guidance == "all" else (args.guidance,)
    # Ordered by guidance first: each preset renders a different prompt, so
    # grouping keeps one cache prefix hot per block instead of cycling three.
    jobs = [
        (p, g, p.label.assessments[g])
        for g in wanted
        for p in runnable
        if p.label and g in p.label.assessments
    ]

    print(f"Eval set: {', '.join(areas)}  ({len(packs)} entries selected)")
    print(f"Runnable: {len(runnable)}   →  {len(jobs)} LLM calls "
          f"({'all labelled guidances' if args.guidance == 'all' else args.guidance})")
    if excluded:
        print("Excluded:")
        for reason, n in excluded.most_common():
            print(f"  {n:>4}  {reason}")
    if not jobs:
        print("\nNothing to run.")
        return 1

    if args.dry_run:
        print()
        for p, g, golden in jobs:
            print(f"  d{p.meta.days_out:<3} {g:<13} golden={golden:<6} "
                  f"{p.area:<8} {p.corpus_id[:52]}")
        return 0

    config = load_digest_config(args.config)
    if args.prompt:
        template = Path(args.prompt).read_text()
    else:
        template = (Path(__file__).resolve().parent.parent / "configs"
                    / "weather_digest" / getattr(config.prompts, "briefer")).read_text()

    print(f"LLM: {config.llm.provider}/{config.llm.model}")
    print(f"Prompt: {args.prompt or config.prompts.briefer}\n")

    results, passed, failed = [], 0, 0
    tok_in = tok_out = cached = 0

    for i, (p, guidance, golden) in enumerate(jobs, 1):
        # Render per job: guidance changes the prompt, so it cannot be hoisted.
        system_prompt = config.render_prompt(template, guidance_key=guidance)
        try:
            digest, info = run_one(context_path(p).read_text(), system_prompt, config,
                                   cache=not args.no_cache)
        except Exception as exc:  # noqa: BLE001 — one bad entry must not kill the run
            print(f"  [{i:>3}/{len(jobs)}] ERROR {p.corpus_id[:40]}: {exc}", flush=True)
            results.append({"corpus_id": p.corpus_id, "guidance": guidance, "error": str(exc)})
            continue

        got = digest.assessment
        ok = got == golden
        passed += ok
        failed += not ok
        tok_in += info.get("input_tokens", 0)
        tok_out += info.get("output_tokens", 0)
        cached += info.get("cache_read", 0)

        print(f"  [{i:>3}/{len(jobs)}] {'PASS' if ok else 'FAIL'} "
              f"{golden:5} {compare_assessment(golden, got)} {got:5} | "
              f"{guidance:<13} d{p.meta.days_out:<3} {p.meta.route[:34]:<34} "
              f"{info.get('elapsed_s', '?')}s", flush=True)
        if not ok:
            print(f"         golden rationale: {(p.label.rationale or '-')[:88]}")
            print(f"         model said      : {digest.assessment_reason[:88]}")

        results.append({
            "corpus_id": p.corpus_id, "area": p.area, "guidance": guidance,
            "days_out": p.meta.days_out, "route": p.meta.route,
            "golden": golden, "got": got, "passed": ok,
            "golden_rationale": p.label.rationale,
            "model_reason": digest.assessment_reason,
            "debrief_decision": p.meta.debrief_decision,
            "digest": digest.model_dump(),
            **info,
        })

    total = passed + failed
    print(f"\n{'=' * 60}")
    if total:
        print(f"Accuracy vs golden: {passed}/{total} = {passed / total * 100:.0f}%")
    by_g = Counter(r["guidance"] for r in results if "passed" in r)
    for g in sorted(by_g):
        rows = [r for r in results if r.get("guidance") == g and "passed" in r]
        hits = sum(r["passed"] for r in rows)
        print(f"  {g:<13} {hits}/{len(rows)}")
    print(f"Golden distribution: {dict(Counter(r['golden'] for r in results if 'golden' in r))}")
    print(f"Model  distribution: {dict(Counter(r['got'] for r in results if 'got' in r))}")
    # Drift vs a previous run: "did this prompt change what the model says?",
    # which is a different question from "is the model right". Golden accuracy
    # can sit still for labelling-philosophy reasons while a prompt edit quietly
    # moves real recommendations, so a prompt change wants both numbers.
    if args.baseline:
        try:
            prev = json.loads(Path(args.baseline).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"\n(could not read baseline {args.baseline}: {exc})")
        else:
            before = {(r["corpus_id"], r["guidance"]): r["got"]
                      for r in prev if "got" in r}
            pairs = [(r, before[(r["corpus_id"], r["guidance"])])
                     for r in results
                     if "got" in r and (r["corpus_id"], r["guidance"]) in before]
            moved = [(r, b) for r, b in pairs if r["got"] != b]
            print(f"\nDrift vs {Path(args.baseline).name}: "
                  f"{len(pairs) - len(moved)}/{len(pairs)} unchanged, {len(moved)} moved")
            for r, b in moved:
                # Flag whether the move went toward or away from the golden.
                verdict = ("-> now matches golden" if r["got"] == r["golden"]
                           else ("was matching golden" if b == r["golden"] else "both wrong"))
                print(f"  {compare_assessment(b, r['got'])} {b:5} -> {r['got']:5} "
                      f"| {r['guidance']:<13} {r['corpus_id'][:40]}  ({verdict})")
            if not moved and pairs:
                print("  (no recommendation changed)")
            missing = len(results) - len(pairs)
            if missing > 0:
                print(f"  ({missing} result(s) absent from the baseline — not compared)")

    print(f"Tokens: {tok_in:,} in ({cached:,} from cache) + {tok_out:,} out")

    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"Results saved to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
