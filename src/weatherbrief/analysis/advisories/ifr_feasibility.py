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
    format_extent,
    hazardous_icing_zones,
    min_icing_clearance,
    ribbon_peak,
    worst_severity,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryHighlights,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ConvectiveRisk,
    HighlightSeverity,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)
from weatherbrief.models.airport_conditions import FlightCategory

# Convective risk levels ordered by severity
_CONVECTIVE_SEVERITY = [
    ConvectiveRisk.NONE,
    ConvectiveRisk.MARGINAL,
    ConvectiveRisk.LOW,
    ConvectiveRisk.MODERATE,
    ConvectiveRisk.HIGH,
    ConvectiveRisk.EXTREME,
]
_CONVECTIVE_SEVERITY_INDEX = {r: i for i, r in enumerate(_CONVECTIVE_SEVERITY)}

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
    convective_min_risk_idx: int,
    cruise_altitude_ft: float,
    icing_altitude_buffer_ft: float,
) -> tuple[
    int, int, int, int, int, ConvectiveRisk,
    list[tuple[float, HighlightSeverity]],
    list[tuple[float, FlaggedCell | None]],
    list[tuple[float, FlaggedCell | None]],
]:
    """Check en-route icing and convective hazards in a single pass.

    Uses the same clearance-based icing check as the FIKI advisory:
    a point counts as "icing affected" only when an icing zone is within
    *icing_altitude_buffer_ft* of cruise altitude.

    Returns (total, icing_total, affected, icing_count, conv_count,
    worst_conv_risk, ribbon_points, icing_cells, conv_cells).
    - total: points with a sounding (convective denominator)
    - icing_total: points where the active icing method could run (icing
      denominator) — a point on Ogimet-NWP with no native cloud envelope cannot
      assess icing and is excluded so absent icing is not graded clear (#391)
    - affected: number of unique points with icing OR convective risk
    - icing_count / conv_count: individual counts for detail messages
    - ribbon_points / icing_cells / conv_cells: per-point highlight geometry
      (#375) — the ribbon is the worst of the two axes at each x (icing amber,
      convective amber or red for HIGH/EXTREME); the two cell lists carry the
      ``icing_band`` and ``tower``/``tower_unresolved`` cutouts (multi-kind, so
      one list each).
    """
    total = 0
    icing_total = 0
    affected = 0
    icing_count = 0
    conv_count = 0
    worst_conv_risk = ConvectiveRisk.NONE
    ribbon_points: list[tuple[float, HighlightSeverity]] = []
    icing_cells: list[tuple[float, FlaggedCell | None]] = []
    conv_cells: list[tuple[float, FlaggedCell | None]] = []

    for rpa in ctx.analyses:
        dist = rpa.distance_from_origin_nm or 0.0
        sounding = rpa.sounding.get(model)
        if sounding is None:
            ribbon_points.append((dist, HighlightSeverity.UNAVAILABLE))
            icing_cells.append((dist, None))
            conv_cells.append((dist, None))
            continue
        total += 1

        # The active icing method may be unable to run here (Ogimet-NWP with no
        # native cloud envelope): then its empty icing_zones is absent data, not
        # "no icing". Assess icing only when it could run, and keep a separate
        # denominator so absent icing never dilutes toward a clear grade (#391).
        icing_available = sounding.active_icing_available
        if icing_available:
            icing_total += 1
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
        has_convective = False
        conv_sev = HighlightSeverity.GREEN

        conv = sounding.convective
        if conv is not None:
            risk_idx = _CONVECTIVE_SEVERITY_INDEX.get(conv.risk_level, 0)
            if risk_idx >= convective_min_risk_idx:
                has_convective = True
                if risk_idx > _CONVECTIVE_SEVERITY_INDEX.get(worst_conv_risk, 0):
                    worst_conv_risk = conv.risk_level
                conv_sev = (
                    HighlightSeverity.RED
                    if conv.risk_level in (ConvectiveRisk.HIGH, ConvectiveRisk.EXTREME)
                    else HighlightSeverity.AMBER
                )

        if has_icing:
            icing_count += 1
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
                # Icing is the axis this composite *badges* (see primary_method_id
                # below): it is the only one gated against the grade. The
                # convective cells carry their own method too — a region's
                # provenance is about that region, not about which axis won.
                method_id=sounding.icing_method_effective,
            )))
        else:
            icing_cells.append((dist, None))

        if has_convective:
            conv_count += 1
            # Same geometry conventions as the convective emitter (#373): a
            # bounded tower only when BOTH base and top resolve, else a
            # full-column ghost.
            if conv.base_ft is not None and conv.top_ft is not None:
                conv_cells.append((dist, FlaggedCell(
                    kind="tower",
                    severity=conv_sev,
                    base_ft=int(conv.base_ft),
                    top_ft=int(conv.top_ft),
                    metric_id="convective_risk",
                    method_id=sounding.convective_method_effective,
                )))
            else:
                conv_cells.append((dist, FlaggedCell(
                    kind="tower_unresolved",
                    severity=conv_sev,
                    base_ft=None,
                    top_ft=None,
                    metric_id="convective_risk",
                    method_id=sounding.convective_method_effective,
                )))
        else:
            conv_cells.append((dist, None))

        if has_icing or has_convective:
            affected += 1

        if not icing_available:
            icing_sev = HighlightSeverity.UNAVAILABLE
        elif has_icing:
            icing_sev = HighlightSeverity.AMBER
        else:
            icing_sev = HighlightSeverity.GREEN
        ribbon_points.append((dist, worst_severity(icing_sev, conv_sev)))

    return (
        total, icing_total, affected, icing_count, conv_count, worst_conv_risk,
        ribbon_points, icing_cells, conv_cells,
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
                "threshold percentage or significant convective activity "
                "triggers RED."
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
                AdvisoryParameterDef(
                    key="convective_min_risk",
                    label="Convective min",
                    description=(
                        "Minimum convective risk to flag "
                        "(2=LOW, 3=MODERATE, 4=HIGH)"
                    ),
                    type="number",
                    default=3,
                    min=1,
                    max=5,
                    step=1,
                ),
                AdvisoryParameterDef(
                    key="convective_pct_red",
                    label="Convective % (red)",
                    description="Route percentage with convective activity for red",
                    type="percent",
                    unit="%",
                    default=10,
                    min=5,
                    max=50,
                    step=5,
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
        convective_min_risk = int(params.get("convective_min_risk", 3))
        convective_pct_red = params.get("convective_pct_red", 10)

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            # 1. Airport conditions
            airport_status, airport_detail, dep_status, arr_status = (
                _check_airport_ifr(
                    ctx, model, min_dep_ceiling_ft, min_arr_ceiling_ft
                )
            )

            # 2. En-route hazards (single pass — no double-counting), including
            # the per-point highlight geometry (#375).
            (
                total, icing_total, affected, icing_count, conv_count, worst_conv_risk,
                ribbon_points, icing_cells, conv_cells,
            ) = _check_enroute_hazards(
                ctx, model, convective_min_risk,
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

            # 3. Determine icing status
            icing_status = AdvisoryStatus.GREEN
            icing_detail = ""
            if total > 0 and icing_total == 0:
                # Points exist but the active icing method could run at none of
                # them → the icing axis is unassessable, not clear (#391). It
                # contributes UNAVAILABLE (ignored by `worst`) instead of GREEN.
                icing_status = AdvisoryStatus.UNAVAILABLE
            elif icing_total > 0 and icing_count > 0:
                icing_pct = 100.0 * icing_count / icing_total
                if icing_pct >= icing_pct_red:
                    icing_status = AdvisoryStatus.RED
                elif icing_pct >= icing_pct_amber:
                    icing_status = AdvisoryStatus.AMBER
                if icing_status != AdvisoryStatus.GREEN:
                    icing_detail = adv_t(
                        "ifr.icing_over", loc,
                        extent=format_extent(icing_count, icing_total, ctx.total_distance_nm),
                    )

            # Coverage tolerance (#391): a no-icing verdict from icing-assessable
            # points at too small a share of the route is UNAVAILABLE, not clear
            # (contributes nothing to the composite's `worst` rather than a false
            # GREEN). Flagged icing verdicts always stand.
            if icing_status == AdvisoryStatus.GREEN and below_coverage(icing_total, len(ctx.analyses)):
                icing_status = AdvisoryStatus.UNAVAILABLE

            # 4. Determine convective status
            conv_status = AdvisoryStatus.GREEN
            conv_detail = ""
            if total > 0 and conv_count > 0:
                conv_pct = 100.0 * conv_count / total
                # HIGH/EXTREME at any point is always red
                if worst_conv_risk in (ConvectiveRisk.HIGH, ConvectiveRisk.EXTREME):
                    conv_status = AdvisoryStatus.RED
                elif conv_pct >= convective_pct_red:
                    conv_status = AdvisoryStatus.RED
                else:
                    conv_status = AdvisoryStatus.AMBER
                conv_detail = adv_t(
                    "ifr.conv_over", loc,
                    risk=worst_conv_risk.value.upper(),
                    extent=format_extent(conv_count, total, ctx.total_distance_nm),
                )

            # Coverage tolerance (#391): a no-convection verdict from soundings at
            # too small a share of the route is UNAVAILABLE, not clear — the same
            # guard the icing axis above already has (contributes nothing to
            # `worst` rather than a false GREEN). Flagged convective verdicts stand.
            if conv_status == AdvisoryStatus.GREEN and below_coverage(total, len(ctx.analyses)):
                conv_status = AdvisoryStatus.UNAVAILABLE

            # 5. Combine all factors
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
                        + build_regions(conv_cells, ctx.total_distance_nm)
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
                highlights=highlights,
                primary_method_id=primary_method_id,
            ))

        return RouteAdvisoryResult.from_per_model("ifr_feasibility", per_model, params)
