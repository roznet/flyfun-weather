"""Invariants every published advisory result must satisfy (#578).

`tests/analysis/advisories/test_published_extent_consistency.py` asserted the
#571 extent rules against one synthetic fixture and only ever at the
``per_model`` layer. The predicates themselves are not test-only: they are
properties of a published :class:`RouteAdvisoryResult`, and the interesting
failures live where nothing was looking — above ``per_model``, and on real
packs. So they live here, in the library, and three callers share them:

* the unit tests, over synthetic contexts;
* ``tests/analysis/advisories/test_extent_invariants_on_corpus.py``, over real
  corpus packs when one is available;
* ``scripts/rerun_advisories_diff.py --check-invariants``, over the whole
  staging sweep.

That is the difference between "a reviewer finds it at site N" and "every site
at once".

Three things deliberately are NOT violations:

* an aggregate reading GREEN while a model reads RED — that is what MAJORITY
  means, and :func:`masked_flagged_models` reports it as an observation so the
  sweep can count it (see ``designs/advisories.md``, "Aggregation");
* UNAVAILABLE anything — a model with no data publishes no extent;
* a flagged result that publishes no extent measurement at all (``fronts``,
  which grades a distance to a boundary rather than a span of route). The rules
  are about a published extent contradicting itself or its verdict; where there
  is no extent there is nothing to contradict. See ``check_model_result``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from weatherbrief.models.advisories import (
    AdvisoryStatus,
    ModelAdvisoryResult,
    RouteAdvisoriesManifest,
    RouteAdvisoryResult,
    published_pct,
)

# Statuses that assert a problem. UNAVAILABLE is excluded everywhere: it means
# "no data", and a model with no data measures no extent.
FLAGGED = frozenset({AdvisoryStatus.RED, AdvisoryStatus.AMBER})

# Miles are published rounded to 0.1, so comparisons carry half of that.
_TOL_NM = 0.05

# "over 120nm/300nm" — the printed extent pair. Same shape the per-model
# printed-denominator test keys on.
_PRINTED_EXTENT = re.compile(r"(\d+)nm/(\d+)nm")


@dataclass(frozen=True)
class Violation:
    """One broken invariant, addressed to whoever has to fix it."""

    advisory_id: str
    # The per-model result's model name, or None for the aggregate layer.
    model: str | None
    rule: str
    detail: str

    def __str__(self) -> str:
        where = f"{self.advisory_id}/{self.model}" if self.model else f"{self.advisory_id}/aggregate"
        return f"{where}: [{self.rule}] {self.detail}"


def check_model_result(
    advisory_id: str, m: ModelAdvisoryResult
) -> list[Violation]:
    """The per-model extent invariants (#571), as predicates rather than prose."""
    out: list[Violation] = []

    def fail(rule: str, detail: str) -> None:
        out.append(Violation(advisory_id, m.model, rule, detail))

    if m.domain_nm > 0:
        # Recomputed with the very helper ``build()`` uses, so a field set by a
        # path that bypassed it is what shows up here.
        expected = published_pct(m.affected_nm, m.domain_nm)
        if abs(m.affected_pct - expected) > 0.05:
            fail(
                "pct_is_nm_over_domain",
                f"affected_pct={m.affected_pct} but {m.affected_nm}nm / "
                f"{m.domain_nm}nm = {expected}",
            )
        expected_mod = published_pct(m.affected_mod_nm, m.domain_nm)
        if abs(m.affected_mod_pct - expected_mod) > 0.05:
            fail(
                "mod_pct_is_nm_over_domain",
                f"affected_mod_pct={m.affected_mod_pct} but {m.affected_mod_nm}nm / "
                f"{m.domain_nm}nm = {expected_mod}",
            )

    if m.domain_nm > m.total_nm + _TOL_NM:
        fail(
            "domain_within_route",
            f"domain_nm {m.domain_nm} exceeds the route's {m.total_nm}",
        )
    if m.affected_nm > m.domain_nm + _TOL_NM:
        fail(
            "affected_within_domain",
            f"affected_nm {m.affected_nm} exceeds domain_nm {m.domain_nm}",
        )
    if m.affected_mod_nm > m.domain_nm + _TOL_NM:
        fail(
            "mod_within_domain",
            f"affected_mod_nm {m.affected_mod_nm} exceeds domain_nm {m.domain_nm}",
        )

    # The catch-all for the whole false-report class: something flagged must
    # publish the coverage that flagged it. Round 9's `affected_pct: 0.0` beside
    # `affected_points: 1` on a RED was one instance of it; this is the general
    # predicate rather than the one site a reviewer happened to read.
    #
    # Scoped to results that publish an extent *at all*. ``fronts`` is the one
    # evaluator that builds its result directly rather than through ``build()``
    # (fronts.py:344), because it grades a point-in-space crossing measured in
    # km — a distance to a boundary, not a span of route — so every extent field
    # stays at its zero default even on a graded AMBER/RED. Reading that as "a
    # RED with no coverage" would put a false violation on essentially every
    # staging pack with active frontal weather, which is the same broken-signal
    # failure this module exists to prevent, merely inverted (#583 review).
    # Publishing no measurement is outside this rule; publishing one and then
    # contradicting it is what the rule is for — so an evaluator that later
    # starts publishing an extent comes back under it with no edit here.
    if m.status in FLAGGED and (m.total_points > 0 or m.total_nm > 0):
        if m.affected_points <= 0 and m.affected_nm <= 0:
            fail(
                "flagged_has_coverage",
                f"status {m.status.value} with no coverage at all "
                f"(affected_points={m.affected_points}, affected_nm={m.affected_nm})",
            )
        elif m.affected_pct <= 0:
            fail(
                "flagged_has_coverage",
                f"status {m.status.value} publishes affected_pct=0.0 beside "
                f"affected_points={m.affected_points} / affected_nm={m.affected_nm} — "
                "0% reads as 'nothing wrong' to the digest and the API",
            )

    match = _PRINTED_EXTENT.search(m.detail)
    if match and m.domain_nm > 0:
        printed = int(match.group(2))
        if printed != round(m.domain_nm):
            fail(
                "printed_denominator_is_the_published_one",
                f"detail prints /{printed}nm but publishes domain_nm={m.domain_nm} "
                f"— {m.detail!r}",
            )
    return out


def check_advisory(a: RouteAdvisoryResult) -> list[Violation]:
    """Per-model invariants plus the aggregate layer above them.

    The aggregate publishes no extent fields of its own — its numbers reach the
    reader through ``aggregate_detail``, sourced from the representative model.
    So "the aggregate is self-consistent" means: it grades what a model graded,
    it names which one, and any extent it prints is that model's.
    """
    out: list[Violation] = []
    for m in a.per_model:
        out.extend(check_model_result(a.advisory_id, m))

    def fail(rule: str, detail: str) -> None:
        out.append(Violation(a.advisory_id, None, rule, detail))

    statuses = {m.status for m in a.per_model}
    agg = a.aggregate_status

    # The aggregate never invents a grade no model holds. True of both MAJORITY
    # (most common, ties to worst) and WORST; an aggregate outside the set means
    # something wrote it by hand.
    if agg != AdvisoryStatus.UNAVAILABLE and statuses and agg not in statuses:
        fail(
            "aggregate_is_a_model_status",
            f"aggregate {agg.value} held by no model ("
            f"{', '.join(sorted(s.value for s in statuses))})",
        )
    if agg != AdvisoryStatus.UNAVAILABLE and not a.per_model:
        fail(
            "aggregate_needs_a_model",
            f"aggregate {agg.value} with no per-model results behind it",
        )

    by_model = {m.model: m for m in a.per_model}
    rep = by_model.get(a.representative_model) if a.representative_model else None
    if a.representative_model and rep is None:
        fail(
            "representative_is_a_real_model",
            f"representative_model={a.representative_model!r} is not in per_model "
            f"({', '.join(sorted(by_model))})",
        )
    elif rep is not None and rep.status != agg:
        fail(
            "representative_carries_the_aggregate_status",
            f"representative {rep.model} is {rep.status.value} but the aggregate "
            f"reads {agg.value}",
        )
    elif a.representative_model is None and agg in statuses:
        # The client reads "whose geometry do we highlight?" off this field; a
        # None with a matching model means it silently falls back.
        fail(
            "representative_is_named",
            f"aggregate {agg.value} matches a model but representative_model is unset",
        )

    match = _PRINTED_EXTENT.search(a.aggregate_detail)
    if match:
        printed = int(match.group(2))
        domains = {
            round(m.domain_nm) for m in a.per_model
            if m.status == agg and m.domain_nm > 0
        }
        if domains and printed not in domains:
            fail(
                "aggregate_prints_a_published_denominator",
                f"aggregate_detail prints /{printed}nm, which is no "
                f"{agg.value} model's domain ({sorted(domains)}) — "
                f"{a.aggregate_detail!r}",
            )
    return out


def check_advisories(advisories: list[RouteAdvisoryResult]) -> list[Violation]:
    """Every invariant over a list of results (what ``evaluate_all`` returns)."""
    out: list[Violation] = []
    for a in advisories:
        out.extend(check_advisory(a))
    return out


def check_manifest(manifest: RouteAdvisoriesManifest) -> list[Violation]:
    """Every invariant over a published manifest (what a pack stores)."""
    return check_advisories(list(manifest.advisories))


def masked_flagged_models(a: RouteAdvisoryResult) -> list[ModelAdvisoryResult]:
    """Models flagged RED/AMBER under a *strictly calmer* aggregate.

    NOT a violation — under MAJORITY a single dissenting model does not move the
    aggregate, which is the documented choice (``designs/advisories.md``). It is
    reported so a replay can count it: an aggregate reading GREEN while a model
    reads RED prints reassuring text ("Smooth ride expected") over a RED model,
    and how often that happens across real packs is a number worth watching
    rather than rediscovering.
    """
    order = [AdvisoryStatus.GREEN, AdvisoryStatus.AMBER, AdvisoryStatus.RED]
    if a.aggregate_status not in order:
        return []
    agg_rank = order.index(a.aggregate_status)
    return [
        m for m in a.per_model
        if m.status in FLAGGED and order.index(m.status) > agg_rank
    ]


def masked_pairs(advisories: list[RouteAdvisoryResult]) -> list[tuple[str, ModelAdvisoryResult]]:
    """``(advisory_id, model_result)`` for every masked flagged model."""
    return [
        (a.advisory_id, m)
        for a in advisories
        for m in masked_flagged_models(a)
    ]
