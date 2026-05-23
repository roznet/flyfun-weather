"""Convective advisory — can fly around convective activity."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import format_extent, pct_above_threshold
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

        for model in ctx.models:
            total = 0
            affected = 0
            has_high = False
            worst_risk = ConvectiveRisk.NONE
            below_cruise_count = 0  # risky points skipped because tops below cruise
            max_cover_pct: float | None = None
            # Details-only DD-vs-model-scheme cross-check tally (never grades).
            xcheck_fired = 0
            xcheck_dirs: dict[str, int] = {}
            xcheck_worst_dd_risk = ConvectiveRisk.NONE

            for rpa in ctx.analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    continue
                total += 1

                # Independent of the grade filters below: compare the chosen
                # thermo risk against the model's own convective scheme.
                xc = convective_cross_check(sounding.convective, sounding.convective_nwp)
                if xc is not None:
                    xcheck_fired += 1
                    xcheck_dirs[xc.direction] = xcheck_dirs.get(xc.direction, 0) + 1
                    if xc.direction == "dd_not_corroborated" and sounding.convective is not None:
                        if _RISK_ORDER.index(sounding.convective.risk_level) > _RISK_ORDER.index(
                            xcheck_worst_dd_risk
                        ):
                            xcheck_worst_dd_risk = sounding.convective.risk_level

                conv = sounding.convective
                if conv is None:
                    continue

                risk_idx = _RISK_ORDER.index(conv.risk_level)
                if risk_idx < _RISK_ORDER.index(min_risk):
                    continue

                # Skip if convective tops are well below cruise altitude
                if (
                    conv.top_ft is not None
                    and conv.top_ft + top_clearance_ft <= cruise_ft
                ):
                    below_cruise_count += 1
                    continue

                affected += 1
                if risk_idx > _RISK_ORDER.index(worst_risk):
                    worst_risk = conv.risk_level

                if conv.risk_level in (ConvectiveRisk.HIGH, ConvectiveRisk.EXTREME):
                    has_high = True

                if conv.cover_pct is not None:
                    max_cover_pct = max(max_cover_pct or 0, conv.cover_pct)

            ext = format_extent(affected, total, ctx.total_distance_nm)
            loc = ctx.locale
            cover_suffix = _coverage_suffix(max_cover_pct)
            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            elif has_high:
                status = AdvisoryStatus.RED
                detail = adv_t("convective.risk_over", loc, risk=worst_risk.value.upper(), extent=ext) + cover_suffix
            elif affected == 0:
                status = AdvisoryStatus.GREEN
                if below_cruise_count > 0:
                    detail = adv_t("convective.below_cruise", loc, count=below_cruise_count)
                else:
                    detail = adv_t("convective.none", loc)
            else:
                status = pct_above_threshold(affected, total, affected_pct_amber, affected_pct_red)
                # LOW risk alone can't escalate to RED — cap at AMBER
                if worst_risk == ConvectiveRisk.LOW and status == AdvisoryStatus.RED:
                    status = AdvisoryStatus.AMBER
                detail = adv_t("convective.risk_over", loc, risk=worst_risk.value.upper(), extent=ext) + cover_suffix

            cross_check: str | None = None
            if xcheck_fired > 0:
                dominant = max(xcheck_dirs, key=lambda d: xcheck_dirs[d])
                xc_ext = format_extent(xcheck_fired, total, ctx.total_distance_nm)
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

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
                cross_check=cross_check,
            ))

        return RouteAdvisoryResult.from_per_model("convective", per_model, params)
