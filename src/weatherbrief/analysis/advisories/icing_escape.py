"""Icing escape advisory — can escape icing by descending to warm air (non-FIKI)."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    build_cost_model,
    icing_zones_in_altitude_range,
    max_terrain_near_point,
    pct_above_threshold,
    terrain_at_distance,
    to_mitigation_profile,
)
from weatherbrief.analysis.advisories.evidence import (
    EvidenceSample,
    icing_method_id,
    icing_method_is_available,
    summarize_evidence,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.analysis.advisories.vertical_profile import (
    INF,
    Blockage,
    CostModel,
    Profile,
    solve,
)
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    IcingRisk,
    IcingType,
    Mitigation,
    MitigationKind,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)

# Finite crossing cost for the ONLY icing a non-FIKI aircraft may be advised to transit:
# thin/light RIME (design decision 8 — "only thin/light rime is finite-crossable … the
# solver must not casually price non-FIKI icing transit"). Everything heavier is a hard
# wall (see ``_icing_cell_cost``): MODERATE/SEVERE at any type, non-rime light (clear/
# mixed ice), and SLD/freezing precip. The value only has to make the solver prefer a
# hazard-free reroute (0) over crossing the rime when one exists.
_LIGHT_RIME_COST = 1.0


ICING_METRIC_BY_METHOD = {
    "ogimet_dd": "icing_risk",
    "ogimet_nwp": "icing_ogimet_nwp_risk",
    "sfip": "sfip_risk",
    "ieng": "ieng_icing_risk",
}


def _icing_cell_cost(sounding, alt_ft: float) -> float:
    """Occupancy cost of one altitude at one route point for the icing cost field.

    ``0`` below the freezing level (warm air — icing can't persist, feasible even in
    cloud); ``INF`` for any icing a non-FIKI aircraft must route *around* rather than
    through — SLD/freezing precip, MODERATE/SEVERE at any type, and non-rime (clear/mixed)
    light ice; ``_LIGHT_RIME_COST`` only for thin/light RIME, the sole soft wall (design
    decision 8). This is the only advisory-specific piece; the solver, floor, ceiling,
    anchoring and profile output are shared (design decision 3).
    """
    fz = sounding.indices.freezing_level_ft if sounding.indices else None
    if fz is not None and alt_ft < fz:
        return 0.0
    worst = 0.0
    for z in sounding.icing_zones:
        if not (z.base_ft <= alt_ft <= z.top_ft):
            continue
        if z.risk == IcingRisk.NONE and not z.sld_risk:
            continue  # no meaningful icing in this zone at this altitude
        if z.sld_risk or z.risk != IcingRisk.LIGHT or z.icing_type != IcingType.RIME:
            return INF  # hard wall — never priced as a "climb-through" for non-FIKI
        worst = max(worst, _LIGHT_RIME_COST)  # thin/light rime — the one soft wall
    return worst


def _build_icing_cost_model(
    ctx: RouteContext,
    model: str,
    terrain_margin_ft: float,
) -> CostModel | None:
    """Icing cost model: the shared builder plus this advisory's icing hazard→cost mapping.

    Floor is ``terrain + terrain_margin_ft`` (the same clearance the escape check already
    uses). For the icing soft wall the floor-reachable band climbs *through* light rime
    (finite), stopping only at hard walls (see ``_icing_cell_cost``). Bin construction,
    terrain-floor / ceiling walling and anchoring all live in ``build_cost_model``.
    """
    return build_cost_model(ctx, model, _icing_cell_cost, terrain_margin_ft)


def _icing_escape_mitigations(
    ctx: RouteContext,
    model: str,
    terrain_margin_ft: float,
    status: AdvisoryStatus,
    loc: str | None,
) -> list[Mitigation]:
    """Derive an icing-escape mitigation from the solved min-icing vertical profile (#335).

    Only when icing is flagged (AMBER/RED). Solves for the lowest-icing continuous
    profile and, if it is materially cleaner than sitting at planned cruise, reports the
    escape: descend to warm air below the freezing level, climb on-top, or — the maneuver
    the old single-transition code could not express — climb over the icing then descend
    below it before destination (a two-transition profile). Advice only; the grade is
    untouched. A :class:`Blockage` (no continuous flyable band) yields nothing.
    """
    if status not in (AdvisoryStatus.AMBER, AdvisoryStatus.RED):
        return []

    model_cm = _build_icing_cost_model(ctx, model, terrain_margin_ft)
    if model_cm is None:
        return []

    cruise = ctx.cruise_altitude_ft
    result = solve(model_cm, preferred_alt_ft=cruise)
    if isinstance(result, Blockage):
        return []

    profile: Profile = result
    bins = model_cm.bin_altitudes_ft
    cbin = min(range(len(bins)), key=lambda b: abs(bins[b] - cruise))
    cruise_cost = sum(col[cbin] for col in model_cm.cost_field)

    # Only worth advising if the profile is meaningfully less iced than cruise itself.
    if cruise_cost <= 0 or profile.total_cost >= cruise_cost:
        return []

    max_alt = max(s.alt_ft for s in profile.segments)
    min_alt = min(s.alt_ft for s in profile.segments)
    # GREEN = a fully ice-free escape (zero residual cost). Any residual cost can now
    # only be thin/light RIME (everything heavier is INF and never routed through — see
    # _icing_cell_cost), so AMBER is the right ceiling for a non-zero-cost escape.
    mitigated = AdvisoryStatus.GREEN if profile.total_cost == 0 else AdvisoryStatus.AMBER

    if max_alt > cruise and min_alt < cruise:
        detail = adv_t("icing_escape.mitigation.profile", loc)
        alt: int | None = None
    elif min_alt < cruise:
        alt = min_alt
        detail = adv_t("icing_escape.mitigation.descend", loc, alt=alt)
    else:
        alt = max_alt
        detail = adv_t("icing_escape.mitigation.climb", loc, alt=alt)

    return [Mitigation(
        kind=MitigationKind.ALTITUDE,
        addresses="icing_escape",
        detail=detail,
        mitigated_status=mitigated,
        altitude_ft=alt,
        profile=to_mitigation_profile(profile),
    )]


@register
class IcingEscapeEvaluator:
    """Evaluates whether icing can be escaped by descending to warm air."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="icing_escape",
            name="Icing Escape (non-FIKI)",
            short_description="Icing at cruise and warm-air escape viability",
            description=(
                "For non-FIKI aircraft. First detects icing at or below cruise "
                "altitude, then checks whether descending to warm air above terrain "
                "is viable as an escape route. The reported percentage reflects "
                "icing coverage along the route."
            ),
            category="icing",
            timing_class="scan",
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="terrain_margin_ft",
                    label="Terrain margin",
                    description="Minimum clearance above terrain for escape",
                    type="altitude",
                    unit="ft",
                    default=1000,
                    min=500,
                    max=3000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="tight_margin_ft",
                    label="Tight margin",
                    description="Freezing level below this margin above terrain triggers amber",
                    type="altitude",
                    unit="ft",
                    default=2000,
                    min=1000,
                    max=5000,
                    step=500,
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
                    key="icing_coverage_pct_amber",
                    label="Icing extent (amber)",
                    description=(
                        "Percentage of route with icing (but escape available) "
                        "to trigger amber. Only applies when warm-air descent "
                        "is viable everywhere along the route."
                    ),
                    type="percent",
                    unit="%",
                    default=20,
                    min=5,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="no_escape_pct_red",
                    label="No escape (red)",
                    description=(
                        "Percentage of route where icing exists but descending "
                        "to warm air is blocked by terrain. Any no-escape point "
                        "triggers amber; exceeding this threshold escalates to red."
                    ),
                    type="percent",
                    unit="%",
                    default=15,
                    min=5,
                    max=50,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        terrain_margin = params.get("terrain_margin_ft", 1000)
        tight_margin = params.get("tight_margin_ft", 2000)
        icing_altitude_buffer_ft = params.get("icing_altitude_buffer_ft", 2000)
        # Accept both new and legacy param names for backwards compatibility
        icing_coverage_pct_amber = params.get(
            "icing_coverage_pct_amber",
            params.get("route_pct_amber", 20),
        )
        no_escape_pct_red = params.get(
            "no_escape_pct_red",
            params.get("min_route_pct", 15),
        )
        ordered_analyses = sorted(
            ctx.analyses,
            key=lambda rpa: (rpa.distance_from_origin_nm, rpa.point_index),
        )

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            evaluated: set[int] = set()
            complete: set[int] = set()
            affected: set[int] = set()
            no_escape_points: set[int] = set()
            tight_margin_points: set[int] = set()
            samples: list[EvidenceSample] = []
            method_id = icing_method_id(ctx.icing_method)
            metric_id = ICING_METRIC_BY_METHOD.get(method_id or "")

            for rpa in ordered_analyses:
                sounding = rpa.sounding.get(model)
                if not icing_method_is_available(sounding, ctx.icing_method):
                    continue
                assert sounding is not None
                point_index = rpa.point_index
                evaluated.add(point_index)

                relevant_zones = icing_zones_in_altitude_range(
                    sounding.icing_zones,
                    0,
                    ctx.cruise_altitude_ft + icing_altitude_buffer_ft,
                )
                if not relevant_zones:
                    complete.add(point_index)
                    continue

                affected.add(point_index)
                for zone in relevant_zones:
                    samples.append(
                        EvidenceSample(
                            point_index=point_index,
                            severity=AdvisoryStatus.AMBER,
                            reason_code="icing_exposure",
                            metric_id=metric_id,
                            method_id=method_id,
                            lower_altitude_ft=round(zone.base_ft),
                            upper_altitude_ft=round(zone.top_ft),
                        )
                    )

                # Get freezing level and terrain
                fz_level_ft = None
                if sounding.indices and sounding.indices.freezing_level_ft is not None:
                    fz_level_ft = sounding.indices.freezing_level_ft

                terrain_ft = max_terrain_near_point(
                    ctx.elevation, rpa.distance_from_origin_nm
                )
                if terrain_ft is None:
                    terrain_ft = terrain_at_distance(
                        ctx.elevation,
                        rpa.distance_from_origin_nm,
                    )

                if fz_level_ft is None or terrain_ft is None:
                    continue
                complete.add(point_index)

                if fz_level_ft < terrain_ft + terrain_margin:
                    no_escape_points.add(point_index)
                    reason = "icing_no_warm_escape"
                    severity = AdvisoryStatus.RED
                elif fz_level_ft < terrain_ft + tight_margin:
                    tight_margin_points.add(point_index)
                    reason = "icing_tight_warm_escape"
                    severity = AdvisoryStatus.AMBER
                else:
                    continue

                for zone in relevant_zones:
                    samples.append(
                        EvidenceSample(
                            point_index=point_index,
                            severity=severity,
                            reason_code=reason,
                            metric_id="freezing_level_ft",
                            method_id=method_id,
                            lower_altitude_ft=round(zone.base_ft),
                            upper_altitude_ft=round(zone.top_ft),
                        )
                    )

            summary = summarize_evidence(
                route_points=ordered_analyses,
                total_distance_nm=ctx.total_distance_nm,
                evaluated_point_indices=evaluated,
                complete_point_indices=complete,
                affected_point_indices=affected,
                evidence_samples=samples,
            )
            total = summary.total_points
            no_escape_count = len(no_escape_points)

            # Determine model status
            loc = ctx.locale
            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            elif no_escape_count > 0 and pct_above_threshold(
                no_escape_count, total, no_escape_pct_red,
            ) != AdvisoryStatus.GREEN:
                status = AdvisoryStatus.RED
                ext = summary.format_extent()
                detail = adv_t("icing_escape.no_escape", loc, extent=ext, count=no_escape_count)
            elif no_escape_count > 0:
                status = AdvisoryStatus.AMBER
                ext = summary.format_extent()
                detail = adv_t("icing_escape.no_escape", loc, extent=ext, count=no_escape_count)
            elif summary.affected_points == 0:
                status = AdvisoryStatus.GREEN
                detail = adv_t("icing_escape.no_icing", loc)
            else:
                status = pct_above_threshold(
                    summary.affected_points,
                    total,
                    icing_coverage_pct_amber,
                )
                ext = summary.format_extent()
                if status == AdvisoryStatus.GREEN and tight_margin_points:
                    status = AdvisoryStatus.AMBER
                    detail = adv_t("icing_escape.tight_margin", loc, extent=ext)
                elif status == AdvisoryStatus.GREEN:
                    detail = adv_t("icing_escape.warm_escape", loc, extent=ext)
                else:
                    detail = adv_t("icing_escape.warm_escape", loc, extent=ext)

            # Escape mitigation from the shared vertical-profile solver (advice only,
            # never alters the grade above) — #335.
            mitigations = _icing_escape_mitigations(ctx, model, terrain_margin, status, loc)

            missing_detail = adv_t(
                "no_data" if summary.data_state == "unavailable" else "partial_data",
                loc,
            )
            per_model.append(
                summary.build_result(
                    model=model,
                    status=status,
                    detail=detail,
                    unavailable_detail=missing_detail,
                    primary_method_id=method_id,
                    mitigations=mitigations,
                )
            )

        return RouteAdvisoryResult.from_per_model("icing_escape", per_model, params)
