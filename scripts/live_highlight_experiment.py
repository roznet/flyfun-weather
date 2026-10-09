"""Experiment (#690 follow-up, now #697): a one-line "highlight" of a live
tick, written by a small model from facts the code has already computed.

    python scripts/live_highlight_experiment.py <live.json or /live body> [...]
        [--model claude-haiku-5-5] [--facts-only]

Code does the weather: it reduces the tick (glance, ribbon bands, storms,
SIGMETs, change rows) to a short facts block. The model only chooses what
leads and phrases it, under the product's voice rules (no verdict, facts
only, plain words).

The prompt, the facts block and the grounding check now live in
``weatherbrief.tasks.live_highlight`` — the code the tick runs — so this
script replays exactly what production does and cannot drift from it. What is
left here is the replay harness: ``--flown`` to re-ask the same tick as if N
NM were flown, and the per-case report.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from weatherbrief.tasks.live_highlight import (  # noqa: E402
    DEFAULT_MODEL,
    EFFORT,
    MAX_TOKENS,
    MAX_WORDS,
    SYSTEM,
    THINKING,
    check_grounding,
    review_flags,
    facts,
)


def _at(live: dict, flown: float | None) -> dict:
    """The tick as if ``flown`` NM were flown: storms behind that point drop
    out of "ahead"."""
    if flown is None:
        return live
    live = json.loads(json.dumps(live))
    r = live.get("ribbon") or {}
    r["flown_nm"] = flown
    for s in (live.get("storms") or {}).get("storms") or []:
        s["ahead"] = (s.get("along_nm") or 0) >= flown
    return live


def highlight(client, model: str, f: dict) -> tuple[str, dict, float]:
    t = time.time()
    # Production's request for its own model (#715); Haiku 4.5 takes neither
    # field, any other model gets low effort.
    if model == DEFAULT_MODEL:
        extra = {"thinking": THINKING, "output_config": {"effort": EFFORT}}
    elif model.startswith("claude-haiku-4"):
        extra = {}
    else:
        extra = {"output_config": {"effort": "low"}}
    resp = client.messages.create(
        model=model, max_tokens=MAX_TOKENS, system=SYSTEM, **extra,
        messages=[{"role": "user", "content": "FACTS:\n" + json.dumps(f, indent=1, ensure_ascii=False)}],
    )
    text = next((b.text for b in resp.content if b.type == "text"), "").strip()
    return text, resp.usage, time.time() - t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--facts-only", action="store_true")
    ap.add_argument("--show-facts", action="store_true")
    ap.add_argument("--flown", type=float, action="append",
                    help="replay the tick as if this many NM were flown (repeatable)")
    args = ap.parse_args()

    client = None
    if not args.facts_only:
        import anthropic
        from dotenv import load_dotenv

        load_dotenv()
        client = anthropic.Anthropic()

    cases = []
    for p in args.files:
        live = json.loads(p.read_text())
        live = live.get("body", live)
        for flown in args.flown or [None]:
            cases.append((p, _at(live, flown)))

    total_in = total_out = rejected = 0
    for p, live in cases:
        f = facts(live)
        print(f"\n=== {p.name}  ({f['route']}, {f['now']}, {f['flight']})")
        g = live.get("glance") or {}
        if g:
            print("nutshell:", g.get("headline"))
        if args.facts_only or args.show_facts:
            print(json.dumps(f, indent=1, ensure_ascii=False))
        if client is None:
            continue
        text, usage, dt = highlight(client, args.model, f)
        total_in += usage.input_tokens
        total_out += usage.output_tokens
        words = len(text.split())
        print(f"HIGHLIGHT ({args.model}, {dt:.1f}s, {usage.input_tokens} in / {usage.output_tokens} out, "
              f"{words} words{'' if words <= MAX_WORDS else f' — OVER {MAX_WORDS}'}):\n  {text}")
        # The same check the tick runs: a line the grounding check would reject
        # never reaches a client, so a replay that does not show it is lying
        # about what the feature would do.
        reason = check_grounding(text, f)
        print(f"  grounding: {'ok' if reason is None else 'REJECTED — ' + reason}")
        flags = review_flags(text, f) if reason is None else []
        if flags:
            print(f"  flags: {'; '.join(flags)}")
        if reason is not None:
            rejected += 1
    if client is not None:
        from weatherbrief.costs import token_rates_for

        in_rate, out_rate = token_rates_for(args.model)  # per 1k; raises if unpriced
        cost = total_in / 1e3 * in_rate + total_out / 1e3 * out_rate
        print(f"\n{len(cases)} calls, {total_in} in / {total_out} out tokens, ${cost:.4f}"
              f" (${cost / len(cases):.5f} per tick), {rejected} rejected by grounding")


if __name__ == "__main__":
    sys.exit(main())
