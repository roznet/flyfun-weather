"""Turbulence advisory — ride quality acceptable at cruise."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    FlaggedCell,
    below_coverage,
    build_regions,
    build_ribbon,
    format_extent,
    pct_above_threshold,
    ribbon_peak,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryHighlights,
    AdvisoryParameterDef,
    AdvisoryStatus,
    CATRiskLevel,
    HighlightSeverity,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)


@register
class TurbulenceEvaluator:
    """Evaluates ride quality based on CAT risk and vertical motion at cruise."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="turbulence",
            name="Turbulence",
            short_description="Ride quality acceptable at cruise",
            description=(
                "Checks CAT risk layers and vertical motion at cruise altitude. "
                "Any severe CAT triggers RED regardless of route percentage."
            ),
            category="turbulence",
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="route_pct_amber",
                    label="Route % (amber)",
                    description="Route percentage with turbulence for amber",
                    type="percent",
                    unit="%",
                    default=20,
                    min=5,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="strong_w_fpm",
                    label="Strong w threshold",
                    description="Vertical velocity above this is significant",
                    type="speed",
                    unit="ft/min",
                    default=200,
                    min=100,
                    max=500,
                    step=50,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        route_pct_amber = params.get("route_pct_amber", 20)
        strong_w_fpm = params.get("strong_w_fpm", 200)
        cruise = ctx.cruise_altitude_ft

        _CAT_ORDER = [CATRiskLevel.NONE, CATRiskLevel.LIGHT, CATRiskLevel.MODERATE, CATRiskLevel.SEVERE]
        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            total = 0
            affected = 0
            has_severe = False
            worst_cat = CATRiskLevel.NONE
            # Per-point highlight geometry (#375). Ribbon: SEVERE CAT anywhere in
            # the column → red (the cutout showing where it sits is exactly the
            # ambiguity the highlight resolves); MODERATE CAT overlapping the
            # cruise band or a strong updraft near cruise → amber; else green.
            ribbon_points: list[tuple[float, HighlightSeverity]] = []
            region_cells: list[tuple[float, FlaggedCell | None]] = []

            for rpa in ctx.analyses:
                dist = rpa.distance_from_origin_nm or 0.0
                sounding = rpa.sounding.get(model)
                vm = sounding.vertical_motion if sounding is not None else None
                # "Assessed" for turbulence means we have a vertical-motion
                # assessment: CAT comes from ``vm.cat_risk_layers`` (Richardson-
                # derived, independent of omega) and strong-updraft from
                # ``vm.max_w_fpm`` (omega) — both live on ``vm``. A sounding with
                # no ``vm`` (lite analysis / old pack) can assess neither, so it
                # is UNAVAILABLE, not a smooth-ride GREEN. We deliberately do NOT
                # gate on omega: an omega-less model still has a complete CAT
                # assessment and must grade normally (#391 — the #389 mistake).
                if sounding is None or vm is None:
                    ribbon_points.append((dist, HighlightSeverity.UNAVAILABLE))
                    region_cells.append((dist, None))
                    continue
                total += 1

                point_affected = False
                strong_w_here = False
                severe_layers: list = []
                moderate_cruise_layers: list = []

                for layer in vm.cat_risk_layers:
                    at_cruise = layer.base_ft <= cruise <= layer.top_ft
                    if at_cruise and layer.risk != CATRiskLevel.NONE:
                        point_affected = True
                        if _CAT_ORDER.index(layer.risk) > _CAT_ORDER.index(worst_cat):
                            worst_cat = layer.risk
                        if layer.risk == CATRiskLevel.SEVERE:
                            has_severe = True
                    # Highlight geometry: severe counts anywhere in the
                    # column, moderate only when it overlaps cruise.
                    if layer.risk == CATRiskLevel.SEVERE:
                        severe_layers.append(layer)
                    elif layer.risk == CATRiskLevel.MODERATE and at_cruise:
                        moderate_cruise_layers.append(layer)

                if vm.max_w_fpm is not None and abs(vm.max_w_fpm) > strong_w_fpm:
                    if vm.max_w_level_ft is not None and abs(vm.max_w_level_ft - cruise) < 3000:
                        point_affected = True
                        strong_w_here = True

                if point_affected:
                    affected += 1

                # Per-point ribbon verdict + CAT-layer cutout (#375). Strong-w is
                # resolved to a single level (max_w_level_ft), not a band — it
                # contributes amber to the ribbon but no strong_updraft cutout
                # (skip the kind rather than invent geometry).
                if severe_layers:
                    severity = HighlightSeverity.RED
                    band = severe_layers
                elif moderate_cruise_layers or strong_w_here:
                    severity = HighlightSeverity.AMBER
                    band = moderate_cruise_layers
                else:
                    severity = HighlightSeverity.GREEN
                    band = []

                ribbon_points.append((dist, severity))
                if band:
                    region_cells.append((dist, FlaggedCell(
                        kind="cat_layer",
                        severity=severity,
                        base_ft=int(min(la.base_ft for la in band)),
                        top_ft=int(max(la.top_ft for la in band)),
                    )))
                else:
                    region_cells.append((dist, None))

            ext = format_extent(affected, total, ctx.total_distance_nm)
            loc = ctx.locale
            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            elif has_severe:
                status = AdvisoryStatus.RED
                detail = adv_t("turbulence.severe_over", loc, extent=ext)
            elif affected == 0:
                status = AdvisoryStatus.GREEN
                detail = adv_t("turbulence.smooth", loc)
            else:
                status = pct_above_threshold(affected, total, route_pct_amber, red_pct=50)
                risk_label = worst_cat.value.upper() if worst_cat != CATRiskLevel.NONE else "Turbulence"
                detail = adv_t("turbulence.risk_over", loc, risk=risk_label, extent=ext)

            # Coverage tolerance (#391): a smooth verdict from soundings-with-vm at
            # too small a share of the route cannot vouch for the unassessed rest.
            if status == AdvisoryStatus.GREEN and below_coverage(total, len(ctx.analyses)):
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)

            # Highlights (#375) only when the model has data.
            highlights = None
            if total > 0:
                ribbon = build_ribbon(ribbon_points, ctx.total_distance_nm)
                highlights = AdvisoryHighlights(
                    ribbon=ribbon,
                    regions=build_regions(region_cells, ctx.total_distance_nm),
                    peak_dist_nm=ribbon_peak(ribbon),
                )

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
                highlights=highlights,
            ))

        return RouteAdvisoryResult.from_per_model("turbulence", per_model, params)
