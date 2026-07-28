"""Approach feasibility advisory evaluator (issue #509).

Answers the arrival question nothing else in the briefing does: **can I get in,
on a runway I can also land on?**

``flight_category`` grades the destination's ceiling/visibility, ``airport_wind``
grades the wind, and ``enrich_wind`` picks the wind-best runway with no approach
awareness at all — so the briefing can say *"destination IFR, ceiling 600 ft,
best runway 24"* at a field where 24 has no approach and the ILS serves 06. This
evaluator owns that intersection: ceiling **and** wind **and** infrastructure.

Design rules that make this correct rather than merely plausible (all from #509):

1. **One minima table.** Decision heights come from
   ``analysis.alternate_requirement.APPROACH_CLASS_PROXY`` via
   ``proxy_for_approach`` — the same estimates the alternate-requirement card
   shows. A second table would drift.
2. **Asymmetric uncertainty.** Those DHs are *estimates*. Estimate uncertainty
   may push toward AMBER, never toward RED: RED needs a hard fact (no IAP at
   all) or a ceiling below even the **best-case** DH.
3. **Flag circling, don't grade it.** ``nav.db`` carries no circling minima and
   no "circling not authorised" flag, so a circling-only approach is surfaced as
   a compromise (AMBER), never given a computed circling verdict.
4. **Alignment is brittle.** Wind direction spreads across models at range and
   alignment is discrete, so the grade is computed per model and the registry's
   majority aggregation absorbs the disagreement. Near the wind boundary the
   verdict degrades to AMBER rather than flipping GREEN <-> RED.

The destination's flight category selects which logic applies — the alignment /
circling / tailwind reasoning only earns its keep once the pilot can no longer be
expected to complete the arrival visually:

* **VFR** -> GREEN, always. Approach infrastructure is irrelevant.
* **MVFR** -> IAP presence only, capped at AMBER (any approach -> GREEN, none ->
  AMBER). Never RED.
* **IFR / LIFR** -> the full logic below.

Known gap (deliberate, #509 design constraint 4): at D-0 this should prefer the
TAF over the NWP consensus so the card cannot contradict the alternates card
beside it. Advisories run at pipeline step 3 and METAR/TAF are fetched at step
3.5, so ``RouteContext`` has no observation to read; the NWP consensus in
``airport_conditions.arrival`` is what is graded today.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.analysis.alternate_requirement import ApproachProxy, proxy_for_approach
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)
from weatherbrief.models.airport_conditions import (
    AirportApproaches,
    AirportModelCondition,
    FlightCategory,
    RunwayApproach,
    RunwayWind,
)

ADVISORY_ID = "approach_feasibility"

# Categories at which the alignment / circling / tailwind logic applies. Above
# these the pilot completes the landing visually, so a misaligned or
# circling-only approach is not a penalty.
_FULL_LOGIC_CATEGORIES = (FlightCategory.IFR, FlightCategory.LIFR)


@dataclass(frozen=True)
class _EndPlan:
    """One candidate arrival: a straight-in approach paired with its wind."""

    approach: RunwayApproach
    wind: RunwayWind
    proxy: ApproachProxy

    @property
    def tailwind_kt(self) -> float:
        """Positive when the wind pushes from behind on this end."""
        return -self.wind.headwind_kt


class _Softening(str, Enum):
    """Why (or whether) a no-usable-straight-in verdict softens off RED.

    The value doubles as the message-key suffix, so the grade and the copy
    cannot be chosen from different tests — the bug this enum replaces.
    """

    #: The ceiling supports circling — a real option, so say "plan for circling".
    CIRCLING = "circling"
    #: We could not resolve an approach's alignment, or the wind model did not
    #: cover a served end. Genuine uncertainty (design rule 2), so it softens —
    #: but the copy must name THAT reason, never borrow the circling advice.
    UNCERTAIN = "uncertain"
    #: Nothing softens it.
    NONE = "blocked"

    @property
    def status(self) -> AdvisoryStatus:
        return (
            AdvisoryStatus.RED if self is _Softening.NONE
            else AdvisoryStatus.AMBER
        )


@dataclass(frozen=True)
class _GradedPlan:
    """One candidate arrival with its two failure axes kept separate.

    ``status`` is the verdict for this candidate, but ``wind`` and ``minima``
    survive alongside it because "no usable straight-in" has two very different
    causes — the wind ruled the end out, or the end's own approach minima did —
    and they need different copy.
    """

    wind: AdvisoryStatus
    minima: AdvisoryStatus
    plan: _EndPlan

    @property
    def status(self) -> AdvisoryStatus:
        return AdvisoryStatus.worst([self.wind, self.minima])


def _proxy(approach: RunwayApproach) -> ApproachProxy:
    """Estimated minima for one approach — never ``None`` for a present IAP.

    ``proxy_for_approach`` only returns ``None`` for ``has_iap=False``, and the
    caller has already established that this approach exists; an unknown or
    unmapped ``approach_type`` degrades to the most-demanding non-precision
    range rather than to nothing.
    """
    proxy = proxy_for_approach(approach.approach_type, has_iap=True)
    assert proxy is not None  # noqa: S101 - narrows the type; see docstring
    return proxy


def _best_case_minima(approaches: list[RunwayApproach]) -> tuple[float, float]:
    """Lowest plausible (decision height ft, visibility m) across the approaches.

    The *best case* deliberately: a forecast below this is below every plate the
    field could hold, which is the only ceiling/visibility fact hard enough to
    justify RED (design rule 2). Callers must pass a non-empty list.
    """
    proxies = [_proxy(a) for a in approaches]
    return min(p.dh_lo for p in proxies), min(p.vis_lo for p in proxies)


def _wind_for_end(cond: AirportModelCondition, runway_id: str) -> RunwayWind | None:
    """The model's wind components on one runway end, if it computed them."""
    key = runway_id.strip().upper()
    return next((r for r in cond.all_runways if r.runway_id.strip().upper() == key), None)


def _straight_in_plans(
    cond: AirportModelCondition, approaches: AirportApproaches,
) -> list[_EndPlan]:
    """Every straight-in approach whose served end also has wind components.

    An approach whose end the wind model does not cover is dropped rather than
    assumed benign — it simply cannot be graded, and the remaining plans (or the
    absence of any) carry the verdict.
    """
    plans: list[_EndPlan] = []
    for approach in approaches.approaches:
        if not approach.runway_id:
            continue
        wind = _wind_for_end(cond, approach.runway_id)
        if wind is None:
            continue
        plans.append(_EndPlan(
            approach=approach,
            wind=wind,
            proxy=_proxy(approach),
        ))
    return plans


def _plan_wind_status(
    plan: _EndPlan, tailwind_limit_kt: float, crosswind_limit_kt: float,
) -> AdvisoryStatus:
    """Grade the wind on one straight-in candidate.

    GREEN needs components genuinely within limits — no tailwind at all and a
    crosswind inside the limit. A tailwind that is still within the limit is a
    compromise (AMBER); beyond it the end is not usable straight-in, which is
    the only wind outcome that can feed the RED path.
    """
    tail = plan.tailwind_kt
    if tail > tailwind_limit_kt:
        return AdvisoryStatus.RED
    if tail > 0 or plan.wind.crosswind_kt > crosswind_limit_kt:
        return AdvisoryStatus.AMBER
    return AdvisoryStatus.GREEN


def _band_status(value: float | None, lo: float, hi: float) -> AdvisoryStatus:
    """Grade a forecast value against an estimated-minima band ``[lo, hi]``.

    Clear of the band (``>= hi``, the worst-case plate) -> GREEN; inside it ->
    AMBER, because the plate decides and we do not have it; below even the best
    case -> RED. This is where design rule 2 lives: the AMBER middle is exactly
    the width of our uncertainty about the published minima.
    """
    if value is None:
        return AdvisoryStatus.GREEN
    if value < lo:
        return AdvisoryStatus.RED
    if value < hi:
        return AdvisoryStatus.AMBER
    return AdvisoryStatus.GREEN


def _minima_status(
    cond: AirportModelCondition, proxy: ApproachProxy,
) -> AdvisoryStatus:
    """Grade ceiling AND visibility against one approach's estimated minima.

    A ceiling of ``None`` means the model found no BKN/OVC layer, which clears
    everything. A visibility of ``None`` means the model does not publish one
    (ECMWF visibility is GRIB-only) — the axis is skipped rather than flagged,
    matching ``flight_category``, which also does not read an absent visibility
    as a poor one.
    """
    return AdvisoryStatus.worst([
        _band_status(cond.ceiling_ft, proxy.dh_lo, proxy.dh_hi),
        _band_status(cond.visibility_m, proxy.vis_lo, proxy.vis_hi),
    ])


def _rank(status: AdvisoryStatus) -> int:
    """Order statuses best-first so the most usable arrival plan wins."""
    return {
        AdvisoryStatus.GREEN: 0,
        AdvisoryStatus.AMBER: 1,
        AdvisoryStatus.UNAVAILABLE: 2,
        AdvisoryStatus.RED: 3,
    }[status]


def _grade_full(
    cond: AirportModelCondition,
    approaches: AirportApproaches,
    params: dict[str, float],
    loc: str | None,
) -> tuple[AdvisoryStatus, str]:
    """IFR/LIFR logic: alignment, circling and tailwind all apply."""
    tailwind_limit = float(params.get("tailwind_limit_kt", 10))
    crosswind_limit = float(params.get("crosswind_limit_kt", 20))
    circling_ceiling = float(params.get("circling_ceiling_ft", 1000))

    if not approaches.has_iap:
        key = (
            "approach_feasibility.no_iap" if approaches.has_procedure_data
            else "approach_feasibility.no_procedure_data"
        )
        return AdvisoryStatus.RED, adv_t(key, loc, icao=approaches.icao)

    ceiling = cond.ceiling_ft

    # Hard forecast fact — below every plate the field could hold. Checked before
    # the wind so a genuinely un-shootable approach reads as such regardless of
    # which runway the wind favours.
    best_dh, best_vis = _best_case_minima(approaches.approaches)
    if ceiling is not None and ceiling < best_dh:
        return AdvisoryStatus.RED, adv_t(
            "approach_feasibility.below_minima", loc,
            ceiling=int(ceiling), dh=int(best_dh),
        )
    if cond.visibility_m is not None and cond.visibility_m < best_vis:
        return AdvisoryStatus.RED, adv_t(
            "approach_feasibility.below_vis_minima", loc,
            vis=int(cond.visibility_m), min_vis=int(best_vis),
        )

    # Straight-in candidates, best arrival first. Wind and minima are kept
    # apart, not just merged into one verdict: when no candidate is usable, WHY
    # it is unusable decides which story the pilot gets (see the fallback).
    graded = sorted(
        (
            _GradedPlan(
                wind=_plan_wind_status(p, tailwind_limit, crosswind_limit),
                minima=_minima_status(cond, p.proxy),
                plan=p,
            )
            for p in _straight_in_plans(cond, approaches)
        ),
        key=lambda g: (_rank(g.status), g.plan.tailwind_kt, g.plan.wind.crosswind_kt),
    )

    best = graded[0] if graded else None
    if best is not None and best.status != AdvisoryStatus.RED:
        return best.status, adv_t(
            "approach_feasibility.straight_in", loc,
            approach=_approach_label(best.plan.approach),
            runway=best.plan.wind.runway_id,
            wind=_wind_note(best.plan, loc),
        )

    # No usable straight-in. Neither an approach we could not align, nor a
    # straight-in end the wind model did not cover, may drive RED (design
    # rule 2) — both are our uncertainty, not a fact about the arrival. Ceiling
    # decides whether circling is even on the table, but we never compute a
    # circling verdict (design rule 3), only whether to soften to AMBER.
    alignment_unresolved = any(
        not a.runway_id and not a.circling for a in approaches.approaches
    )
    wind_unresolved = bool(approaches.served_runway_ids) and not graded
    circling_supported = ceiling is None or ceiling >= circling_ceiling
    # ONE discriminator drives both the grade and the copy. They were split
    # before — status off `circling_supported or <uncertainty>`, copy off
    # `circling_supported` alone — which let an unrelated unresolved approach
    # soften the grade to AMBER while the text still made the hard claim, and
    # (worse) let the misalignment branch advise "plan for circling" at a
    # ceiling that will not support it. Softening for uncertainty is correct,
    # but it has to say so in its own words rather than borrow the circling copy.
    if circling_supported:
        softening = _Softening.CIRCLING
    elif alignment_unresolved or wind_unresolved:
        softening = _Softening.UNCERTAIN
    else:
        softening = _Softening.NONE

    # Two different failures land here and they are NOT the same story. If an
    # end the wind is happy with exists and is blocked only by its own
    # approach's minima, there is no misalignment at all — saying "wind favours
    # 20, approaches serve 02, 20 — plan for circling" would name the served,
    # wind-favoured runway as if it were unserved. This is rule 2 applied per
    # approach rather than airport-wide: the forecast is below the best case for
    # *that* end's approach, even though a lower-minima approach exists on an
    # end the wind has ruled out.
    minima_blocked = [
        g for g in graded
        if g.wind != AdvisoryStatus.RED and g.minima == AdvisoryStatus.RED
    ]
    if minima_blocked:
        closest = min(minima_blocked, key=lambda g: g.plan.proxy.dh_lo)
        return softening.status, adv_t(
            f"approach_feasibility.minima_{softening.value}", loc,
            runway=closest.plan.wind.runway_id,
            approach=_approach_label(closest.plan.approach),
            dh=int(closest.plan.proxy.dh_lo),
        )

    # Every approach-served end is out on wind (or there is nothing straight-in
    # to grade) — the genuine misalignment case.
    wind_best = cond.best_runway.runway_id if cond.best_runway else None
    served = ", ".join(sorted(approaches.served_runway_ids)) or "-"

    return softening.status, adv_t(
        f"approach_feasibility.misaligned_{softening.value}", loc,
        served=served, wind_runway=wind_best or "-",
        ceiling=int(ceiling) if ceiling is not None else 0,
    )


def _approach_label(approach: RunwayApproach) -> str:
    """Short label for one approach: its class, falling back to its name."""
    return approach.approach_type or approach.name or "IAP"


def _wind_note(plan: _EndPlan, loc: str | None) -> str:
    """Human phrasing of the wind on the chosen end."""
    tail = plan.tailwind_kt
    if tail > 0:
        return adv_t("approach_feasibility.tailwind", loc, kt=round(tail))
    return adv_t("approach_feasibility.headwind", loc, kt=round(-tail))


def _grade_mvfr(
    approaches: AirportApproaches, loc: str | None,
) -> tuple[AdvisoryStatus, str]:
    """MVFR: IAP presence only, capped at AMBER. Never RED."""
    if approaches.has_iap:
        return AdvisoryStatus.GREEN, adv_t(
            "approach_feasibility.mvfr_ok", loc, count=len(approaches.approaches),
        )
    return AdvisoryStatus.AMBER, adv_t("approach_feasibility.mvfr_no_iap", loc)


@register
class ApproachFeasibilityEvaluator:
    """Is the instrument approach aligned with the runway the wind favours?"""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id=ADVISORY_ID,
            name="Approach Feasibility",
            short_description="Can you get in, on a runway you can also land on?",
            description=(
                "Joins three facts the briefing otherwise reports separately at "
                "the destination: the ceiling, the runway the wind favours, and "
                "which runways actually have a published instrument approach. "
                "A VFR destination is always green — it is visually reachable. "
                "At MVFR only the presence of an approach is checked (no "
                "approach at all is amber; alignment is not a penalty when the "
                "landing can be completed visually). At IFR/LIFR the full logic "
                "applies: green for a straight-in approach to a runway whose "
                "wind components are within limits and a ceiling clear of the "
                "estimated minima; amber for a compromise (circling required, "
                "ceiling inside the estimated minima band, or a tailwind still "
                "within limits); red only for a hard fact — no approach at all, "
                "a ceiling below even the best-case decision height, or an "
                "approach-served runway with an out-of-limits tailwind and no "
                "ceiling for circling. Decision heights are estimates shared "
                "with the alternate-requirement card, so uncertainty can only "
                "soften a grade, never harden it."
            ),
            category="flight_rules",
            timing_class="cheap",
            # Deliberately NOT timing_hint: the ceiling half of this advisory is
            # the same destination burn-off case ``flight_category`` already
            # hints on, and the wind half follows ``airport_wind``, which does
            # not hint either. A second hint on one phenomenon adds no lever.
            parameters=[
                AdvisoryParameterDef(
                    key="tailwind_limit_kt",
                    label="Tailwind limit",
                    description=(
                        "Tailwind on an approach-served runway above this is "
                        "treated as not usable straight-in"
                    ),
                    type="speed", unit="kt",
                    default=10, min=0, max=20, step=1,
                    audience="pilot",
                ),
                AdvisoryParameterDef(
                    key="crosswind_limit_kt",
                    label="Crosswind limit",
                    description=(
                        "Crosswind on an approach-served runway above this is a "
                        "compromise (amber). Crosswind severity itself is graded "
                        "by the Airport Wind advisory"
                    ),
                    type="speed", unit="kt",
                    default=20, min=5, max=40, step=5,
                    audience="pilot",
                ),
                AdvisoryParameterDef(
                    key="circling_ceiling_ft",
                    label="Circling ceiling",
                    description=(
                        "Assumed ceiling needed to circle. Below this a "
                        "misaligned approach can no longer be salvaged visually"
                    ),
                    type="altitude", unit="ft",
                    default=1000, min=500, max=2500, step=100,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        loc = ctx.locale

        # The collection step could not run (old pack / no nav.db), or it ran
        # and could not determine the approach picture (unknown ICAO, procedure
        # query raised). Absent data is not a clear approach — and, just as
        # importantly, it is not a *missing* approach either: grading it would
        # turn a data gap into the grade map's most severe input.
        if (
            ctx.arrival_approaches is None
            or ctx.arrival_approaches.lookup_failed
            or ctx.airport_conditions is None
        ):
            return RouteAdvisoryResult.from_per_model(ADVISORY_ID, [], params)

        approaches = ctx.arrival_approaches
        arr = ctx.airport_conditions.arrival

        per_model: list[ModelAdvisoryResult] = []
        for model in ctx.models:
            cond = arr.condition_for_model(model)
            if cond is None:
                per_model.append(ModelAdvisoryResult.build(
                    model=model, status=AdvisoryStatus.UNAVAILABLE,
                    detail=adv_t("no_data", loc), affected=0, total=0,
                    total_distance_nm=ctx.total_distance_nm,
                ))
                continue

            if cond.flight_category == FlightCategory.VFR:
                status, detail = AdvisoryStatus.GREEN, adv_t(
                    "approach_feasibility.vfr", loc,
                )
            elif cond.flight_category in _FULL_LOGIC_CATEGORIES:
                status, detail = _grade_full(cond, approaches, params, loc)
            else:
                status, detail = _grade_mvfr(approaches, loc)

            label = adv_t("airport.arr", loc)
            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status,
                detail=f"{label} {arr.icao}: {cond.flight_category.value} — {detail}",
                affected=1 if status != AdvisoryStatus.GREEN else 0,
                total=1,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model(ADVISORY_ID, per_model, params)
