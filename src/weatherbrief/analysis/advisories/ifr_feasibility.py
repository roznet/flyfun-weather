"""IFR feasibility advisory — overall IFR flight viability assessment.

Composite advisory combining airport conditions, en-route icing exposure,
and convective activity into a single go/no-go style assessment for IFR flights.
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    FlaggedCell,
    apply_airport_endpoints,
    below_coverage,
    build_regions,
    build_ribbon,
    driving_method_id,
    RouteExtent,
    format_extent,
    route_extent,
    ribbon_peak,
    worst_severity,
)
from weatherbrief.analysis.advisories.aircraft_icing import (
    IcingModelGrade,
    grade_aircraft_icing_model,
    resolve_aircraft_icing_params,
)
from weatherbrief.analysis.advisories.convective_character import (
    classify_route_character,
    resolve_character_params,
)
from weatherbrief.analysis.advisories.convective_grading import (
    ConvectiveModelGrade,
    grade_convective_model,
    resolve_convective_params,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryHighlights,
    AdvisoryParameterDef,
    AdvisoryStatus,
    AirportClause,
    ConvectiveCharacter,
    HighlightSeverity,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)
from weatherbrief.models.airport_conditions import FlightCategory

# Parameters this composite used to grade its own icing axis with, before it
# took the ``aircraft_icing`` card's grade (#827). Stored profiles may still
# carry them; they are ignored and not echoed back as if they had been applied.
_RETIRED_ICING_PARAMS = frozenset({
    "extent_pct_amber", "extent_pct_red", "extent_min_nm", "icing_altitude_buffer_ft",
})


def _worst_status(*statuses: AdvisoryStatus) -> AdvisoryStatus:
    """Return the most severe status from the given values."""
    return AdvisoryStatus.worst(list(statuses))


def _check_airport_ifr(
    ctx: RouteContext,
    model: str,
    min_dep_ceiling_ft: float,
    min_arr_ceiling_ft: float,
) -> tuple[AdvisoryStatus, str, AdvisoryStatus, AdvisoryStatus, list[AirportClause]]:
    """Check departure and arrival airport conditions for IFR feasibility.

    IFR flights accept IFR conditions, but LIFR triggers amber and
    ceilings below configurable minimums trigger red.

    Returns (status, detail_fragment, dep_status, arr_status, clauses). The
    per-airport statuses colour the endpoint ribbon segments of the highlight
    (#375). ``clauses`` records the category at every airport with a condition,
    printed (LIFR) or not, for the aggregate's cross-model consensus (#823).

    Missing airport data is UNAVAILABLE, never a hardcoded clear GREEN (#391):
    the airport axis then simply doesn't contribute to the composite's ``worst``
    aggregate instead of vouching for airports it never saw.
    """
    if ctx.airport_conditions is None:
        return (
            AdvisoryStatus.UNAVAILABLE, "",
            AdvisoryStatus.UNAVAILABLE, AdvisoryStatus.UNAVAILABLE, [],
        )

    dep = ctx.airport_conditions.departure
    arr = ctx.airport_conditions.arrival
    dep_cond = dep.condition_for_model(model)
    arr_cond = arr.condition_for_model(model)

    loc = ctx.locale
    parts: list[str] = []
    per_airport: list[AdvisoryStatus] = []
    clauses: list[AirportClause] = []

    for label_key, icao, cond, min_ceil, end in [
        ("airport.dep", dep.icao, dep_cond, min_dep_ceiling_ft, "dep"),
        ("airport.arr", arr.icao, arr_cond, min_arr_ceiling_ft, "arr"),
    ]:
        if cond is None:
            # No condition for this model at this airport — unassessable, not clear.
            per_airport.append(AdvisoryStatus.UNAVAILABLE)
            continue
        status = AdvisoryStatus.GREEN
        suffix = ""
        # LIFR is concerning for IFR
        if cond.flight_category == FlightCategory.LIFR:
            # Check against minimum ceiling
            if cond.ceiling_ft is not None and cond.ceiling_ft < min_ceil:
                status = AdvisoryStatus.RED
                suffix = adv_t(
                    "ifr.lifr_below_min", loc,
                    ceiling=cond.ceiling_ft, min=int(min_ceil),
                )
            else:
                status = AdvisoryStatus.AMBER
        clause = AirportClause(
            end=end, category=cond.flight_category,
            printed=status != AdvisoryStatus.GREEN,
            head=f"{adv_t(label_key, loc)} {icao} ", suffix=suffix, locale=loc,
        )
        clauses.append(clause)
        if clause.printed:
            parts.append(clause.text)
        per_airport.append(status)

    detail = " | ".join(parts) if parts else ""
    worst = _worst_status(*per_airport)
    return worst, detail, per_airport[0], per_airport[1], clauses


def _check_enroute_hazards(
    ctx: RouteContext,
    model: str,
    conv_grade: ConvectiveModelGrade,
    icing_grade: IcingModelGrade,
) -> tuple[
    int, int, int,
    list[tuple[float, HighlightSeverity]],
    list[tuple[float, FlaggedCell | None]],
    RouteExtent,
]:
    """Merge the already-graded icing and convection into the composite's geometry.

    Neither axis is re-derived here. Convection comes from the shared
    ``grade_convective_model`` (meteorology-decisions §22) and icing from the
    ``aircraft_icing`` card's own per-model grade (§56), so this composite can
    never call icing RED while the icing card says "escape available", which is
    what it did before #827. This function only merges the two axes into the
    composite's ribbon and counts.

    Returns (icing_total, affected, icing_count, ribbon_points, icing_cells,
    affected_extent):

    - icing_total: points where the icing card could assess icing (#391).
    - affected: points with icing OR convective risk.
    - icing_count: points the icing card flagged.
    - ribbon_points: per-point worst of the two axes (#375); both cutout lists
      are index-aligned with ``ctx.analyses``.
    - affected_extent: the geometry of the icing-OR-convective union, so the
      published ``affected_nm`` measures the points ``affected`` counts.
    """
    icing_total = sum(icing_grade.assessed)
    affected = 0
    icing_count = 0
    ribbon_points: list[tuple[float, HighlightSeverity]] = []
    union_flags: list[bool] = []
    sounding_flags: list[bool] = []

    for idx, rpa in enumerate(ctx.analyses):
        dist = rpa.distance_from_origin_nm or 0.0
        conv_cell = conv_grade.region_cells[idx][1]
        icing_cell = icing_grade.region_cells[idx][1]
        sounding_flags.append(rpa.sounding.get(model) is not None)
        union_flags.append(conv_cell is not None or icing_cell is not None)
        if icing_cell is not None:
            icing_count += 1
        if union_flags[-1]:
            affected += 1
        icing_sev = (
            icing_cell.severity if icing_cell is not None
            else HighlightSeverity.GREEN if icing_grade.assessed[idx]
            else HighlightSeverity.UNAVAILABLE
        )
        conv_sev = conv_cell.severity if conv_cell is not None else HighlightSeverity.GREEN
        ribbon_points.append((dist, worst_severity(icing_sev, conv_sev)))

    dists = [rpa.distance_from_origin_nm or 0.0 for rpa in ctx.analyses]
    affected_extent = route_extent(
        dists, ctx.total_distance_nm, union_flags, sounding_flags,
        speed_kt=ctx.cruise_groundspeed_kt,
    )
    return (
        icing_total, affected, icing_count, ribbon_points,
        icing_grade.region_cells, affected_extent,
    )


@register
class IFRFeasibilityEvaluator:
    """Evaluates overall feasibility of IFR flight along the route.

    Combines airport conditions (LIFR triggers amber, below-minimums triggers
    red), en-route icing exposure, and convective activity into a single
    advisory. Designed for instrument-rated pilots assessing IFR mission
    viability.
    """

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="ifr_feasibility",
            name="IFR Feasibility",
            short_description="Overall IFR flight viability",
            description=(
                "Composite assessment of IFR flight feasibility. Checks airport "
                "conditions (LIFR triggers amber, below-minimums triggers red), "
                "en-route icing and convective activity. IFR or better at "
                "airports is GREEN. The icing axis is the Icing advisory's own "
                "grade for your aircraft, and the convective axis the "
                "Convective Activity advisory's: tune them there, this "
                "composite never grades either differently."
            ),
            category="flight_rules",
            timing_class="scan",
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="min_dep_ceiling_ft",
                    label="Min dep ceiling",
                    description="Minimum departure ceiling for IFR operations",
                    type="altitude",
                    unit="ft",
                    default=200,
                    min=0,
                    max=1000,
                    step=100,
                    audience="pilot",
                ),
                AdvisoryParameterDef(
                    key="min_arr_ceiling_ft",
                    label="Min arr ceiling",
                    description="Minimum arrival ceiling for IFR approach",
                    type="altitude",
                    unit="ft",
                    default=400,
                    min=100,
                    max=1500,
                    step=100,
                    audience="pilot",
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        min_dep_ceiling_ft = params.get("min_dep_ceiling_ft", 200)
        min_arr_ceiling_ft = params.get("min_arr_ceiling_ft", 400)
        params = {k: v for k, v in params.items() if k not in _RETIRED_ICING_PARAMS}
        # Both hazard axes are graded by their owning advisories' parameters,
        # not by a second set here (§22 convection, §56 icing). Resolved once
        # per evaluation: the user's overrides if that advisory is enabled,
        # catalog defaults otherwise.
        conv_params = resolve_convective_params(ctx)
        icing_params = resolve_aircraft_icing_params(ctx)
        # Character is read (not re-derived) for the EMBEDDED escalation below,
        # through the character advisory's own parameters, same §22 rule.
        char_params = resolve_character_params(ctx)

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            # 1. Airport conditions
            airport_status, airport_detail, dep_status, arr_status, airport_clauses = (
                _check_airport_ifr(
                    ctx, model, min_dep_ceiling_ft, min_arr_ceiling_ft
                )
            )

            # 2. Convection — the SAME call the convective advisory grades with,
            # so this composite's convective axis is that advisory's per-model
            # status by construction rather than by coincidence (§22).
            conv_grade = grade_convective_model(ctx, model, conv_params)
            total = conv_grade.total

            # 3. Icing: the SAME call the aircraft_icing card grades with, so
            # this composite's icing axis is that card's per-model status by
            # construction (§56), FIKI or not.
            icing_grade = grade_aircraft_icing_model(ctx, model, icing_params)
            (
                icing_total, affected, icing_count,
                ribbon_points, icing_cells, affected_extent,
            ) = _check_enroute_hazards(ctx, model, conv_grade, icing_grade)

            loc = ctx.locale
            if (
                total == 0
                and airport_status in (AdvisoryStatus.GREEN, AdvisoryStatus.UNAVAILABLE)
                and not airport_detail
            ):
                per_model.append(ModelAdvisoryResult.build(
                    model=model, status=AdvisoryStatus.UNAVAILABLE,
                    detail=adv_t("no_data", loc), affected=0, total=0,
                    total_distance_nm=ctx.total_distance_nm,
                    # Still counted in the airport consensus (#823).
                    airport_clauses=airport_clauses,
                ))
                continue

            # 4. Icing status, verbatim from the card (its detail already names
            # the column it graded with).
            icing_status = icing_grade.status
            icing_detail = (
                icing_grade.detail
                if icing_status in (AdvisoryStatus.AMBER, AdvisoryStatus.RED) else ""
            )

            # Coverage tolerance (#391): a no-icing verdict from icing-assessable
            # points at too small a share of the route is UNAVAILABLE, not clear
            # (contributes nothing to the composite's `worst` rather than a false
            # GREEN). Flagged icing verdicts always stand. The card applies the
            # same guard, so the two still agree.
            if icing_status == AdvisoryStatus.GREEN and below_coverage(icing_total, len(ctx.analyses)):
                icing_status = AdvisoryStatus.UNAVAILABLE

            # 5. Convective status — taken verbatim from the shared grade. No
            # thresholds, no floor and no altitude filter of its own: whatever
            # the Convective Activity advisory says for this model is what this
            # composite's convective axis says (§22).
            conv_status = conv_grade.status

            # EMBEDDED escalation (§22) — the ONE place this composite is
            # allowed to exceed the convective advisory, and only on information
            # that advisory does not measure. ISOLATED/SCATTERED say
            # "circumnavigable with see-and-avoid": a VFR statement that does not
            # transfer to IMC, so it must not move an IFR grade. WIDESPREAD /
            # ORGANIZED are realized-coverage bands, and the convective advisory
            # already reds on MODERATE+ coverage — escalating on those would
            # price the same measurement twice. EMBEDDED is orthogonal: it is
            # about the stratiform deck *hiding* the cells, and cells you cannot
            # see, in cloud, without radar, are strictly worse under IFR than
            # VFR. One step only, AMBER→RED; a GREEN convective axis has no cells
            # reaching cruise for a deck to hide, so it stays GREEN.
            embedded = (
                classify_route_character(ctx, model, char_params)
                is ConvectiveCharacter.EMBEDDED
            )
            escalated = embedded and conv_status == AdvisoryStatus.AMBER
            if escalated:
                conv_status = AdvisoryStatus.RED

            conv_detail = ""
            if conv_status not in (AdvisoryStatus.GREEN, AdvisoryStatus.UNAVAILABLE):
                # Mirror the convective card's headline shape (MODERATE+ extent +
                # named peak) so the two read as one statement rather than two
                # measurements of the same thing. When the escalation fired, name
                # it — otherwise the IFR card shows a red the convective card
                # does not, with nothing saying why.
                conv_detail = adv_t(
                    "ifr.conv_embedded" if escalated else "ifr.conv_over", loc,
                    risk=conv_grade.worst_risk.value.upper(),
                    # Same rule as the convective card: quote the extent of
                    # the tier the sentence names (#571 D1).
                    extent=format_extent(
                        conv_grade.extent_mod
                        if conv_grade.affected_mod
                        else conv_grade.extent
                    ),
                )

            # Coverage tolerance (#391): a no-convection verdict from soundings at
            # too small a share of the route is UNAVAILABLE, not clear — the same
            # guard the icing axis above already has (contributes nothing to
            # `worst` rather than a false GREEN). This ABSTAINS, it never grades
            # worse than the convective advisory, so §22's consistency invariant
            # still holds. Flagged convective verdicts stand.
            if conv_status == AdvisoryStatus.GREEN and below_coverage(total, len(ctx.analyses)):
                conv_status = AdvisoryStatus.UNAVAILABLE

            # 6. Combine all factors
            status = _worst_status(airport_status, icing_status, conv_status)

            detail_parts = []
            if airport_detail:
                detail_parts.append(airport_detail)
            if icing_detail:
                detail_parts.append(icing_detail)
            if conv_detail:
                detail_parts.append(conv_detail)

            if not detail_parts:
                detail = adv_t("ifr.acceptable", loc)
            else:
                detail = " | ".join(detail_parts)

            # Composite coverage tolerance (#391 review): mirror VFR — a GREEN IFR
            # grade while most en-route soundings are missing overstates
            # confidence even when a real-GREEN airport axis would carry it past
            # the per-axis icing/convective guards. Downgrade a would-be-GREEN
            # composite to UNAVAILABLE on thin en-route coverage; a flagged grade
            # always stands.
            if status == AdvisoryStatus.GREEN and below_coverage(total, len(ctx.analyses)):
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)

            # 6. Highlights (#375) only when the model has en-route data. The
            # airport IFR-viability axis colours the endpoint ribbon segments.
            icing_regions = build_regions(icing_cells, ctx.total_distance_nm)
            highlights = None
            if total > 0:
                apply_airport_endpoints(ribbon_points, dep_status, arr_status)
                ribbon = build_ribbon(ribbon_points, ctx.total_distance_nm)
                highlights = AdvisoryHighlights(
                    ribbon=ribbon,
                    regions=(
                        icing_regions
                        + build_regions(conv_grade.region_cells, ctx.total_distance_nm)
                    ),
                    peak_dist_nm=ribbon_peak(ribbon),
                )

            # primary_method_id (#409 review round 2): icing is the ONLY
            # method-bearing axis of this composite, so its method may be badged
            # only when the icing axis actually drove — or tied for — the grade.
            # driving_method_id over the *pooled* region list can't tell: icing
            # emits an AMBER region per in-buffer point even when icing_pct stays
            # below its own threshold (icing_status GREEN), so a pooled match
            # would borrow "ogimet_nwp" for a convective- or airport-driven grade.
            # Resolve the icing method over the icing-only regions (single-axis,
            # so driving_method_id is correct, incl. the extent-escalated RED),
            # then gate it on the icing axis reaching the composite status.
            icing_primary = driving_method_id(
                AdvisoryHighlights(regions=icing_regions), icing_status,
            )
            primary_method_id = icing_primary if icing_status == status else None

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
                extent=affected_extent,
                highlights=highlights,
                primary_method_id=primary_method_id,
                airport_clauses=airport_clauses,
            ))

        return RouteAdvisoryResult.from_per_model("ifr_feasibility", per_model, params)
