"""FIKI icing advisory — icing manageable for FIKI-equipped aircraft.

Evaluates three flight phases:
- Departure: total icing thickness to transit during climb (near origin)
- Cruise: proportion of route with clear air at cruise altitude
- Arrival: total icing thickness to transit during descent (near destination)
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    EvidenceSample,
    FlaggedCell,
    driving_method_id,
    hazardous_icing_zones,
    icing_zones_in_altitude_range,
    min_icing_clearance,
    EXTENT_MIN_NM,
    extent_min_nm_param,
    grade_extent,
    summarize_evidence,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    HighlightSeverity,
    IcingRisk,
    IcingZone,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)

_RISK_ORDER = [IcingRisk.NONE, IcingRisk.LIGHT, IcingRisk.MODERATE, IcingRisk.SEVERE]


def _worst_risk(a: IcingRisk, b: IcingRisk) -> IcingRisk:
    """Return the more severe of two icing risks."""
    return a if _RISK_ORDER.index(a) >= _RISK_ORDER.index(b) else b


def _transit_icing(
    zones: list[IcingZone],
    cruise_alt_ft: float,
) -> tuple[float, IcingRisk, bool]:
    """Total icing thickness between surface and cruise altitude at one point.

    Returns (thickness_ft, worst_severity, has_sld).
    """
    thickness = 0.0
    worst = IcingRisk.NONE
    sld = False
    for zone in zones:
        clipped_base = max(zone.base_ft, 0)
        clipped_top = min(zone.top_ft, cruise_alt_ft)
        if clipped_top > clipped_base:
            thickness += clipped_top - clipped_base
            worst = _worst_risk(worst, zone.risk)
            if zone.sld_risk:
                sld = True
    return thickness, worst, sld


_min_icing_clearance = min_icing_clearance  # local alias for backward compat


@register
class FIKIIcingEvaluator:
    """Evaluates icing severity for FIKI-equipped aircraft.

    FIKI aircraft can transit icing but not loiter indefinitely.
    Analyses departure climb, cruise clear-air proportion, and arrival descent.
    """

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="fiki_icing",
            name="FIKI Icing",
            short_description="Icing manageable for FIKI-equipped",
            description=(
                "For FIKI-equipped aircraft. Evaluates icing across three flight "
                "phases: climb-out near departure, clear-air cruise proportion, "
                "and descent near arrival. FIKI can transit icing layers but "
                "should minimise exposure — thick transit layers or extended "
                "cruise in icing are concerning."
            ),
            category="icing",
            timing_class="scan",
            default_enabled=False,  # opt-in for FIKI operators
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="proximity_nm",
                    label="Proximity radius",
                    description=(
                        "Distance from origin/destination to assess "
                        "climb/descent icing"
                    ),
                    type="number",
                    unit="nm",
                    default=50,
                    min=10,
                    max=100,
                    step=10,
                ),
                AdvisoryParameterDef(
                    key="cruise_icing_buffer_ft",
                    label="Cruise icing buffer",
                    description=(
                        "Minimum vertical clearance from cruise altitude to "
                        "nearest icing layer for clear-air cruise"
                    ),
                    type="altitude",
                    unit="ft",
                    default=2000,
                    min=500,
                    max=5000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="transit_thickness_amber_ft",
                    label="Transit thickness amber",
                    description="Total icing thickness during climb/descent for amber",
                    type="altitude",
                    unit="ft",
                    default=3000,
                    min=1000,
                    max=8000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="transit_thickness_red_ft",
                    label="Transit thickness red",
                    description="Total icing thickness during climb/descent for red",
                    type="altitude",
                    unit="ft",
                    default=5000,
                    min=2000,
                    max=10000,
                    step=500,
                ),
                # Flipped to AFFECTED polarity in #571 Stage 3. This was the
                # one gate in the system that read the other way — a percentage
                # of the *good* thing, compared with ``<`` — so a pilot moving
                # between advisory cards had to remember which direction each
                # slider meant. Same measurement, stated like every other.
                AdvisoryParameterDef(
                    key="extent_pct_amber",
                    label="% of cruise in icing (amber)",
                    description=(
                        "Route percentage where cruise is not in clear air, for amber"
                    ),
                    type="percent",
                    unit="%",
                    default=20,
                    min=0,
                    max=100,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="extent_pct_red",
                    label="% of cruise in icing (red)",
                    description=(
                        "Route percentage where cruise is not in clear air, for red"
                    ),
                    type="percent",
                    unit="%",
                    default=50,
                    min=0,
                    max=100,
                    step=5,
                ),
                extent_min_nm_param(),
                AdvisoryParameterDef(
                    key="severe_is_red",
                    label="Severe = RED",
                    description="Any severe icing in transit triggers RED",
                    type="boolean",
                    default=1,
                    min=0,
                    max=1,
                    step=1,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        proximity_nm = params.get("proximity_nm", 50)
        cruise_buffer_ft = params.get("cruise_icing_buffer_ft", 2000)
        transit_amber = params.get("transit_thickness_amber_ft", 3000)
        transit_red = params.get("transit_thickness_red_ft", 5000)
        extent_pct_amber = params.get("extent_pct_amber", 20)
        extent_pct_red = params.get("extent_pct_red", 50)
        extent_min_nm = params.get("extent_min_nm", EXTENT_MIN_NM)
        severe_is_red = params.get("severe_is_red", 1) > 0.5

        cruise_alt = ctx.cruise_altitude_ft
        total_dist = ctx.total_distance_nm

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            dep_max_thickness = 0.0
            dep_worst = IcingRisk.NONE
            dep_sld = False

            arr_max_thickness = 0.0
            arr_worst = IcingRisk.NONE
            arr_sld = False

            cruise_clear = 0
            cruise_total = 0
            total = 0
            # One evidence sample per route point (#393). The ribbon severity
            # (corridor transit thickness/severity/SLD + cruise clear-air) and
            # the grade's ``affected`` (cruise NOT clear-air) key on different
            # predicates, so each sample carries both — a corridor cutout can be
            # flagged while the cruise-clear grade is not.
            samples: list[EvidenceSample] = []

            for rpa in ctx.analyses:
                dist = rpa.distance_from_origin_nm or 0.0
                sounding = rpa.sounding.get(model)
                if sounding is None or not sounding.active_icing_available:
                    # No sounding, or the active icing method could not run here
                    # (Ogimet-NWP with no native cloud envelope) — absent icing,
                    # not clear. UNAVAILABLE; excluded from both denominators.
                    samples.append(EvidenceSample(
                        distance_nm=dist, assessed=False,
                        severity=HighlightSeverity.UNAVAILABLE,
                    ))
                    continue
                total += 1

                # Ogimet zones at risk NONE mean "assessed, no icing" — they must
                # not add to the transit thickness a FIKI grade is built from.
                zones = hazardous_icing_zones(sounding.icing_zones)
                t, sev, sld = _transit_icing(zones, cruise_alt)

                # --- departure / arrival transit icing ---
                in_corridor = False
                if dist <= proximity_nm:
                    in_corridor = True
                    dep_max_thickness = max(dep_max_thickness, t)
                    dep_worst = _worst_risk(dep_worst, sev)
                    dep_sld = dep_sld or sld

                if dist >= total_dist - proximity_nm:
                    in_corridor = True
                    arr_max_thickness = max(arr_max_thickness, t)
                    arr_worst = _worst_risk(arr_worst, sev)
                    arr_sld = arr_sld or sld

                # --- cruise clear-air check ---
                cruise_total += 1
                point_clear = (
                    not zones
                    or _min_icing_clearance(zones, cruise_alt) >= cruise_buffer_ft
                )
                if point_clear:
                    cruise_clear += 1

                # --- per-point ribbon verdict + icing-band cutout (#375) ---
                # Corridor points grade the transit column exactly as the route
                # grade does (SLD / SEVERE / thickness); everywhere the cruise
                # clear-air check contributes amber when icing sits within the
                # cruise buffer. Thin transit-able icing away from cruise stays
                # green.
                # ``reason_code`` is derived here, in the same branch as the
                # severity, so the two cannot drift. It matters for this advisory
                # more than for any other: a RED icing band means three quite
                # different things, and the colour tells them apart from nothing.
                #   ``sld``              — supercooled large droplets. Not a
                #     severity call: FIKI certification does not cover SLD, so the
                #     approval simply does not apply here.
                #   ``severe_icing``     — beyond the aircraft's capability.
                #   ``thick_transit``    — the layer is merely *thick*. A FIKI
                #     aircraft can transit it; the exposure is long.
                # And the AMBER split is this advisory's whole thesis (see the
                # module docstring — FIKI can transit icing but not loiter in it):
                #   ``icing_at_cruise``  — ice sits within the cruise buffer. You
                #     would be *loitering* in it, which is the thing FIKI cannot do.
                #   ``transit_exposure`` — thickness on the climb/descent only.
                # Precedence runs most-consequential first; a point can satisfy
                # several predicates and the code names the one that decides.
                reason: str | None = None
                if in_corridor and (
                    sld
                    or (severe_is_red and sev == IcingRisk.SEVERE)
                    or t >= transit_red
                ):
                    severity = HighlightSeverity.RED
                    if sld:
                        reason = "sld"
                    elif severe_is_red and sev == IcingRisk.SEVERE:
                        reason = "severe_icing"
                    else:
                        reason = "thick_transit"
                elif (in_corridor and t >= transit_amber) or not point_clear:
                    severity = HighlightSeverity.AMBER
                    reason = "icing_at_cruise" if not point_clear else "transit_exposure"
                else:
                    severity = HighlightSeverity.GREEN

                relevant_zones = icing_zones_in_altitude_range(
                    zones, 0, cruise_alt + cruise_buffer_ft
                )
                region = None
                if severity != HighlightSeverity.GREEN and relevant_zones:
                    region = FlaggedCell(
                        kind="icing_band",
                        severity=severity,
                        base_ft=int(min(z.base_ft for z in relevant_zones)),
                        top_ft=int(max(z.top_ft for z in relevant_zones)),
                        reason_code=reason,
                        metric_id="icing",
                        # The icing method behind these zones (#408).
                        method_id=sounding.icing_method_effective,
                    )
                # Grade ``affected`` = cruise NOT clear-air; ribbon ``severity``
                # includes corridor transit — deliberately decoupled (#393).
                samples.append(EvidenceSample(
                    distance_nm=dist, assessed=True, severity=severity,
                    affected=not point_clear, region=region,
                ))

            summary = summarize_evidence(
                samples, total_dist, speed_kt=ctx.cruise_groundspeed_kt,
            )

            # --- derive severity from the three metrics ---
            # The complement of the same extent the gate reads (#571): the
            # sentence and the colour can no longer quote different numbers.
            clear_pct = 100.0 - summary.extent.pct

            loc = ctx.locale
            if total == 0:
                per_model.append(
                    ModelAdvisoryResult.build(
                        model=model,
                        status=AdvisoryStatus.UNAVAILABLE,
                        detail=adv_t("no_data", loc),
                        affected=0,
                        total=0,
                        total_distance_nm=total_dist,
                    )
                )
                continue

            statuses: list[AdvisoryStatus] = []
            detail_parts: list[str] = []

            # SLD — always RED
            if dep_sld or arr_sld:
                statuses.append(AdvisoryStatus.RED)
                where = " & ".join(
                    [x for x in ["dep" if dep_sld else "", "arr" if arr_sld else ""] if x]
                )
                detail_parts.append(adv_t("fiki.sld_risk", loc, where=where))

            # Severe icing in transit
            if severe_is_red and (
                dep_worst == IcingRisk.SEVERE or arr_worst == IcingRisk.SEVERE
            ):
                statuses.append(AdvisoryStatus.RED)
                where = " & ".join(
                    [
                        x
                        for x in [
                            "dep" if dep_worst == IcingRisk.SEVERE else "",
                            "arr" if arr_worst == IcingRisk.SEVERE else "",
                        ]
                        if x
                    ]
                )
                detail_parts.append(adv_t("fiki.severe_icing", loc, where=where))

            # Transit thickness (worst of departure / arrival)
            worst_transit = max(dep_max_thickness, arr_max_thickness)
            if worst_transit >= transit_red:
                statuses.append(AdvisoryStatus.RED)
            elif worst_transit >= transit_amber:
                statuses.append(AdvisoryStatus.AMBER)
            else:
                statuses.append(AdvisoryStatus.GREEN)

            transit_parts = []
            if dep_max_thickness > 0:
                transit_parts.append(adv_t("fiki.dep_transit", loc, thickness=f"{dep_max_thickness:.0f}"))
            if arr_max_thickness > 0:
                transit_parts.append(adv_t("fiki.arr_transit", loc, thickness=f"{arr_max_thickness:.0f}"))
            if transit_parts:
                detail_parts.append(adv_t("fiki.transit", loc, parts=", ".join(transit_parts)))

            # Clear cruise — graded through the shared gate on the same
            # affected-cruise extent every other advisory uses (#571 Stage 3),
            # so this axis gets the minimum-extent floor and the distance-based
            # percentage with it. The pilot-facing sentence still reports the
            # CLEAR share, which is the useful number for a FIKI aircraft.
            statuses.append(grade_extent(
                summary.extent,
                amber_pct=extent_pct_amber,
                red_pct=extent_pct_red,
                min_nm=extent_min_nm,
            ))
            detail_parts.append(adv_t("fiki.cruise_clear", loc, pct=f"{clear_pct:.0f}"))

            status = AdvisoryStatus.worst(statuses)
            if (
                status == AdvisoryStatus.GREEN
                and worst_transit == 0
                and clear_pct >= 100
            ):
                detail = adv_t("fiki.no_icing", loc)
            else:
                detail = " | ".join(detail_parts)

            # Coverage tolerance (#391): a clear FIKI verdict from assessable
            # points at too small a share of the route cannot vouch for the rest
            # (safety-sensitive icing evaluator). Flagged verdicts always stand.
            if status == AdvisoryStatus.GREEN and summary.below_coverage:
                per_model.append(ModelAdvisoryResult.build(
                    model=model, status=AdvisoryStatus.UNAVAILABLE,
                    detail=adv_t("no_data", loc), affected=0, total=total,
                    total_distance_nm=total_dist,
                ))
                continue

            affected = cruise_total - cruise_clear
            per_model.append(
                ModelAdvisoryResult.build(
                    model=model,
                    status=status,
                    detail=detail,
                    affected=affected,
                    total=cruise_total,
                    total_distance_nm=total_dist,
                    extent=summary.extent,
                    highlights=summary.highlights,  # model has data here (total > 0)
                    primary_method_id=driving_method_id(summary.highlights, status),
                )
            )

        return RouteAdvisoryResult.from_per_model("fiki_icing", per_model, params)
