"""VMC cruise advisory — can maintain VMC at cruise altitude."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import format_extent, pct_above_threshold
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    CloudCoverage,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)


@register
class VMCCruiseEvaluator:
    """Evaluates whether VMC can be maintained at cruise altitude."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="vmc_cruise",
            name="VMC at Cruise",
            short_description="Can maintain VMC at cruise altitude",
            description=(
                "Checks cloud layers and NWP cloud cover at cruise altitude. "
                "BKN or OVC coverage at cruise means IMC conditions."
            ),
            category="cloud",
            parameters=[
                AdvisoryParameterDef(
                    key="bkn_pct_amber",
                    label="BKN % (amber)",
                    description="Route percentage with BKN at cruise for amber",
                    type="percent",
                    unit="%",
                    default=25,
                    min=5,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="ovc_pct_red",
                    label="OVC % (red)",
                    description="Route percentage with OVC at cruise for red",
                    type="percent",
                    unit="%",
                    default=50,
                    min=10,
                    max=100,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        bkn_pct_amber = params.get("bkn_pct_amber", 25)
        ovc_pct_red = params.get("ovc_pct_red", 50)
        cruise = ctx.cruise_altitude_ft

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            total = 0
            bkn_count = 0
            ovc_count = 0

            for rpa in ctx.analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    continue
                total += 1

                # Check cloud layers at cruise altitude
                worst_coverage = None
                for cl in sounding.cloud_layers:
                    if cl.base_ft <= cruise <= cl.top_ft:
                        if worst_coverage is None:
                            worst_coverage = cl.coverage
                        elif cl.coverage == CloudCoverage.OVC:
                            worst_coverage = CloudCoverage.OVC
                        elif cl.coverage == CloudCoverage.BKN and worst_coverage != CloudCoverage.OVC:
                            worst_coverage = CloudCoverage.BKN

                if worst_coverage == CloudCoverage.OVC:
                    ovc_count += 1
                elif worst_coverage == CloudCoverage.BKN:
                    bkn_count += 1

            affected = bkn_count + ovc_count
            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = "No data"
            else:
                ovc_pct = 100 * ovc_count / total

                if ovc_pct >= ovc_pct_red:
                    status = AdvisoryStatus.RED
                    detail = f"OVC at cruise over {format_extent(ovc_count, total, ctx.total_distance_nm)}"
                elif 100 * affected / total >= bkn_pct_amber:
                    status = AdvisoryStatus.AMBER
                    detail = f"IMC at cruise over {format_extent(affected, total, ctx.total_distance_nm)}"
                elif affected > 0:
                    status = AdvisoryStatus.GREEN
                    detail = f"Mostly clear at cruise, IMC over {format_extent(affected, total, ctx.total_distance_nm)}"
                else:
                    status = AdvisoryStatus.GREEN
                    detail = "Clear at cruise altitude"

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("vmc_cruise", per_model, params)
