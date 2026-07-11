"""VMC cruise advisory — can maintain VMC at cruise altitude."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    FlaggedCell,
    build_regions,
    build_ribbon,
    format_extent,
    ribbon_peak,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryHighlights,
    AdvisoryParameterDef,
    AdvisoryStatus,
    CloudCoverage,
    HighlightSeverity,
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
            timing_class="scan",
            altitude_dependent=True,
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
            # Per-point highlight geometry (#373). ribbon_points carries every
            # route point (incl. no-sounding → UNAVAILABLE); region_cells carries
            # a FlaggedCell only for BKN/OVC points, None otherwise.
            ribbon_points: list[tuple[float, HighlightSeverity]] = []
            region_cells: list[tuple[float, FlaggedCell | None]] = []

            for rpa in ctx.analyses:
                dist = rpa.distance_from_origin_nm or 0.0
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    ribbon_points.append((dist, HighlightSeverity.UNAVAILABLE))
                    region_cells.append((dist, None))
                    continue
                total += 1

                # Check cloud layers at cruise altitude, tracking the envelope
                # (min base / max top) of layers that contain cruise for the
                # scrim cutout.
                worst_coverage = None
                env_base: float | None = None
                env_top: float | None = None
                for cl in sounding.cloud_layers:
                    if cl.base_ft <= cruise <= cl.top_ft:
                        env_base = cl.base_ft if env_base is None else min(env_base, cl.base_ft)
                        env_top = cl.top_ft if env_top is None else max(env_top, cl.top_ft)
                        if worst_coverage is None:
                            worst_coverage = cl.coverage
                        elif cl.coverage == CloudCoverage.OVC:
                            worst_coverage = CloudCoverage.OVC
                        elif cl.coverage == CloudCoverage.BKN and worst_coverage != CloudCoverage.OVC:
                            worst_coverage = CloudCoverage.BKN

                if worst_coverage == CloudCoverage.OVC:
                    ovc_count += 1
                    severity = HighlightSeverity.RED
                elif worst_coverage == CloudCoverage.BKN:
                    bkn_count += 1
                    severity = HighlightSeverity.AMBER
                else:
                    severity = HighlightSeverity.GREEN

                ribbon_points.append((dist, severity))
                if severity in (HighlightSeverity.AMBER, HighlightSeverity.RED):
                    region_cells.append((dist, FlaggedCell(
                        kind="cruise_imc",
                        severity=severity,
                        base_ft=int(env_base) if env_base is not None else None,
                        top_ft=int(env_top) if env_top is not None else None,
                    )))
                else:
                    region_cells.append((dist, None))

            affected = bkn_count + ovc_count
            loc = ctx.locale
            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            else:
                ovc_pct = 100 * ovc_count / total

                if ovc_pct >= ovc_pct_red:
                    status = AdvisoryStatus.RED
                    detail = adv_t("vmc_cruise.ovc", loc, extent=format_extent(ovc_count, total, ctx.total_distance_nm))
                elif 100 * affected / total >= bkn_pct_amber:
                    status = AdvisoryStatus.AMBER
                    detail = adv_t("vmc_cruise.imc", loc, extent=format_extent(affected, total, ctx.total_distance_nm))
                elif affected > 0:
                    status = AdvisoryStatus.GREEN
                    detail = adv_t("vmc_cruise.mostly_clear", loc, extent=format_extent(affected, total, ctx.total_distance_nm))
                else:
                    status = AdvisoryStatus.GREEN
                    detail = adv_t("vmc_cruise.clear", loc)

            # Build highlights only when the model has data (total > 0); an
            # all-UNAVAILABLE model gets no scrim/ribbon (highlights=None).
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

        return RouteAdvisoryResult.from_per_model("vmc_cruise", per_model, params)
