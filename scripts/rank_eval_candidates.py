#!/usr/bin/env python3
"""Rank eval packs by how much a human label would teach us (dev-only workbench).

A season pull lands dozens of packs in staging; most confirm the status quo
("AMBER, pilot said consistent"). This scores each pack on cheap, deterministic
signals already in it and prints a review queue, so labelling time goes on the
informative ones:

- **debrief vs grade** — the pilot's per-category verdict against our grade for
  the advisories that category maps to (``ADVISORY_TAG_MAP``): "better" under a
  flagged grade is an over-warn, "worse" an under-warn; a cancelled flight we
  called GREEN, or a flown one we called RED, also scores.
- **model spread** — one model RED while another reads GREEN on the same advisory.
- **code drift** — re-grading with today's code (non-destructive) moves an
  aggregate, so the label decides which version is right.
- **thumbs** — a digest thumbs-down on the flight (``--feedback-json``: the
  archive's ``metadata/feedback.json``). Thumbs are pack-level: full weight
  when it is the staged pack, half when the pilot thumbed another briefing of
  the same flight.
- **duplicates** — same route and departure day: only the best-scoring pack
  stays in the queue, the rest are listed as duplicates.

A drift that most packs share (a code change that moves every pack the same
way) is reported once as systematic instead of scoring each pack. The queue is
picked greedily for **diversity**: each pick earns a bonus for the flagged
advisories the queue does not cover yet, so the top N spread across hazards
rather than repeating the most common one; a coverage table closes the report.

Already-labelled packs are skipped. Nothing is written to the packs.

Usage:
    source venv/bin/activate
    python scripts/rank_eval_candidates.py --area staging --since 2026-07-21
    python scripts/rank_eval_candidates.py --area staging --since 2026-07-21 \\
        --feedback-json /path/to/feedback.json --top 15 --output queue.md
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from weatherbrief.debriefs.taxonomy import ADVISORY_TAG_MAP  # noqa: E402
from weatherbrief.eval_workbench import corpus, rerun  # noqa: E402

FLAGGED = {"amber", "red"}
# Advisories whose model spread or drift is worth a reviewer's eye. Diagnostics
# (model agreement) disagree by design and would drown the signal.
_SPREAD_SKIP = {"model_agreement", "dd_nwp_agreement"}


@dataclass
class Scored:
    corpus_id: str
    route: str
    departure: str
    days_out: int
    assessment: str | None
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    flagged: set[str] = field(default_factory=set)  # advisories graded AMBER/RED
    drift: list[str] = field(default_factory=list)  # "aid old->new" moves

    def add(self, points: int, reason: str) -> None:
        self.score += points
        self.reasons.append(reason)


def _statuses(manifest) -> dict[str, tuple[str, dict[str, str]]]:
    """advisory_id -> (aggregate status, {model: status})."""
    if manifest is None:
        return {}
    return {
        a.advisory_id: (
            a.aggregate_status.value,
            {m.model: m.status.value for m in a.per_model},
        )
        for a in manifest.advisories
    }


def _score_debrief(s: Scored, pack_dir: Path, saved: dict) -> None:
    path = pack_dir / "debrief.json"
    if not path.exists():
        return
    debrief = json.loads(path.read_text())
    decision = debrief.get("decision")
    outcomes = debrief.get("outcomes") or {}
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)

    by_tag: dict[str, list[str]] = defaultdict(list)
    for advisory_id, tag in ADVISORY_TAG_MAP.items():
        by_tag[tag.value].append(advisory_id)

    for tag, verdict in outcomes.items():
        graded = [
            (aid, saved[aid][0]) for aid in by_tag.get(tag, []) if aid in saved
        ]
        flagged = [(aid, st) for aid, st in graded if st in FLAGGED]
        if verdict == "better" and flagged:
            worst = "red" if any(st == "red" for _, st in flagged) else "amber"
            s.add(3 if worst == "red" else 2,
                  f"over-warn: pilot {tag} better, we {worst.upper()} "
                  f"({', '.join(aid for aid, _ in flagged)})")
        elif verdict == "worse":
            calm = [aid for aid, st in graded if st not in FLAGGED]
            s.add(4, f"under-warn: pilot {tag} worse"
                     + (f", we calm on {', '.join(calm)}" if calm else ""))
        elif verdict == "consistent" and flagged:
            s.add(0, f"confirmed: {tag} consistent with our flag")

    if decision == "cancelled" and s.assessment == "GREEN":
        s.add(3, "cancelled although we said GREEN")
    elif decision == "flown" and s.assessment == "RED":
        s.add(2, "flown although we said RED")


def _score_spread(s: Scored, saved: dict) -> None:
    hits = [
        aid for aid, (_agg, per_model) in saved.items()
        if aid not in _SPREAD_SKIP
        and "red" in per_model.values() and "green" in per_model.values()
    ]
    if hits:
        s.add(min(len(hits), 2), f"model spread RED/GREEN on {', '.join(sorted(hits))}")


def _drift(saved: dict, now: dict) -> list[str]:
    return [
        f"{aid} {saved[aid][0]}->{now[aid][0]}"
        for aid in sorted(saved.keys() & now.keys())
        if aid not in _SPREAD_SKIP and saved[aid][0] != now[aid][0]
        and "unavailable" not in (saved[aid][0], now[aid][0])
    ]


def _score_drift(scored: list[Scored], systemic_share: float) -> list[str]:
    """Score per-pack drift, leaving out moves most packs share; return those."""
    counts: dict[str, int] = defaultdict(int)
    for s in scored:
        for move in s.drift:
            counts[move] += 1
    systemic = sorted(m for m, n in counts.items() if n >= systemic_share * len(scored))
    for s in scored:
        own = [m for m in s.drift if m not in systemic]
        if own:
            s.add(min(len(own), 2), f"today's code differs: {'; '.join(own[:3])}"
                                    + (" …" if len(own) > 3 else ""))
    return systemic


def _pick_diverse(ranked: list[Scored], n: int) -> list[Scored]:
    """Greedy top-n: score plus 1 per flagged advisory not yet in the queue (max 2)."""
    queue: list[Scored] = []
    covered: set[str] = set()
    pool = list(ranked)
    while pool and len(queue) < n:
        best = max(pool, key=lambda x: (x.score + min(len(x.flagged - covered), 2), -pool.index(x)))
        pool.remove(best)
        new = sorted(best.flagged - covered)
        if new:
            best.reasons.append(f"adds coverage: {', '.join(new[:4])}" + (" …" if len(new) > 4 else ""))
        covered |= best.flagged
        queue.append(best)
    return queue


def _thumbs(feedback_json: str | None) -> dict[str, list[dict]]:
    """flight_id -> digest thumb rows (any pack of the flight)."""
    if not feedback_json:
        return {}
    out: dict[str, list[dict]] = defaultdict(list)
    for row in json.loads(Path(feedback_json).read_text()):
        if row.get("category") == "digest_rating" and row.get("flight_id"):
            out[row["flight_id"]].append(row)
    return out


def _source(pack_dir: Path) -> tuple[str | None, str | None]:
    """(flight id, pack dir name) from the ``_source.json`` breadcrumb."""
    src = pack_dir / "_source.json"
    if not src.exists():
        return None, None
    path = Path(json.loads(src.read_text())["source_pack_dir"])
    return path.parent.name, path.name


def _same_pack(thumb_ts: str, pack_dir_name: str | None) -> bool:
    """A thumb's pack_timestamp and the pack dir name encode the same instant."""
    return bool(pack_dir_name) and pack_dir_name[:19].replace("-", ":")[11:] == thumb_ts[11:19] \
        and pack_dir_name[:10] == thumb_ts[:10]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--area", choices=("staging", "corpus"), default="staging")
    ap.add_argument("--since", help="Only packs departing on/after YYYY-MM-DD")
    ap.add_argument("--feedback-json", help="Archive metadata/feedback.json (digest thumbs)")
    ap.add_argument("--no-rerun", action="store_true", help="Skip the code-drift re-grade")
    ap.add_argument("--top", type=int, default=15, help="Size of the review queue")
    ap.add_argument("--systemic-share", type=float, default=0.25,
                    help="A drift on at least this share of packs is reported once, not scored")
    ap.add_argument("--output", help="Also write the report as markdown here")
    args = ap.parse_args()

    thumbs = _thumbs(args.feedback_json)
    scored: list[Scored] = []
    for pack in corpus.list_corpus(args.area):
        meta = pack.meta
        if pack.label is not None and pack.label.assessments:
            continue
        if args.since and meta.target_date < args.since:
            continue
        pack_dir = corpus.pack_path(pack.corpus_id, args.area)
        s = Scored(pack.corpus_id, meta.route, meta.departure_time[:16],
                   meta.days_out, meta.assessment)
        saved_manifest = rerun.load_saved_manifest(pack_dir)
        saved = _statuses(saved_manifest)
        s.flagged = {aid for aid, (agg, _m) in saved.items()
                     if agg in FLAGGED and aid not in _SPREAD_SKIP}

        _score_debrief(s, pack_dir, saved)
        _score_spread(s, saved)
        if not args.no_rerun and saved_manifest is not None:
            try:
                s.drift = _drift(saved, _statuses(rerun.rerun_manifest(pack_dir, saved_manifest)))
            except Exception as exc:  # a stale label-only pack cannot be re-run
                s.reasons.append(f"(re-run failed: {type(exc).__name__})")
        fid, pack_name = _source(pack_dir)
        for row in thumbs.get(fid, []) if fid else []:
            if row.get("sentiment") != "down":
                continue
            here = _same_pack(str(row.get("pack_timestamp") or ""), pack_name)
            s.add(2 if here else 1,
                  f"digest thumbs-down{'' if here else ' (on another briefing of this flight, '
                                        + str(row.get('pack_timestamp'))[:10] + ')'}: "
                  f"{(row.get('comment') or '')[:80]!r}")
        scored.append(s)

    systemic = _score_drift(scored, args.systemic_share)

    # One pack per route+day: keep the best-scoring, list the rest as duplicates.
    best: dict[tuple[str, str], Scored] = {}
    dupes: list[Scored] = []
    for s in sorted(scored, key=lambda x: (-x.score, x.days_out)):
        key = (s.route, s.departure[:10])
        if key in best:
            dupes.append(s)
        else:
            best[key] = s
    ranked = sorted(best.values(), key=lambda x: (-x.score, x.departure))
    queue = _pick_diverse(ranked, args.top)
    rest = [s for s in ranked if s not in queue]

    pool_cov: dict[str, int] = defaultdict(int)
    queue_cov: dict[str, int] = defaultdict(int)
    for s in ranked:
        for aid in s.flagged:
            pool_cov[aid] += 1
    for s in queue:
        for aid in s.flagged:
            queue_cov[aid] += 1

    lines = [f"# Review queue — {args.area}"
             + (f", departures from {args.since}" if args.since else ""),
             "",
             f"{len(scored)} unlabelled packs, {len(ranked)} after de-duplicating "
             f"route+day ({len(dupes)} duplicates).",
             ""]
    if systemic:
        lines += [f"Systematic drift (on ≥{args.systemic_share:.0%} of packs, not scored): "
                  + "; ".join(systemic), ""]
    lines += [f"## Review first (top {len(queue)}, picked for score and advisory diversity)", "",
              "| score | pack | grade | why |", "|---:|---|---|---|"]
    for s in queue:
        lines.append(f"| {s.score} | `{s.corpus_id}` | {s.assessment or '-'} | "
                     + ("; ".join(s.reasons) or "-") + " |")
    lines += ["", "## Then (skim or accept as-is)", "",
              "| score | pack | grade | why |", "|---:|---|---|---|"]
    for s in rest:
        lines.append(f"| {s.score} | `{s.corpus_id}` | {s.assessment or '-'} | "
                     + ("; ".join(s.reasons) or "-") + " |")
    lines += ["", "## Advisory coverage (packs flagging it AMBER/RED)", "",
              "| advisory | in pool | in review queue |", "|---|---:|---:|"]
    for aid in sorted(pool_cov, key=lambda a: -pool_cov[a]):
        lines.append(f"| {aid} | {pool_cov[aid]} | {queue_cov.get(aid, 0)} |")
    if dupes:
        lines += ["", "## Duplicates (same route and day as a pack above)", ""]
        lines += [f"- `{s.corpus_id}` (score {s.score})" for s in dupes]
    report = "\n".join(lines) + "\n"
    print(report)
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
