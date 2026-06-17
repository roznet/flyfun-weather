#!/usr/bin/env python3
"""Run LLM digest eval against saved context fixtures.

Replays saved context strings through the LLM and compares the new
assessment against the original. Useful for:
  - Testing prompt changes without re-fetching weather data
  - Evaluating assessment calibration (GREEN/AMBER/RED distribution)
  - Reviewing specific cases interactively

Usage:
    # Run all fixtures (costs real LLM tokens!)
    python scripts/run_digest_eval.py

    # Dry-run: show what would be tested
    python scripts/run_digest_eval.py --dry-run

    # Run only fixtures matching a filter
    python scripts/run_digest_eval.py --filter "egtf_lfat"

    # Run only AMBER fixtures (the ones we want to review)
    python scripts/run_digest_eval.py --assessment AMBER

    # Show context for a specific fixture (no LLM call)
    python scripts/run_digest_eval.py --show egtf_ebos_ehrd_2026-03-02_d2_2026-02-28

    # Use a different prompt file
    python scripts/run_digest_eval.py --prompt configs/weather_digest/prompts/briefer_v2.md

    # Use a specific LLM config
    python scripts/run_digest_eval.py --config openai

    # Compare guidance presets on the same fixtures
    python scripts/run_digest_eval.py --guidance conservative
    python scripts/run_digest_eval.py --guidance tolerant
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (works regardless of cwd)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from weatherbrief.digest.llm_config import DigestConfig, create_llm, load_digest_config
from weatherbrief.digest.llm_digest import WeatherDigest


EVAL_DIR = Path("tests/eval_data/digests")

_ASSESSMENT_ORDER = {"GREEN": 0, "AMBER": 1, "RED": 2}


def load_index() -> list[dict]:
    idx_path = EVAL_DIR / "index.json"
    return json.loads(idx_path.read_text())


def load_fixture(fixture_id: str) -> dict:
    fixture_dir = EVAL_DIR / fixture_id
    return {
        "context": (fixture_dir / "context.txt").read_text(),
        "digest": json.loads((fixture_dir / "digest.json").read_text()),
        "meta": json.loads((fixture_dir / "meta.json").read_text()),
    }


def resolve_expected(meta: dict) -> dict:
    """Golden expected assessments per guidance, tolerant of both schemas.

    The interactive labeller (``label_digest_eval.py``) writes the SME's
    ground truth to ``meta["golden"]["assessments"]``; older synthetic
    fixtures carry a top-level ``meta["expected_assessments"]``. Prefer the
    golden label when present so CLI-produced labels are actually scored.
    """
    golden = meta.get("golden")
    if isinstance(golden, dict) and isinstance(golden.get("assessments"), dict):
        return golden["assessments"]
    return meta.get("expected_assessments", {})


def run_one(
    context: str,
    system_prompt: str,
    config: DigestConfig,
) -> tuple[WeatherDigest | None, dict]:
    """Run a single LLM digest call. Returns (digest, info)."""
    llm = create_llm(config)
    structured_llm = llm.with_structured_output(WeatherDigest, include_raw=True)

    t0 = time.time()
    raw_result = structured_llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context},
    ])
    elapsed = time.time() - t0

    digest: WeatherDigest = raw_result["parsed"]
    info = {"elapsed_s": round(elapsed, 1)}

    raw_msg = raw_result.get("raw")
    if raw_msg:
        usage = getattr(raw_msg, "usage_metadata", None)
        if usage:
            info["input_tokens"] = usage.get("input_tokens", 0)
            info["output_tokens"] = usage.get("output_tokens", 0)

    return digest, info


def compare_assessment(old: str, new: str) -> str:
    """Return a comparison symbol."""
    if old == new:
        return "="
    old_i = _ASSESSMENT_ORDER.get(old, -1)
    new_i = _ASSESSMENT_ORDER.get(new, -1)
    return "^" if new_i > old_i else "v"


def main():
    parser = argparse.ArgumentParser(description="Run digest eval")
    parser.add_argument("--dry-run", action="store_true", help="List fixtures, don't call LLM")
    parser.add_argument("--filter", type=str, help="Only fixtures matching this substring")
    parser.add_argument("--assessment", type=str, help="Only fixtures with this original assessment")
    parser.add_argument("--show", type=str, help="Show context for a specific fixture ID")
    parser.add_argument("--prompt", type=str, help="Path to alternative system prompt file")
    parser.add_argument("--guidance", type=str, help="Guidance preset key (conservative/balanced/tolerant)")
    parser.add_argument("--config", type=str, default="default", help="LLM config name")
    parser.add_argument("--output", type=str, help="Save results JSON to this path")
    parser.add_argument("--limit", type=int, help="Max fixtures to run")
    args = parser.parse_args()

    index = load_index()

    # Show mode: print context and exit
    if args.show:
        fixture = load_fixture(args.show)
        meta = fixture["meta"]
        digest = fixture["digest"]
        adv = meta.get("advisory_summary", {})
        print(f"=== {args.show} ===")
        print(f"Route: {meta['route']}  Date: {meta['target_date']}  D-{meta['days_out']}")
        print(f"Original: {digest['assessment']} — {digest['assessment_reason']}")
        if adv.get("has_advisories"):
            print(f"Advisories: G={adv['counts']['green']} A={adv['counts']['amber']} R={adv['counts']['red']}")
            for a in adv.get("advisories", []):
                if a["aggregate"] != "green":
                    models_str = " ".join(f"{k}={v}" for k, v in a["models"].items())
                    print(f"  [{a['aggregate'].upper():5}] {a['id']}: {models_str}")
        print(f"\n--- Context ({meta['context_chars']} chars) ---")
        print(fixture["context"])
        return

    guidance_key = args.guidance

    # Filter
    entries = index
    if args.filter:
        entries = [e for e in entries if args.filter in e["id"]]
    if args.assessment:
        entries = [e for e in entries if e["assessment"] == args.assessment.upper()]
    if args.limit:
        entries = entries[:args.limit]

    print(f"Fixtures: {len(entries)} (of {len(index)} total)")

    if args.dry_run:
        has_expected_count = 0
        for e in entries:
            adv = e.get("advisory_counts", {})
            # Show expected assessment for the active guidance if available
            expected_col = ""
            if guidance_key:
                fixture = load_fixture(e["id"])
                ea = resolve_expected(fixture["meta"])
                if guidance_key in ea:
                    expected_col = f" exp={ea[guidance_key]:5}"
                    has_expected_count += 1
                else:
                    expected_col = " exp=  -  "
            print(
                f"  [{e['assessment']:5}]{expected_col} G={adv.get('green', '-'):>2} "
                f"A={adv.get('amber', '-'):>2} R={adv.get('red', '-'):>2} | "
                f"{e['route']:45} d{e['days_out']} | {e['context_chars']:>6}ch  {e['id']}"
            )
        total_chars = sum(e["context_chars"] for e in entries)
        print(f"\nTotal context: {total_chars:,} chars (~{total_chars//4:,} tokens)")
        if guidance_key:
            print(f"Expected assessments ({guidance_key}): {has_expected_count}/{len(entries)} fixtures")
        return

    # Load LLM config and prompt
    config = load_digest_config(args.config)
    if args.prompt:
        system_prompt = Path(args.prompt).read_text()
    else:
        system_prompt = config.load_prompt("briefer", guidance_key=guidance_key)

    print(f"LLM: {config.llm.provider}/{config.llm.model}")
    print(f"Prompt: {args.prompt or config.prompts.briefer}")
    if guidance_key:
        print(f"Guidance: {guidance_key}")
    print()

    results = []
    changed = 0
    passed = 0
    failed = 0
    no_expected = 0
    total_input = 0
    total_output = 0

    for i, entry in enumerate(entries):
        fixture = load_fixture(entry["id"])
        meta = fixture["meta"]

        # Use golden/expected assessment for the guidance when available,
        # else fall back to what the model originally produced.
        expected_assessments = resolve_expected(meta)
        if guidance_key and guidance_key in expected_assessments:
            expected = expected_assessments[guidance_key]
            has_expected = True
        else:
            expected = fixture["digest"]["assessment"]
            has_expected = False

        try:
            digest, info = run_one(fixture["context"], system_prompt, config)
            new_assessment = digest.assessment
            cmp = compare_assessment(expected, new_assessment)
            total_input += info.get("input_tokens", 0)
            total_output += info.get("output_tokens", 0)

            if expected != new_assessment:
                changed += 1

            if has_expected:
                if expected == new_assessment:
                    passed += 1
                    status = "PASS"
                else:
                    failed += 1
                    status = "FAIL"
            else:
                no_expected += 1
                status = "----"

            adv = entry.get("advisory_counts", {})
            print(
                f"  [{i+1:>3}/{len(entries)}] {status} {expected:5} {cmp} {new_assessment:5} | "
                f"G={adv.get('green', '-'):>2} A={adv.get('amber', '-'):>2} R={adv.get('red', '-'):>2} | "
                f"{entry['route']:35} d{entry['days_out']} | "
                f"{info.get('elapsed_s', '?')}s"
            )
            if cmp != "=":
                reason_label = "expected" if has_expected else "old"
                if has_expected:
                    # Show the SME's golden rationale (why it was labelled), not
                    # the model's original reasoning; fall back if unlabelled.
                    golden = meta.get("golden") or {}
                    reason_text = (
                        golden.get("rationale")
                        or fixture["digest"]["assessment_reason"]
                    )
                else:
                    reason_text = fixture["digest"]["assessment_reason"]
                print(f"         {reason_label}: {reason_text[:90]}")
                print(f"         new:      {digest.assessment_reason[:90]}")

            results.append({
                "id": entry["id"],
                "route": entry["route"],
                "days_out": entry["days_out"],
                "expected_assessment": expected,
                "new_assessment": new_assessment,
                "has_expected": has_expected,
                "passed": has_expected and expected == new_assessment,
                "changed": expected != new_assessment,
                "expected_reason": fixture["digest"]["assessment_reason"],
                "new_reason": digest.assessment_reason,
                "new_digest": digest.model_dump(),
                "advisory_counts": entry.get("advisory_counts", {}),
                **info,
            })

        except Exception as e:
            print(f"  [{i+1:>3}/{len(entries)}] ERROR: {e}")
            results.append({
                "id": entry["id"],
                "error": str(e),
            })

    # Summary
    print(f"\n{'='*60}")
    print(f"Changed: {changed}/{len(entries)}")
    if passed or failed:
        total_tested = passed + failed
        print(f"Regression: {passed}/{total_tested} passed, {failed}/{total_tested} failed")
        if no_expected:
            print(f"  ({no_expected} fixtures without expected_assessments — not scored)")
    print(f"Tokens: {total_input:,} input + {total_output:,} output")

    from collections import Counter
    expected_dist = Counter(r.get("expected_assessment") for r in results if "expected_assessment" in r)
    new_dist = Counter(r.get("new_assessment") for r in results if "new_assessment" in r)
    print(f"Expected distribution: {dict(expected_dist)}")
    print(f"New distribution: {dict(new_dist)}")

    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
