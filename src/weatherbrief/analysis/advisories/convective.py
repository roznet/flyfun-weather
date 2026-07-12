"""Convective advisory — can fly around convective activity."""

from __future__ import annotations

import math

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import pct_above_threshold
from weatherbrief.analysis.advisories.evidence import (
    EvidenceSample,
    resolve_convective_point,
    summarize_evidence,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.analysis.sounding.convective import convective_cross_check
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ConvectiveRisk,
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

_STATUS_ORDER = {
    AdvisoryStatus.UNAVAILABLE: -1,
    AdvisoryStatus.GREEN: 0,
    AdvisoryStatus.AMBER: 1,
    AdvisoryStatus.RED: 2,
}


def _grade_risks(
    risks: list[ConvectiveRisk],
    total_points: int,
    affected_pct_amber: float,
    affected_pct_red: float,
) -> tuple[AdvisoryStatus, ConvectiveRisk]:
    """Apply the established convective colour policy to qualifying risks."""
    worst_risk = max(
        risks,
        key=_RISK_ORDER.index,
        default=ConvectiveRisk.NONE,
    )
    if total_points == 0:
        return AdvisoryStatus.UNAVAILABLE, worst_risk
    if not risks:
        return AdvisoryStatus.GREEN, worst_risk
    if worst_risk in (ConvectiveRisk.HIGH, ConvectiveRisk.EXTREME):
        return AdvisoryStatus.RED, worst_risk

    status = pct_above_threshold(
        len(risks),
        total_points,
        affected_pct_amber,
        affected_pct_red,
    )
    if worst_risk == ConvectiveRisk.LOW and status == AdvisoryStatus.RED:
        status = AdvisoryStatus.AMBER
    return status, worst_risk


def _finite_altitude(altitude_ft: float | None) -> float | None:
    if altitude_ft is None or not math.isfinite(altitude_ft):
        return None
    return altitude_ft


def _is_below_cruise(
    top_ft: float | None,
    cruise_ft: float,
    top_clearance_ft: float,
) -> bool:
    finite_top_ft = _finite_altitude(top_ft)
    return (
        finite_top_ft is not None
        and finite_top_ft + top_clearance_ft <= cruise_ft
    )


def _evidence_altitude_bounds(
    base_ft: float | None,
    top_ft: float | None,
) -> tuple[int | None, int | None]:
    finite_base_ft = _finite_altitude(base_ft)
    finite_top_ft = _finite_altitude(top_ft)
    if (
        finite_base_ft is None
        or finite_top_ft is None
        or finite_base_ft > finite_top_ft
    ):
        return None, None
    return round(finite_base_ft), round(finite_top_ft)


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
        ordered_analyses = sorted(
            ctx.analyses,
            key=lambda rpa: (rpa.distance_from_origin_nm, rpa.point_index),
        )

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            evaluated: set[int] = set()
            complete: set[int] = set()
            affected: set[int] = set()  # >= min_risk — drives the colour
            affected_mod: set[int] = set()  # >= MODERATE — headline extent
            samples: list[EvidenceSample] = []
            active_methods: list[str | None] = []
            active_path_risks: list[tuple[ConvectiveRisk, str | None]] = []
            qualifying_risks: list[
                tuple[ConvectiveRisk, str | None, bool]
            ] = []
            below_cruise_count = 0  # risky points skipped because tops below cruise
            max_cover_pct: float | None = None
            # Details-only DD-vs-model-scheme cross-check tally (never grades).
            xcheck_evaluated: set[int] = set()
            xcheck_dirs: dict[str, set[int]] = {}
            xcheck_worst_dd_risk = ConvectiveRisk.NONE

            for rpa in ordered_analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    continue
                point_index = rpa.point_index
                xcheck_evaluated.add(point_index)

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
                    xcheck_dirs.setdefault(xc.direction, set()).add(point_index)
                    if xc.direction == "dd_not_corroborated" and thermo_conv is not None:
                        if _RISK_ORDER.index(thermo_conv.risk_level) > _RISK_ORDER.index(
                            xcheck_worst_dd_risk
                        ):
                            xcheck_worst_dd_risk = thermo_conv.risk_level

                resolved = resolve_convective_point(sounding)
                active = resolved.selected
                if active is None or resolved.risk_level is None:
                    continue
                evaluated.add(point_index)
                if resolved.complete:
                    complete.add(point_index)

                # Guardrail (#283): the active track may now be the model-native
                # NWP track (convective_method defaults to "nwp"), whose risk can
                # read quiet where a capped loaded-gun DD reads HIGH — exactly
                # where models under-fire. A quiet NWP must never *suppress* DD,
                # so the grade floors at the DD tier (safety asymmetry,
                # meteorology-decisions §4/§5). The two tracks stay independent;
                # the divergence is surfaced via the cross-check below and the
                # dd_nwp_agreement advisory, not blended into the DD tier. When
                # the active track is DD this is a no-op.
                graded_risk = resolved.risk_level
                floor_controls = resolved.floor_controls

                active_method_id = resolved.selected_method_id
                active_methods.append(active_method_id)
                active_risk_idx = _RISK_ORDER.index(active.risk_level)
                if (
                    active_risk_idx >= _RISK_ORDER.index(min_risk)
                    and not _is_below_cruise(
                        active.top_ft,
                        cruise_ft,
                        top_clearance_ft,
                    )
                ):
                    active_path_risks.append(
                        (active.risk_level, active_method_id)
                    )
                native_nwp_floor = resolved.method_id == "nwp_with_dd_floor"
                method_id = resolved.method_id

                risk_idx = _RISK_ORDER.index(graded_risk)
                if risk_idx < _RISK_ORDER.index(min_risk):
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
                check_top_ft = _finite_altitude(active.top_ft)
                if floor_controls:
                    thermo_top = _finite_altitude(thermo_conv.top_ft)
                    if check_top_ft is None or (
                        thermo_top is not None and thermo_top > check_top_ft
                    ):
                        check_top_ft = thermo_top
                if _is_below_cruise(
                    check_top_ft,
                    cruise_ft,
                    top_clearance_ft,
                ):
                    below_cruise_count += 1
                    continue

                affected.add(point_index)
                qualifying_risks.append(
                    (graded_risk, active_method_id, native_nwp_floor)
                )
                if risk_idx >= _MOD_IDX:
                    affected_mod.add(point_index)

                if active.cover_pct is not None:
                    max_cover_pct = max(max_cover_pct or 0, active.cover_pct)

                source = resolved.source
                assert source is not None
                lower_altitude_ft, upper_altitude_ft = _evidence_altitude_bounds(
                    source.base_ft,
                    source.top_ft,
                )
                samples.append(
                    EvidenceSample(
                        point_index=point_index,
                        severity=(
                            AdvisoryStatus.RED
                            if graded_risk
                            in (ConvectiveRisk.HIGH, ConvectiveRisk.EXTREME)
                            else AdvisoryStatus.AMBER
                        ),
                        reason_code=(
                            "convective_dd_floor"
                            if floor_controls
                            else "convective_active"
                        ),
                        metric_id=(
                            "nwp_convective_risk"
                            if method_id == "nwp"
                            else "convective_risk"
                        ),
                        method_id=method_id,
                        lower_altitude_ft=lower_altitude_ft,
                        upper_altitude_ft=upper_altitude_ft,
                    )
                )

            summary = summarize_evidence(
                route_points=ordered_analyses,
                total_distance_nm=ctx.total_distance_nm,
                evaluated_point_indices=evaluated,
                complete_point_indices=complete,
                affected_point_indices=affected,
                evidence_samples=samples,
                moderate_point_indices=affected_mod,
            )

            loc = ctx.locale
            cover_suffix = _coverage_suffix(max_cover_pct)
            status, worst_risk = _grade_risks(
                [risk for risk, _, _ in qualifying_risks],
                summary.total_points,
                affected_pct_amber,
                affected_pct_red,
            )
            if summary.total_points == 0:
                detail = adv_t("no_data", loc)
            elif summary.affected_points == 0:
                if below_cruise_count > 0:
                    detail = adv_t("convective.below_cruise", loc, count=below_cruise_count)
                else:
                    detail = adv_t("convective.none", loc)
            else:
                # Colour grade is unchanged (#300 is display-only): HIGH/EXTREME
                # anywhere → RED; otherwise threshold on the LOW-floor extent,
                # capped at AMBER when the peak is only LOW.
                # Headline anchors on the MODERATE+ extent + named peak, so the
                # severity word (peak) and the coverage (MODERATE+ extent) are
                # never conflated. When nothing reaches MODERATE (LOW-only floor),
                # fall back to "primed, not firing" so favorable-but-quiet CAPE
                # doesn't masquerade as active convection (#300).
                if summary.affected_mod_points > 0:
                    detail = adv_t(
                        "convective.risk_over_mod", loc,
                        extent=summary.format_mod_extent(),
                        peak=worst_risk.value.upper(),
                    ) + cover_suffix
                else:
                    # Calibrated for the default LOW floor (min_risk=2): "primed,
                    # not firing" fits a LOW peak. Under a non-default min_risk=1
                    # a MARGINAL-only route also lands here and the wording is a
                    # slight overstatement — accepted (rare, non-default; #302
                    # review).
                    detail = adv_t(
                        "convective.favorability",
                        loc,
                        extent=summary.format_extent(),
                    ) + cover_suffix

            cross_check: str | None = None
            if xcheck_dirs:
                dominant = max(xcheck_dirs, key=lambda d: len(xcheck_dirs[d]))
                # Extent reflects only the dominant direction's points, not the
                # combined fire count — otherwise a mix of both directions would
                # overstate the extent of the direction we're describing.
                xcheck_summary = summarize_evidence(
                    route_points=ordered_analyses,
                    total_distance_nm=ctx.total_distance_nm,
                    evaluated_point_indices=xcheck_evaluated,
                    complete_point_indices=xcheck_evaluated,
                    affected_point_indices=xcheck_dirs[dominant],
                    evidence_samples=(),
                )
                xc_ext = xcheck_summary.format_extent()
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

            status_without_floor, _ = _grade_risks(
                [risk for risk, _ in active_path_risks],
                summary.total_points,
                affected_pct_amber,
                affected_pct_red,
            )
            if (
                any(native_nwp_floor for _, _, native_nwp_floor in qualifying_risks)
                and _STATUS_ORDER[status_without_floor] < _STATUS_ORDER[status]
            ):
                primary_method_id = "nwp_with_dd_floor"
            elif active_path_risks:
                worst_active_risk = max(
                    (risk for risk, _ in active_path_risks),
                    key=_RISK_ORDER.index,
                )
                primary_method_id = next(
                    method
                    for risk, method in active_path_risks
                    if risk == worst_active_risk
                )
            else:
                primary_method_id = active_methods[0] if active_methods else None
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
                    primary_method_id=primary_method_id,
                    cross_check=cross_check,
                )
            )

        return RouteAdvisoryResult.from_per_model("convective", per_model, params)
