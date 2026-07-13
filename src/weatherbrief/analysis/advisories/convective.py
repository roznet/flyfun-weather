"""Convective advisory — can fly around convective activity."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    FlaggedCell,
    build_regions,
    build_ribbon,
    driving_method_id,
    format_extent,
    pct_above_threshold,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.analysis.sounding.convective import convective_cross_check
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

# Ordered from least to most severe
_RISK_ORDER = [
    ConvectiveRisk.NONE,
    ConvectiveRisk.MARGINAL,
    ConvectiveRisk.LOW,
    ConvectiveRisk.MODERATE,
    ConvectiveRisk.HIGH,
    ConvectiveRisk.EXTREME,
]

# Threshold for "actual convective concern" — the headline extent is anchored
# here (MODERATE+), separate from the LOW min_risk floor that drives the colour
# (#300).
_MOD_IDX = _RISK_ORDER.index(ConvectiveRisk.MODERATE)


def _coverage_suffix(max_cover_pct: float | None) -> str:
    """Append coverage label when NWP convective cover data is available."""
    if max_cover_pct is None:
        return ""
    if max_cover_pct >= 75:
        label = "extensive"
    elif max_cover_pct >= 50:
        label = "widespread"
    elif max_cover_pct >= 25:
        label = "scattered"
    else:
        label = "isolated"
    return f", {label} ({int(max_cover_pct)}% cover)"


@register
class ConvectiveEvaluator:
    """Evaluates convective activity along the route."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="convective",
            name="Convective Activity",
            short_description="Can fly around convective activity",
            description=(
                "Uses convective risk assessment per point. "
                "Points where tops are below cruise altitude (with clearance margin) are ignored. "
                "High/Extreme risk at any point triggers RED."
            ),
            category="convective",
            timing_class="scan",
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="min_risk",
                    label="Min risk level",
                    description="Minimum risk level that counts (0=NONE, 1=MARGINAL, 2=LOW, 3=MODERATE)",
                    type="number",
                    default=2,
                    min=1,
                    max=4,
                    step=1,
                ),
                AdvisoryParameterDef(
                    key="affected_pct_amber",
                    label="Route % (amber)",
                    description="Route percentage affected for amber",
                    type="percent",
                    unit="%",
                    default=20,
                    min=5,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="affected_pct_red",
                    label="Route % (red)",
                    description="Route percentage affected for red",
                    type="percent",
                    unit="%",
                    default=50,
                    min=10,
                    max=100,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="top_clearance_ft",
                    label="Top clearance (ft)",
                    description="Ignore convection whose tops are this far below cruise altitude",
                    type="number",
                    unit="ft",
                    default=2000,
                    min=0,
                    max=5000,
                    step=500,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        min_risk_idx = int(params.get("min_risk", 2))
        affected_pct_amber = params.get("affected_pct_amber", 20)
        affected_pct_red = params.get("affected_pct_red", 50)
        top_clearance_ft = params.get("top_clearance_ft", 2000)

        min_risk = _RISK_ORDER[min(min_risk_idx, len(_RISK_ORDER) - 1)]
        cruise_ft = ctx.cruise_altitude_ft

        per_model: list[ModelAdvisoryResult] = []
        peak_by_model: dict[str, ConvectiveRisk] = {}

        for model in ctx.models:
            total = 0
            affected = 0  # >= min_risk (LOW floor) — drives the colour
            affected_mod = 0  # >= MODERATE — anchors the headline extent (#300)
            has_high = False
            worst_risk = ConvectiveRisk.NONE
            below_cruise_count = 0  # risky points skipped because tops below cruise
            max_cover_pct: float | None = None
            # Details-only DD-vs-model-scheme cross-check tally (never grades).
            xcheck_fired = 0
            xcheck_dirs: dict[str, int] = {}
            xcheck_worst_dd_risk = ConvectiveRisk.NONE
            # Per-point highlight geometry (#373). Every route point contributes
            # a ribbon severity (no sounding → UNAVAILABLE, unaffected → GREEN);
            # flagged points also contribute a tower cutout.
            ribbon_points: list[tuple[float, HighlightSeverity]] = []
            region_cells: list[tuple[float, FlaggedCell | None]] = []
            # Worst affected point for peak_dist_nm: max graded risk, ties → CAPE.
            peak_key: tuple[int, float] | None = None
            peak_dist: float | None = None

            for rpa in ctx.analyses:
                dist = rpa.distance_from_origin_nm or 0.0
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    ribbon_points.append((dist, HighlightSeverity.UNAVAILABLE))
                    region_cells.append((dist, None))
                    continue
                total += 1

                # Independent of the grade filters below: compare the chosen
                # thermo (CAPE-derived) risk against the model's own convective
                # scheme. Use convective_thermo explicitly (matches the digest
                # and dd_nwp_agreement) so this stays a DD-vs-NWP comparison even
                # if convective ever becomes the chosen (possibly NWP) method.
                # Do NOT fall back to sounding.convective: when the active track
                # is the NWP one (convective_method="nwp") that would pass the
                # NWP assessment as both sides → circular self-comparison the
                # nwp_cape_fallback guard can't catch (#283 review). If thermo is
                # missing, convective_cross_check returns None on its own.
                thermo_conv = sounding.convective_thermo
                xc = convective_cross_check(thermo_conv, sounding.convective_nwp)
                if xc is not None:
                    xcheck_fired += 1
                    xcheck_dirs[xc.direction] = xcheck_dirs.get(xc.direction, 0) + 1
                    if xc.direction == "dd_not_corroborated" and thermo_conv is not None:
                        if _RISK_ORDER.index(thermo_conv.risk_level) > _RISK_ORDER.index(
                            xcheck_worst_dd_risk
                        ):
                            xcheck_worst_dd_risk = thermo_conv.risk_level

                conv = sounding.convective
                if conv is None:
                    # Sounding present but no convective assessment: not a hazard
                    # we can locate — grade the ribbon GREEN (not UNAVAILABLE,
                    # which is reserved for a missing sounding).
                    ribbon_points.append((dist, HighlightSeverity.GREEN))
                    region_cells.append((dist, None))
                    continue

                # Guardrail (#283): the active track may now be the model-native
                # NWP track (convective_method defaults to "nwp"), whose risk can
                # read quiet where a capped loaded-gun DD reads HIGH — exactly
                # where models under-fire. A quiet NWP must never *suppress* DD,
                # so the grade floors at the DD tier (safety asymmetry,
                # meteorology-decisions §4/§5). The two tracks stay independent;
                # the divergence is surfaced via the cross-check below and the
                # dd_nwp_agreement advisory, not blended into the DD tier. When
                # the active track is DD this is a no-op.
                graded_risk = conv.risk_level
                # Did the thermo floor decide this point, or the active track?
                # Both produce an identical RED tower, and the difference is the
                # whole story: "the model's own convective scheme sees storms"
                # versus "the model is quiet, but the thermodynamics are loaded".
                # This is the compound provenance we deliberately keep OUT of
                # ``method_id`` — that field answers "which layer do I draw?" and
                # the answer is still the active track's. ``reason_code`` answers
                # "why is this flagged?", which is a different question.
                floored_by_thermo = False
                if thermo_conv is not None and _RISK_ORDER.index(
                    thermo_conv.risk_level
                ) > _RISK_ORDER.index(graded_risk):
                    graded_risk = thermo_conv.risk_level
                    floored_by_thermo = True
                reason = "thermo_floor" if floored_by_thermo else "active_track"

                risk_idx = _RISK_ORDER.index(graded_risk)
                if risk_idx < _RISK_ORDER.index(min_risk):
                    # Below the min risk floor → GREEN on the ribbon (checked,
                    # nothing worth flagging here).
                    ribbon_points.append((dist, HighlightSeverity.GREEN))
                    region_cells.append((dist, None))
                    continue

                # Skip if convective tops are well below cruise altitude. When the
                # DD floor raised the grade and the active (quiet NWP) track has no
                # geometry (top_ft=None), fall back to the thermo EL so altitude
                # awareness is preserved — otherwise top_ft=None bypasses the
                # filter and fires the advisory for convection that tops out below
                # cruise (#283 review I1). Pre-#283 this point used the thermo EL
                # because a quiet NWP returned None and the slot fell back to DD.
                # When the DD floor raised the grade, the DD tower (EL) is what
                # justifies it — so use the deeper of the active and thermo tops
                # for altitude awareness. This covers both a missing NWP top
                # (top_ft=None) and a shallow NWP top below the DD EL (#283
                # review): otherwise a quiet/shallow NWP top would filter out a
                # point graded HIGH by a DD tower that does reach cruise.
                check_top_ft = conv.top_ft
                if thermo_conv is not None and graded_risk != conv.risk_level:
                    thermo_top = thermo_conv.top_ft
                    if check_top_ft is None or (
                        thermo_top is not None and thermo_top > check_top_ft
                    ):
                        check_top_ft = thermo_top
                if (
                    check_top_ft is not None
                    and check_top_ft + top_clearance_ft <= cruise_ft
                ):
                    below_cruise_count += 1
                    # Tops below cruise (with clearance) → not a hazard at cruise
                    # → GREEN on the ribbon, no cutout.
                    ribbon_points.append((dist, HighlightSeverity.GREEN))
                    region_cells.append((dist, None))
                    continue

                affected += 1
                if risk_idx >= _MOD_IDX:
                    affected_mod += 1
                if risk_idx > _RISK_ORDER.index(worst_risk):
                    worst_risk = graded_risk

                is_high = graded_risk in (ConvectiveRisk.HIGH, ConvectiveRisk.EXTREME)
                if is_high:
                    has_high = True

                if conv.cover_pct is not None:
                    max_cover_pct = max(max_cover_pct or 0, conv.cover_pct)

                # Highlight geometry for this flagged point (#373).
                severity = HighlightSeverity.RED if is_high else HighlightSeverity.AMBER
                ribbon_points.append((dist, severity))
                # Base from the same resolution as the top (model base, thermo-LFC
                # fallback when the DD floor raised the grade).
                check_base_ft = conv.base_ft
                if (
                    check_base_ft is None
                    and thermo_conv is not None
                    and graded_risk != conv.risk_level
                ):
                    check_base_ft = thermo_conv.base_ft
                # A "tower" cutout requires BOTH base and top resolved. A known top
                # with an unknown base (e.g. ECMWF nwp_lcl_top with no LCL) must
                # NOT render as a solid box down to terrain — the client draws
                # base=None to the ground, which would imply a base the model
                # doesn't have and erase the tower/ghost distinction. So fall back
                # to the full-column ghost whenever either bound is missing
                # (mirrors nwp-convective-bg.ts's depth-unresolved column).
                # The convective track that actually sourced this geometry —
                # "nwp" / "thermo" under the CAPE fallback (#408). Deliberately
                # NOT compounded when the thermo floor raised the grade: the
                # floor changes the severity, not where the evidence came from,
                # so the chip selects the layer the evidence is drawn from.
                conv_method = sounding.convective_method_effective
                if check_top_ft is not None and check_base_ft is not None:
                    region_cells.append((dist, FlaggedCell(
                        kind="tower",
                        severity=severity,
                        base_ft=int(check_base_ft),
                        top_ft=int(check_top_ft),
                        reason_code=reason,
                        metric_id="convective_risk",
                        method_id=conv_method,
                    )))
                else:
                    # Depth-unresolved (nwp_precip / cover-only, or a resolved top
                    # with unknown base): full-column ghost.
                    region_cells.append((dist, FlaggedCell(
                        kind="tower_unresolved",
                        severity=severity,
                        base_ft=None,
                        top_ft=None,
                        reason_code=reason,
                        metric_id="convective_risk",
                        method_id=conv_method,
                    )))

                # Peak = worst graded risk, ties broken by highest CAPE — matches
                # the MCP deep-link's highest-CAPE peak.
                cape = conv.cape_jkg
                if cape is None and thermo_conv is not None:
                    cape = thermo_conv.cape_jkg
                key = (risk_idx, cape if cape is not None else 0.0)
                if peak_key is None or key > peak_key:
                    peak_key = key
                    peak_dist = dist

            ext = format_extent(affected, total, ctx.total_distance_nm)
            ext_mod = format_extent(affected_mod, total, ctx.total_distance_nm)
            loc = ctx.locale
            cover_suffix = _coverage_suffix(max_cover_pct)
            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            elif affected == 0:
                status = AdvisoryStatus.GREEN
                if below_cruise_count > 0:
                    detail = adv_t("convective.below_cruise", loc, count=below_cruise_count)
                else:
                    detail = adv_t("convective.none", loc)
            else:
                # Colour grade is unchanged (#300 is display-only): HIGH/EXTREME
                # anywhere → RED; otherwise threshold on the LOW-floor extent,
                # capped at AMBER when the peak is only LOW.
                if has_high:
                    status = AdvisoryStatus.RED
                else:
                    status = pct_above_threshold(affected, total, affected_pct_amber, affected_pct_red)
                    if worst_risk == ConvectiveRisk.LOW and status == AdvisoryStatus.RED:
                        status = AdvisoryStatus.AMBER
                # Headline anchors on the MODERATE+ extent + named peak, so the
                # severity word (peak) and the coverage (MODERATE+ extent) are
                # never conflated. When nothing reaches MODERATE (LOW-only floor),
                # fall back to "primed, not firing" so favorable-but-quiet CAPE
                # doesn't masquerade as active convection (#300).
                if affected_mod > 0:
                    detail = adv_t(
                        "convective.risk_over_mod", loc,
                        extent=ext_mod, peak=worst_risk.value.upper(),
                    ) + cover_suffix
                else:
                    # Calibrated for the default LOW floor (min_risk=2): "primed,
                    # not firing" fits a LOW peak. Under a non-default min_risk=1
                    # a MARGINAL-only route also lands here and the wording is a
                    # slight overstatement — accepted (rare, non-default; #302
                    # review).
                    detail = adv_t("convective.favorability", loc, extent=ext) + cover_suffix

            cross_check: str | None = None
            if xcheck_fired > 0:
                dominant = max(xcheck_dirs, key=lambda d: xcheck_dirs[d])
                # Extent reflects only the dominant direction's points, not the
                # combined fire count — otherwise a mix of both directions would
                # overstate the extent of the direction we're describing.
                xc_ext = format_extent(xcheck_dirs[dominant], total, ctx.total_distance_nm)
                if dominant == "dd_not_corroborated":
                    cross_check = (
                        f"DD {xcheck_worst_dd_risk.value.upper()} not corroborated — "
                        f"model convective scheme quiet over {xc_ext}"
                    )
                else:  # model_active_dd_quiet
                    cross_check = (
                        f"model convective scheme active where DD shows little/none "
                        f"over {xc_ext}"
                    )

            # Build highlights only when the model has data (total > 0).
            highlights = None
            if total > 0:
                highlights = AdvisoryHighlights(
                    ribbon=build_ribbon(ribbon_points, ctx.total_distance_nm),
                    regions=build_regions(region_cells, ctx.total_distance_nm),
                    peak_dist_nm=peak_dist,
                )

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
                affected_mod=affected_mod,
                cross_check=cross_check,
                highlights=highlights,
                primary_method_id=driving_method_id(highlights, status),
            ))
            peak_by_model[model] = worst_risk

        result = RouteAdvisoryResult.from_per_model("convective", per_model, params)
        # Override the generic representative-model detail with a cross-model
        # MODERATE+ range + peak, built here where the locale is available
        # (from_per_model is locale-agnostic). GREEN/UNAVAILABLE keep the
        # representative wording (none / below-cruise / no-data). (#300)
        # The per-model cover_suffix is intentionally dropped from this aggregate
        # line: a single representative model's cover is arbitrary across a
        # cross-model range. Cover stays visible in the per-model breakdowns
        # (#302 review).
        agg = result.aggregate_status
        if agg in (AdvisoryStatus.AMBER, AdvisoryStatus.RED):
            loc = ctx.locale
            matching = [m for m in per_model if m.status == agg]
            mod_models = [m for m in matching if m.affected_mod_points > 0]
            if mod_models:
                peak = max(
                    (peak_by_model[m.model] for m in mod_models),
                    key=lambda r: _RISK_ORDER.index(r),
                ).value.upper()
                pcts = sorted(round(m.affected_mod_pct) for m in mod_models)
                lo, hi = pcts[0], pcts[-1]
                if lo == hi:
                    result.aggregate_detail = adv_t(
                        "convective.risk_over_mod_pct", loc, pct=lo, peak=peak
                    )
                else:
                    result.aggregate_detail = adv_t(
                        "convective.risk_over_range", loc, min=lo, max=hi, peak=peak
                    )
            else:
                # LOW-only across every supporting model — favorability range.
                pcts = sorted(
                    round(m.affected_pct) for m in matching if m.total_points > 0
                )
                if pcts:
                    lo, hi = pcts[0], pcts[-1]
                    if lo == hi:
                        result.aggregate_detail = adv_t(
                            "convective.favorability_pct", loc, pct=lo
                        )
                    else:
                        result.aggregate_detail = adv_t(
                            "convective.favorability_range", loc, min=lo, max=hi
                        )
        return result
