"""Departure feasibility advisory evaluator (issue #829).

The departure counterpart of ``approach_feasibility`` (#509), with a much
smaller question: **is IFR available from the departure field at all?**

Nothing else in the advisory set knows. ``flight_category`` grades the
departure's ceiling/visibility and ``ifr_feasibility`` grades the route as if an
IFR departure were always possible — so at a field with no published IFR
procedures (LSZF Birrfeld, the pilot report behind this issue) a sub-VFR
departure read as "IFR is fine". In CH/DE and most of continental Europe, IFR
from such a field is only possible as a Z flight plan: a VFR departure followed
by a pick-up in the air. This card says so and points the pilot at it.

Design rules (agreed with the owner on #829):

1. **Signal = the ``procedures`` table only**, via the same
   ``get_runway_approaches`` lookup ``approach_feasibility`` reads. A field
   "has IFR procedures" iff it has at least one approach row **or** a
   user-declared approach (#510). AIP field 207 is deliberately not used: absent
   from ``nav.db`` and noisy in both directions where it exists.
2. **Never RED.** We cannot verify local departure rules, and delaying is
   always an option. The worst this card says is AMBER.
3. **One flight category.** Graded per model on
   ``airport_conditions.departure.condition_for_model(model)`` — the same value
   ``flight_category`` grades — so the two cards cannot disagree on what the
   departure is forecast to be.
4. **Exempt countries** (``EXEMPT_COUNTRIES``): where IFR departures from a
   field without procedures are routine (the UK, outside controlled airspace),
   the card stays GREEN with an informational note. An unknown country is
   **not** exempt — the flag is the conservative side.

Grade map (per model):

* any published or declared approach -> GREEN
* VFR at departure -> GREEN
* exempt country -> GREEN, with a note
* MVFR / IFR / LIFR -> AMBER
* lookup failed / coverage gap / no lookup / no condition -> UNAVAILABLE

Copy stays in the #509 register: it directs attention and never states a
regulatory verdict (the digest's ``check_regulatory_claim`` guardrail exists
because a briefing once called an IFR departure "the only legal option").
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryStatus,
    AirportClause,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)
from weatherbrief.models.airport_conditions import AirportApproaches, FlightCategory

ADVISORY_ID = "departure_feasibility"

# Countries where an IFR departure from a field with no published procedures is
# routine (UK: IFR outside controlled airspace). A module constant, not a user
# param — extended as feedback arrives, never inferred. The Crown dependencies
# (JE, GG, IM) have their own ISO codes and are deliberately not in it yet: they
# grade AMBER like the continent, the conservative side.
EXEMPT_COUNTRIES: frozenset[str] = frozenset({"GB"})


def _grade(
    category: FlightCategory, approaches: AirportApproaches, loc: str | None,
) -> tuple[AdvisoryStatus, str]:
    """One model's grade and detail (the text after "Dep ICAO: CAT — ")."""
    published = approaches.published
    if published:
        return AdvisoryStatus.GREEN, adv_t(
            "departure_feasibility.has_procedures", loc, count=len(published),
        )
    if approaches.has_declared_approach:
        return AdvisoryStatus.GREEN, adv_t(
            "departure_feasibility.declared", loc, icao=approaches.icao,
        )
    if category == FlightCategory.VFR:
        return AdvisoryStatus.GREEN, adv_t("departure_feasibility.vfr", loc)
    if approaches.iso_country in EXEMPT_COUNTRIES:
        return AdvisoryStatus.GREEN, adv_t("departure_feasibility.exempt", loc)
    # MVFR, IFR and LIFR alike: never RED (design rule 2).
    return AdvisoryStatus.AMBER, adv_t("departure_feasibility.no_procedures", loc)


@register
class DepartureFeasibilityEvaluator:
    """Does the departure field offer IFR at all, when the departure is below VFR?"""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id=ADVISORY_ID,
            name="Departure Feasibility",
            short_description="Is IFR available from the departure field?",
            description=(
                "Checks whether the departure field has any published "
                "instrument procedure, and only matters when the departure is "
                "forecast below VFR. A field with a published approach (or one "
                "you declared in your account settings) is green. A VFR "
                "departure is green. Below VFR at a field with no published "
                "procedure it is amber: in most of Europe, IFR from such a "
                "field means a VFR departure and a pick-up in the air, so check "
                "the pick-up altitude against the cloud. It is never red — "
                "local departure rules are not checked, and delaying is always "
                "an option. In the UK, where IFR departures outside controlled "
                "airspace are common, it stays green with a note."
            ),
            category="flight_rules",
            # The departure category moves with the departure hour, so a timing
            # scenario should re-grade it; no params, nothing to scan for.
            timing_class="cheap",
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        loc = ctx.locale

        # Could not run, or could not determine the picture (unknown ICAO,
        # procedure query raised, unsurveyed country). Same rule as
        # approach_feasibility: a data gap is neither "has IFR" nor "has none".
        if (
            ctx.departure_approaches is None
            or ctx.departure_approaches.lookup_failed
            or ctx.airport_conditions is None
        ):
            return RouteAdvisoryResult.from_per_model(ADVISORY_ID, [], params)

        approaches = ctx.departure_approaches
        dep = ctx.airport_conditions.departure

        per_model: list[ModelAdvisoryResult] = []
        for model in ctx.models:
            cond = dep.condition_for_model(model)
            if cond is None:
                per_model.append(ModelAdvisoryResult.build(
                    model=model, status=AdvisoryStatus.UNAVAILABLE,
                    detail=adv_t("no_data", loc), affected=0, total=0,
                    total_distance_nm=ctx.total_distance_nm,
                ))
                continue

            status, detail = _grade(cond.flight_category, approaches, loc)
            # The category is the clause head, so the aggregate prints the
            # cross-model consensus rather than this model's (#823).
            clause = AirportClause(
                end="dep", category=cond.flight_category, printed=True,
                head=f"{adv_t('airport.dep', loc)} {dep.icao}: ",
                suffix=f" — {detail}", locale=loc,
            )
            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status,
                detail=clause.text,
                affected=1 if status != AdvisoryStatus.GREEN else 0,
                total=1,
                total_distance_nm=ctx.total_distance_nm,
                airport_clauses=[clause],
            ))

        return RouteAdvisoryResult.from_per_model(ADVISORY_ID, per_model, params)
