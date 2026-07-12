"""FIKI icing advisory — icing manageable for FIKI-equipped aircraft.

Evaluates three flight phases:
- Departure: total icing thickness to transit during climb (near origin)
- Cruise: proportion of route with clear air at cruise altitude
- Arrival: total icing thickness to transit during descent (near destination)
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    FlaggedCell,
    build_regions,
    build_ribbon,
    icing_zones_in_altitude_range,
    min_icing_clearance,
    ribbon_peak,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryHighlights,
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
                AdvisoryParameterDef(
                    key="clear_cruise_amber_pct",
                    label="Clear cruise amber",
                    description="Below this clear-air percentage triggers amber",
                    type="percent",
                    unit="%",
                    default=80,
                    min=0,
                    max=100,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="clear_cruise_red_pct",
                    label="Clear cruise red",
                    description="Below this clear-air percentage triggers red",
                    type="percent",
                    unit="%",
                    default=50,
                    min=0,
                    max=100,
                    step=5,
                ),
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
        clear_amber = params.get("clear_cruise_amber_pct", 80)
        clear_red = params.get("clear_cruise_red_pct", 50)
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
            # Per-point highlight geometry (#375): the ribbon mirrors the loop's
            # own per-point classification — transit thickness/severity/SLD in
            # the departure/arrival corridors, cruise clear-air everywhere.
            ribbon_points: list[tuple[float, HighlightSeverity]] = []
            region_cells: list[tuple[float, FlaggedCell | None]] = []

            for rpa in ctx.analyses:
                dist = rpa.distance_from_origin_nm or 0.0
                sounding = rpa.sounding.get(model)
                if sounding is None or not sounding.active_icing_available:
                    # No sounding, or the active icing method could not run here
                    # (Ogimet-NWP with no native cloud envelope) — absent icing,
                    # not clear. UNAVAILABLE; excluded from both denominators.
                    ribbon_points.append((dist, HighlightSeverity.UNAVAILABLE))
                    region_cells.append((dist, None))
                    continue
                total += 1

                zones = sounding.icing_zones
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
                if in_corridor and (
                    sld
                    or (severe_is_red and sev == IcingRisk.SEVERE)
                    or t >= transit_red
                ):
                    severity = HighlightSeverity.RED
                elif (in_corridor and t >= transit_amber) or not point_clear:
                    severity = HighlightSeverity.AMBER
                else:
                    severity = HighlightSeverity.GREEN

                relevant_zones = icing_zones_in_altitude_range(
                    zones, 0, cruise_alt + cruise_buffer_ft
                )
                if severity != HighlightSeverity.GREEN and relevant_zones:
                    region_cells.append((dist, FlaggedCell(
                        kind="icing_band",
                        severity=severity,
                        base_ft=int(min(z.base_ft for z in relevant_zones)),
                        top_ft=int(max(z.top_ft for z in relevant_zones)),
                    )))
                else:
                    region_cells.append((dist, None))
                ribbon_points.append((dist, severity))

            # --- derive severity from the three metrics ---
            clear_pct = (
                100.0 * cruise_clear / cruise_total if cruise_total > 0 else 100.0
            )

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

            # Clear cruise
            if clear_pct < clear_red:
                statuses.append(AdvisoryStatus.RED)
            elif clear_pct < clear_amber:
                statuses.append(AdvisoryStatus.AMBER)
            else:
                statuses.append(AdvisoryStatus.GREEN)
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

            # Highlights (#375) — the model has data here (total > 0).
            ribbon = build_ribbon(ribbon_points, total_dist)
            highlights = AdvisoryHighlights(
                ribbon=ribbon,
                regions=build_regions(region_cells, total_dist),
                peak_dist_nm=ribbon_peak(ribbon),
            )

            affected = cruise_total - cruise_clear
            per_model.append(
                ModelAdvisoryResult.build(
                    model=model,
                    status=status,
                    detail=detail,
                    affected=affected,
                    total=cruise_total,
                    total_distance_nm=total_dist,
                    highlights=highlights,
                )
            )

        return RouteAdvisoryResult.from_per_model("fiki_icing", per_model, params)
