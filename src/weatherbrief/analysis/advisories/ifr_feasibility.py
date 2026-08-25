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
    grade_extent,
    route_extent,
    hazardous_icing_zones,
    min_icing_clearance,
    ribbon_peak,
    worst_severity,
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
    ConvectiveCharacter,
    HighlightSeverity,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)
from weatherbrief.models.airport_conditions import FlightCategory

# Default icing-exposure thresholds (route % with icing near cruise altitude).
# Single source of truth shared by catalog_entry() parameter defaults and the
# evaluate() fallbacks so the two cannot drift. They previously disagreed —
# catalog advertised 20/50 while evaluate() fell back to 15/30 — meaning a call
# without explicit params graded icing more aggressively than the UI showed.
_ICING_PCT_AMBER_DEFAULT = 20
_ICING_PCT_RED_DEFAULT = 50


def _worst_status(*statuses: AdvisoryStatus) -> AdvisoryStatus:
    """Return the most severe status from the given values."""
    return AdvisoryStatus.worst(list(statuses))


def _check_airport_ifr(
    ctx: RouteContext,
    model: str,
    min_dep_ceiling_ft: float,
    min_arr_ceiling_ft: float,
) -> tuple[AdvisoryStatus, str, AdvisoryStatus, AdvisoryStatus]:
    """Check departure and arrival airport conditions for IFR feasibility.

    IFR flights accept IFR conditions, but LIFR triggers amber and
    ceilings below configurable minimums trigger red.

    Returns (status, detail_fragment, dep_status, arr_status). The per-airport
    statuses colour the endpoint ribbon segments of the highlight (#375).

    Missing airport data is UNAVAILABLE, never a hardcoded clear GREEN (#391):
    the airport axis then simply doesn't contribute to the composite's ``worst``
    aggregate instead of vouching for airports it never saw.
    """
    if ctx.airport_conditions is None:
        return (
            AdvisoryStatus.UNAVAILABLE, "",
            AdvisoryStatus.UNAVAILABLE, AdvisoryStatus.UNAVAILABLE,
        )

    dep = ctx.airport_conditions.departure
    arr = ctx.airport_conditions.arrival
    dep_cond = dep.condition_for_model(model)
    arr_cond = arr.condition_for_model(model)

    loc = ctx.locale
    parts: list[str] = []
    per_airport: list[AdvisoryStatus] = []

    for label_key, icao, cond, min_ceil in [
        ("airport.dep", dep.icao, dep_cond, min_dep_ceiling_ft),
        ("airport.arr", arr.icao, arr_cond, min_arr_ceiling_ft),
    ]:
        if cond is None:
            # No condition for this model at this airport — unassessable, not clear.
            per_airport.append(AdvisoryStatus.UNAVAILABLE)
            continue
        status = AdvisoryStatus.GREEN
        # LIFR is concerning for IFR
        if cond.flight_category == FlightCategory.LIFR:
            label = adv_t(label_key, loc)
            # Check against minimum ceiling
            if cond.ceiling_ft is not None and cond.ceiling_ft < min_ceil:
                status = AdvisoryStatus.RED
                parts.append(adv_t(
                    "ifr.lifr_below_min", loc,
                    label=label, icao=icao,
                    ceiling=cond.ceiling_ft, min=int(min_ceil),
                ))
            else:
                status = AdvisoryStatus.AMBER
                parts.append(adv_t("ifr.lifr", loc, label=label, icao=icao))
        per_airport.append(status)

    detail = " | ".join(parts) if parts else ""
    worst = _worst_status(*per_airport)
    return worst, detail, per_airport[0], per_airport[1]


def _check_enroute_hazards(
    ctx: RouteContext,
    model: str,
    conv_grade: ConvectiveModelGrade,
    cruise_altitude_ft: float,
    icing_altitude_buffer_ft: float,
) -> tuple[
    int, int, int,
    list[tuple[float, HighlightSeverity]],
    list[tuple[float, FlaggedCell | None]],
    RouteExtent,
    RouteExtent,
]:
    """Check en-route icing, and merge it with the already-graded convection.

    Icing is assessed here (it is this composite's own axis, with its own
    altitude buffer). Convection is **not** re-derived: ``conv_grade`` comes from
    the shared ``grade_convective_model`` — the identical call the ``convective``
    advisory grades with — so the two advisories cannot disagree about the same
    sounding (meteorology-decisions §22). This function only merges the two axes
    into the composite's ribbon and counts.

    Uses the same clearance-based icing check as the FIKI advisory: a point
    counts as "icing affected" only when an icing zone is within
    *icing_altitude_buffer_ft* of cruise altitude.

    Returns (icing_total, affected, icing_count, ribbon_points, icing_cells,
    icing_extent, affected_extent).
    - icing_total: points where the active icing method could run (the icing
      denominator) — a point on Ogimet-NWP with no native cloud envelope cannot
      assess icing and is excluded so absent icing is not graded clear (#391)
    - affected: number of unique points with icing OR convective risk
    - icing_count: icing-affected points, for the detail message. There is no
      convective counterpart: ``conv_grade.affected`` is that count by
      construction — ``grade_convective_model`` appends a ``FlaggedCell`` for
      exactly the points it increments ``affected`` on, in the same block after
      every filter — so re-counting the cells here would be a second walk over
      a number the grade already carries.
    - icing_extent: the geometry-accurate extent of the icing-affected points
      (#571). The icing sentence prints this rather than the route length scaled
      by ``icing_count / icing_total``, which described neither the points nor
      the miles it claimed.
    - affected_extent: the same geometry over the icing-OR-convective union, so
      the published ``affected_nm`` measures the points ``affected`` counts.
    - ribbon_points: per-point worst of the two axes (#375). The convective
      cutouts come straight off ``conv_grade.region_cells``, which is
      index-aligned with ``ctx.analyses``, so the geometry the composite draws is
      literally the geometry the convective advisory draws.
    """
    icing_total = 0
    affected = 0
    icing_count = 0
    ribbon_points: list[tuple[float, HighlightSeverity]] = []
    icing_cells: list[tuple[float, FlaggedCell | None]] = []
    icing_flags: list[bool] = []
    union_flags: list[bool] = []
    # Denominators (#391): the icing axis speaks only for points where the
    # active icing method could run; the composite's union axis for points with
    # a sounding.
    icing_assessed: list[bool] = []
    sounding_flags: list[bool] = []

    for idx, rpa in enumerate(ctx.analyses):
        dist = rpa.distance_from_origin_nm or 0.0
        icing_flags.append(False)
        union_flags.append(False)
        icing_assessed.append(False)
        sounding_flags.append(False)
        # Index-aligned with the grade (both walk ctx.analyses in order and
        # always append exactly one entry per point).
        conv_cell = conv_grade.region_cells[idx][1]
        conv_sev = (
            conv_cell.severity if conv_cell is not None else HighlightSeverity.GREEN
        )
        has_convective = conv_cell is not None

        sounding = rpa.sounding.get(model)
        if sounding is None:
            ribbon_points.append((dist, HighlightSeverity.UNAVAILABLE))
            icing_cells.append((dist, None))
            continue

        # The active icing method may be unable to run here (Ogimet-NWP with no
        # native cloud envelope): then its empty icing_zones is absent data, not
        # "no icing". Assess icing only when it could run, and keep a separate
        # denominator so absent icing never dilutes toward a clear grade (#391).
        sounding_flags[-1] = True
        icing_available = sounding.active_icing_available
        if icing_available:
            icing_total += 1
            icing_assessed[-1] = True
        # Only zones the pilot would actually meet: an Ogimet zone at risk NONE
        # is the method reporting "assessed, no icing", and counting it made this
        # composite flag — and go RED on — icing that does not exist.
        icing_zones = hazardous_icing_zones(sounding.icing_zones)
        has_icing = (
            icing_available
            and bool(icing_zones)
            and min_icing_clearance(icing_zones, cruise_altitude_ft)
            < icing_altitude_buffer_ft
        )

        if has_icing:
            icing_count += 1
            icing_flags[-1] = True
            # The triggering zones: those within the buffer of cruise.
            band = [
                z for z in icing_zones
                if z.base_ft < cruise_altitude_ft + icing_altitude_buffer_ft
                and z.top_ft > cruise_altitude_ft - icing_altitude_buffer_ft
            ]
            icing_cells.append((dist, FlaggedCell(
                kind="icing_band",
                severity=HighlightSeverity.AMBER,
                base_ft=int(min(z.base_ft for z in band)) if band else None,
                top_ft=int(max(z.top_ft for z in band)) if band else None,
                metric_id="icing",
                # Icing is the axis this composite *badges* (see
                # primary_method_id below): it is the only one gated against the
                # grade. The convective cells carry their own method too — a
                # region's provenance is about that region, not about which axis
                # won.
                method_id=sounding.icing_method_effective,
            )))
        else:
            icing_cells.append((dist, None))

        if has_icing or has_convective:
            affected += 1
            union_flags[-1] = True

        if not icing_available:
            icing_sev = HighlightSeverity.UNAVAILABLE
        elif has_icing:
            icing_sev = HighlightSeverity.AMBER
        else:
            icing_sev = HighlightSeverity.GREEN
        ribbon_points.append((dist, worst_severity(icing_sev, conv_sev)))

    dists = [rpa.distance_from_origin_nm or 0.0 for rpa in ctx.analyses]
    icing_extent = route_extent(
        dists, ctx.total_distance_nm, icing_flags, icing_assessed,
    )
    affected_extent = route_extent(
        dists, ctx.total_distance_nm, union_flags, sounding_flags,
    )
    return (
        icing_total, affected, icing_count, ribbon_points, icing_cells,
        icing_extent, affected_extent,
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
                "en-route icing exposure percentage, and convective activity. "
                "IFR or better at airports is GREEN. Icing exceeding the "
                "threshold percentage triggers RED. The convective axis is the "
                "Convective Activity advisory's own grade — tune it there; this "
                "composite never grades convection differently."
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
                AdvisoryParameterDef(
                    key="icing_pct_amber",
                    label="Icing % (amber)",
                    description="Route percentage with icing near cruise for amber",
                    type="percent",
                    unit="%",
                    default=_ICING_PCT_AMBER_DEFAULT,
                    min=5,
                    max=50,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="icing_pct_red",
                    label="Icing % (red)",
                    description="Route percentage with icing near cruise for red",
                    type="percent",
                    unit="%",
                    default=_ICING_PCT_RED_DEFAULT,
                    min=10,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="icing_altitude_buffer_ft",
                    label="Icing alt buffer",
                    description=(
                        "Icing above cruise + buffer is ignored "
                        "(irrelevant altitude)"
                    ),
                    type="altitude",
                    unit="ft",
                    default=2000,
                    min=500,
                    max=5000,
                    step=500,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        min_dep_ceiling_ft = params.get("min_dep_ceiling_ft", 200)
        min_arr_ceiling_ft = params.get("min_arr_ceiling_ft", 400)
        icing_pct_amber = params.get("icing_pct_amber", _ICING_PCT_AMBER_DEFAULT)
        icing_pct_red = params.get("icing_pct_red", _ICING_PCT_RED_DEFAULT)
        icing_altitude_buffer_ft = params.get("icing_altitude_buffer_ft", 2000)
        # The convective axis is graded by the Convective Activity advisory's
        # own parameters, not by a second set here (§22). Resolved once per
        # evaluation: the user's overrides for ``convective`` if it is enabled,
        # catalog defaults otherwise.
        conv_params = resolve_convective_params(ctx)
        # Character is read (not re-derived) for the EMBEDDED escalation below,
        # through the character advisory's own parameters — same §22 rule.
        char_params = resolve_character_params(ctx)

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            # 1. Airport conditions
            airport_status, airport_detail, dep_status, arr_status = (
                _check_airport_ifr(
                    ctx, model, min_dep_ceiling_ft, min_arr_ceiling_ft
                )
            )

            # 2. Convection — the SAME call the convective advisory grades with,
            # so this composite's convective axis is that advisory's per-model
            # status by construction rather than by coincidence (§22).
            conv_grade = grade_convective_model(ctx, model, conv_params)
            total = conv_grade.total

            # 3. En-route icing, merged with the graded convection into the
            # composite's per-point highlight geometry (#375).
            (
                icing_total, affected, icing_count,
                ribbon_points, icing_cells, icing_extent, affected_extent,
            ) = _check_enroute_hazards(
                ctx, model, conv_grade,
                ctx.cruise_altitude_ft, icing_altitude_buffer_ft,
            )

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
                ))
                continue

            # 4. Determine icing status
            icing_status = AdvisoryStatus.GREEN
            icing_detail = ""
            if total > 0 and icing_total == 0:
                # Points exist but the active icing method could run at none of
                # them → the icing axis is unassessable, not clear (#391). It
                # contributes UNAVAILABLE (ignored by `worst`) instead of GREEN.
                icing_status = AdvisoryStatus.UNAVAILABLE
            elif icing_total > 0 and icing_count > 0:
                icing_status = grade_extent(
                    icing_extent,
                    amber_pct=icing_pct_amber, red_pct=icing_pct_red,
                )
                if icing_status != AdvisoryStatus.GREEN:
                    icing_detail = adv_t(
                        "ifr.icing_over", loc,
                        extent=format_extent(icing_extent),
                    )

            # Coverage tolerance (#391): a no-icing verdict from icing-assessable
            # points at too small a share of the route is UNAVAILABLE, not clear
            # (contributes nothing to the composite's `worst` rather than a false
            # GREEN). Flagged icing verdicts always stand.
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
                affected_nm=affected_extent.nm,
                highlights=highlights,
                primary_method_id=primary_method_id,
            ))

        return RouteAdvisoryResult.from_per_model("ifr_feasibility", per_model, params)
