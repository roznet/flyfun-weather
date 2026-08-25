"""Turbulence advisory — ride quality acceptable at cruise."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    EXTENT_MIN_NM,
    extent_min_nm_param,
    EvidenceSample,
    FlaggedCell,
    format_extent,
    grade_extent,
    summarize_evidence,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
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
                "Severe free-atmosphere CAT triggers RED regardless of route "
                "percentage; severe boundary-layer shear is floored at AMBER "
                "and graded by route percentage. RED via coverage requires "
                "moderate-or-worse risk — light-only coverage caps at AMBER."
            ),
            category="turbulence",
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="extent_pct_amber",
                    label="% of route with turbulence (amber)",
                    description="Route percentage with turbulence for amber",
                    type="percent",
                    unit="%",
                    default=20,
                    min=5,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="extent_pct_red",
                    label="% of route with moderate+ turbulence (red)",
                    description=(
                        "Route percentage with moderate-or-worse turbulence for red"
                    ),
                    type="percent",
                    unit="%",
                    default=50,
                    min=10,
                    max=100,
                    step=5,
                ),
                extent_min_nm_param(),
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
        extent_pct_amber = params.get("extent_pct_amber", 20)
        # Was a hardcoded ``red_pct=50`` inside the gate, invisible in the
        # catalog and therefore untunable (#571 Stage 2).
        extent_pct_red = params.get("extent_pct_red", 50)
        extent_min_nm = params.get("extent_min_nm", EXTENT_MIN_NM)
        strong_w_fpm = params.get("strong_w_fpm", 200)
        cruise = ctx.cruise_altitude_ft

        _CAT_ORDER = [CATRiskLevel.NONE, CATRiskLevel.LIGHT, CATRiskLevel.MODERATE, CATRiskLevel.SEVERE]
        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            has_severe = False
            has_bl_severe = False
            # Points with MODERATE-or-worse at cruise (or a strong updraft).
            # The RED tier of the percentage gate keys on this count, not on
            # any-risk coverage: LIGHT chop over half the route is an AMBER
            # ride-quality note, not a RED hazard (#533 follow-up — with the
            # corrected layer geometry, light/moderate coverage is what an
            # honest Ri read of a sheared low-level day produces).
            significant_points = 0
            worst_cat = CATRiskLevel.NONE
            # One evidence sample per route point (#393). The ribbon and the grade
            # key on DIFFERENT predicates here, so each sample carries both: the
            # ribbon ``severity`` (SEVERE CAT anywhere in the column → red — the
            # cutout showing where it sits is the ambiguity the highlight
            # resolves) and the grade ``affected`` flag (CAT/updraft in the cruise
            # band). ``summarize_evidence`` counts ``affected`` for the grade while
            # the ribbon renders ``severity``; they no longer live in two loops.
            samples: list[EvidenceSample] = []

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
                    samples.append(EvidenceSample(
                        distance_nm=dist, assessed=False,
                        severity=HighlightSeverity.UNAVAILABLE,
                    ))
                    continue

                point_affected = False
                point_significant = False
                # Free-atmosphere SEVERE at cruise — the tier that forces RED
                # and the one the "Severe CAT over …" sentence must quote.
                point_severe = False
                strong_w_here = False
                severe_layers: list = []
                moderate_cruise_layers: list = []

                for layer in vm.cat_risk_layers:
                    at_cruise = layer.base_ft <= cruise <= layer.top_ft
                    if at_cruise and layer.risk != CATRiskLevel.NONE:
                        point_affected = True
                        if layer.risk != CATRiskLevel.LIGHT:
                            point_significant = True
                        if _CAT_ORDER.index(layer.risk) > _CAT_ORDER.index(worst_cat):
                            worst_cat = layer.risk
                        if layer.risk == CATRiskLevel.SEVERE:
                            # Boundary-layer severe shear does NOT bypass the
                            # route-percentage gate (#533): one low-level shear
                            # sheet at 1 of 17 points is not a route-wide
                            # hazard. It still floors the advisory at AMBER
                            # below, and still goes RED once it covers enough
                            # of the route.
                            if layer.boundary_layer:
                                has_bl_severe = True
                            else:
                                has_severe = True
                                point_severe = True
                    # Highlight geometry: severe counts anywhere in the
                    # column, moderate only when it overlaps cruise.
                    if layer.risk == CATRiskLevel.SEVERE:
                        severe_layers.append(layer)
                    elif layer.risk == CATRiskLevel.MODERATE and at_cruise:
                        moderate_cruise_layers.append(layer)

                if vm.max_w_fpm is not None and abs(vm.max_w_fpm) > strong_w_fpm:
                    if vm.max_w_level_ft is not None and abs(vm.max_w_level_ft - cruise) < 3000:
                        point_affected = True
                        point_significant = True
                        strong_w_here = True

                if point_significant:
                    significant_points += 1

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

                region = None
                if band:
                    region = FlaggedCell(
                        kind="cat_layer",
                        severity=severity,
                        base_ft=int(min(la.base_ft for la in band)),
                        top_ft=int(max(la.top_ft for la in band)),
                        metric_id="cat_risk",
                    )
                # ``affected`` (grade) keys on the cruise band; ``severity``
                # (ribbon) on severe-anywhere — deliberately decoupled (#393).
                # The tiers are tagged so each sentence can quote the extent of
                # the tier it *names* (#571 D1): "Severe CAT over 146nm (25%)"
                # was the light-and-above coverage while severe held one point.
                tags = set()
                if point_significant:
                    tags.add("significant")
                if point_severe:
                    tags.add("severe")
                samples.append(EvidenceSample(
                    distance_nm=dist, assessed=True, severity=severity,
                    affected=point_affected, region=region,
                    tags=frozenset(tags),
                ))

            summary = summarize_evidence(
                samples, ctx.total_distance_nm,
                speed_kt=ctx.cruise_groundspeed_kt,
            )
            total = summary.assessed
            affected = summary.affected
            # One extent per severity tier (#571 D1). The severity word and the
            # coverage beside it must describe the same points: a single
            # free-atmosphere severe layer is a real hazard and still forces RED,
            # but it is "Severe CAT over 9nm", not over the light-and-above 146nm.
            significant_extent = summary.extent_of(lambda s: "significant" in s.tags)
            ext = format_extent(summary.extent)
            ext_severe = format_extent(
                summary.extent_of(lambda s: "severe" in s.tags)
            )
            ext_significant = format_extent(significant_extent)
            loc = ctx.locale
            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            elif has_severe:
                status = AdvisoryStatus.RED
                detail = adv_t("turbulence.severe_over", loc, extent=ext_severe)
            elif affected == 0:
                status = AdvisoryStatus.GREEN
                detail = adv_t("turbulence.smooth", loc)
            else:
                # AMBER from any-risk coverage; the RED tier requires the
                # moderate-or-worse count to clear the 50% bar. Light-only
                # coverage over most of the route is a ride-quality note, not
                # a RED hazard.
                status = grade_extent(
                    summary.extent,
                    amber_pct=extent_pct_amber, min_nm=extent_min_nm,
                )
                if (
                    grade_extent(
                        significant_extent,
                        amber_pct=extent_pct_amber, red_pct=extent_pct_red,
                        min_nm=extent_min_nm,
                    )
                    == AdvisoryStatus.RED
                ):
                    status = AdvisoryStatus.RED
                # A severe boundary-layer layer sitting at cruise is at least
                # AMBER even below the route-percentage threshold — grading it
                # GREEN while the detail reads "SEVERE over …" would be
                # incoherent (#533).
                if has_bl_severe and status == AdvisoryStatus.GREEN:
                    status = AdvisoryStatus.AMBER
                risk_label = worst_cat.value.upper() if worst_cat != CATRiskLevel.NONE else "Turbulence"
                # MODERATE-or-worse is a tier of its own: naming it and then
                # quoting the any-risk union describes two different populations
                # in one sentence. LIGHT (and the no-CAT strong-updraft case)
                # legitimately quotes the any-risk extent — that IS its tier.
                tier_ext = (
                    ext_significant
                    if worst_cat not in (CATRiskLevel.NONE, CATRiskLevel.LIGHT)
                    else ext
                )
                detail = adv_t("turbulence.risk_over", loc, risk=risk_label, extent=tier_ext)

            # Coverage tolerance (#391): a smooth verdict from soundings-with-vm at
            # too small a share of the route cannot vouch for the unassessed rest.
            if status == AdvisoryStatus.GREEN and summary.below_coverage:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)

            # Highlights (#375) only when the model has data.
            highlights = summary.highlights if total > 0 else None

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
                extent=summary.extent,
                highlights=highlights,
            ))

        return RouteAdvisoryResult.from_per_model("turbulence", per_model, params)
