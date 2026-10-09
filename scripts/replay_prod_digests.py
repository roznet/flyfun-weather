#!/usr/bin/env python3
"""Replay exported prod digests on a digest config and compare (#717).

Input is a directory from ``scripts/export_digest_replay.py``: one folder per
pack with the byte-faithful ``digest_context.txt`` and the ``digest.json`` prod
actually showed. The system prompt is built exactly as production's
``briefer_node`` builds it (the pack's guidance and locale, the 1h cache
breakpoint), so the stored digest is the baseline and only the candidate needs
running. Records timing, tokens and USD per call.

Saved contexts predate #717's spelled-out runway wind, so the arrows are
decoded here the same way the prompt builder now does (a no-op on newer packs).

    python scripts/replay_prod_digests.py <export_dir> --config default --output replay.json
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from weatherbrief.analysis.advisories.airport_wind import spell_out_runway_wind  # noqa: E402
from weatherbrief.costs import DIGEST_CACHE_TTL, compute_call_cost  # noqa: E402
from weatherbrief.digest.llm_config import (  # noqa: E402
    create_llm, load_digest_config, with_structured,
)
from weatherbrief.digest.llm_digest import WeatherDigest, _system_content  # noqa: E402

_ORDER = ["GREEN", "AMBER", "RED"]


def run_one(pack_dir: Path, entry: dict, config) -> dict:
    head, tail = config.load_prompt_parts("briefer", locale=entry["locale"],
                                          guidance_key=entry["guidance"])
    system = _system_content(head, tail, config.llm, False,
                             locale_cacheable=config.should_cache_locale(entry["locale"]))
    llm = with_structured(create_llm(config), WeatherDigest, config.llm, include_raw=True)
    context = spell_out_runway_wind((pack_dir / "digest_context.txt").read_text())
    t0 = time.time()
    try:
        raw = llm.invoke([{"role": "system", "content": system},
                          {"role": "user", "content": context}])
    except Exception as exc:  # noqa: BLE001 — one bad pack must not kill the run
        return {**entry, "error": str(exc)[:300], "elapsed_s": round(time.time() - t0, 1)}
    out = {**entry, "elapsed_s": round(time.time() - t0, 1)}
    msg, digest = raw.get("raw"), raw.get("parsed")
    usage = (getattr(msg, "usage_metadata", None) or {}) if msg else {}
    details = usage.get("input_token_details") or {}
    out.update(
        input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
        cache_read=details.get("cache_read") or 0, cache_write=details.get("cache_creation") or 0,
        stop_reason=(msg.response_metadata or {}).get("stop_reason") if msg else None,
    )
    out["cost_usd"] = compute_call_cost(config.llm.model, out["input_tokens"], out["output_tokens"],
                                        out["cache_read"], out["cache_write"], cache_ttl=DIGEST_CACHE_TTL)
    if digest is None:
        out["error"] = "no parsed digest"
        return out
    out["got"] = digest.assessment
    out["digest"] = digest.model_dump()
    out["output_chars"] = len(digest.model_dump_json())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("export_dir", type=Path)
    ap.add_argument("--config", default="default")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()

    config = load_digest_config(args.config)
    manifest = json.loads((args.export_dir / "manifest.json").read_text())
    print(f"{len(manifest)} packs  |  {config.llm.model}  |  {config.prompts.briefer}\n")

    results = []
    with ThreadPoolExecutor(args.workers) as ex:
        for i, r in enumerate(ex.map(lambda e: run_one(args.export_dir / e["id"], e, config), manifest), 1):
            results.append(r)
            print(f"[{i:>3}/{len(manifest)}] {r['prod_assessment']:5} -> {r.get('got') or 'ERR':5} "
                  f"{r['guidance']:<12} {r['locale']} {','.join(r['sources']):<20} "
                  f"{r['elapsed_s']}s {r.get('output_tokens', '?')}tok ${r.get('cost_usd', 0):.4f}",
                  flush=True)
    if args.output:
        args.output.write_text(json.dumps(results, indent=1, ensure_ascii=False))

    ok = [r for r in results if r.get("got")]
    if not ok:
        print("\nno successful calls")
        return 1
    same = sum(r["got"] == r["prod_assessment"] for r in ok)
    stricter = sum(_ORDER.index(r["got"]) > _ORDER.index(r["prod_assessment"]) for r in ok)
    prod = collections.Counter(r["prod_assessment"] for r in ok)
    new = collections.Counter(r["got"] for r in ok)
    cost = [r["cost_usd"] for r in ok]
    print(f"\nsame rating as prod: {same}/{len(ok)}  stricter {stricter}  looser {len(ok) - same - stricter}"
          f"  errors {len(results) - len(ok)}")
    print(f"prod G/A/R {prod['GREEN']}/{prod['AMBER']}/{prod['RED']}  ->  "
          f"{new['GREEN']}/{new['AMBER']}/{new['RED']}")
    print(f"cost ${sum(cost):.3f} total, ${statistics.mean(cost):.4f}/call  |  "
          f"time median {statistics.median(r['elapsed_s'] for r in ok)}s  |  "
          f"out median {statistics.median(r['output_tokens'] for r in ok)} tok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
