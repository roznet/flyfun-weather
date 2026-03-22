"""Convective advisory — can fly around convective activity."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import format_extent, pct_above_threshold
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
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

            for rpa in ctx.analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    continue
                total += 1

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

            ext = format_extent(affected, total, ctx.total_distance_nm)
            loc = ctx.locale
            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            elif has_high:
                status = AdvisoryStatus.RED
                detail = adv_t("convective.risk_over", loc, risk=worst_risk.value.upper(), extent=ext)
            elif affected == 0:
                status = AdvisoryStatus.GREEN
                if below_cruise_count > 0:
                    detail = adv_t("convective.below_cruise", loc, count=below_cruise_count)
                else:
                    detail = adv_t("convective.none", loc)
            else:
                status = pct_above_threshold(affected, total, affected_pct_amber, affected_pct_red)
                detail = adv_t("convective.risk_over", loc, risk=worst_risk.value.upper(), extent=ext)

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("convective", per_model, params)
