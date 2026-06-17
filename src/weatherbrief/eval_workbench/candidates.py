"""Candidate scoring + coverage-aware selection for the labelling corpus.

Sampling moves from "by route / advisory-count" to "by meteorological
situation" (#254): we want at least a few labelled packs in every coverage cell,
and we want the SME's time spent on the *interesting* packs — the AMBER-bias
suspects, the red-flexibility cases, and the trend-exercising ones — rather than
on yet another all-green benign leg.

Pure logic over ``CorpusMeta`` so it's unit-testable without real packs.
"""

from __future__ import annotations

from collections import Counter

from weatherbrief.eval_workbench.corpus import CorpusMeta

# Why a pack is worth labelling, and how much each reason is worth.
_REASON_WEIGHTS: dict[str, int] = {
    "amber_all_green": 3,      # AMBER assessment despite all-green advisories
    "icing_plus_convective": 3,
    "trend": 2,                # carries a previous_digest -> exercises `trend`
    "multi_red": 2,
    "single_red": 1,
}

# Each new coverage cell a pick fills is worth more than any quality signal, so
# selection fills the matrix first, then ranks the rest by interest.
_COVERAGE_WEIGHT = 10


def candidate_reasons(meta: CorpusMeta) -> list[str]:
    """Human-readable reasons this pack is an interesting label candidate."""
    sits = set(meta.situations)
    reasons: list[str] = []
    if meta.assessment == "AMBER" and "all_green" in sits:
        reasons.append("amber_all_green")
    if "icing_plus_convective" in sits:
        reasons.append("icing_plus_convective")
    if "previous_digest" in sits:
        reasons.append("trend")
    if "multi_red" in sits:
        reasons.append("multi_red")
    elif "single_red" in sits:
        reasons.append("single_red")
    return reasons


def base_score(meta: CorpusMeta) -> int:
    return sum(_REASON_WEIGHTS.get(r, 0) for r in candidate_reasons(meta))


def select_candidates(
    metas: list[CorpusMeta], *, limit: int, min_per_cell: int = 1
) -> list[dict]:
    """Greedily pick packs that fill coverage cells first, then by interest.

    Returns ``[{corpus_id, score, reasons, new_cells}, ...]`` in selection
    order (most valuable first), capped at ``limit``.
    """
    remaining = list(metas)
    covered: Counter[str] = Counter()
    selected: list[dict] = []

    while remaining and len(selected) < limit:
        best = None
        best_gain = None
        for meta in remaining:
            new_cells = [s for s in meta.situations if covered[s] < min_per_cell]
            gain = len(new_cells) * _COVERAGE_WEIGHT + base_score(meta)
            if best_gain is None or gain > best_gain:
                best, best_gain, best_new = meta, gain, new_cells
        if best is None:
            break
        remaining.remove(best)
        for s in best.situations:
            covered[s] += 1
        selected.append({
            "corpus_id": best.corpus_id,
            "score": best_gain,
            "reasons": candidate_reasons(best),
            "new_cells": best_new,
            "situations": best.situations,
            "route": best.route,
            "days_out": best.days_out,
            "assessment": best.assessment,
        })
    return selected
