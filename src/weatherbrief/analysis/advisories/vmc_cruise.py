"""VMC cruise advisory — can maintain VMC at cruise altitude."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.evidence import (
    EvidenceSample,
    cloud_method_id,
    summarize_evidence,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
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
            evaluated: set[int] = set()
            complete: set[int] = set()
            affected: set[int] = set()
            bkn_points: set[int] = set()
            ovc_points: set[int] = set()
            samples: list[EvidenceSample] = []
            methods: list[str] = []

            for rpa in ctx.analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    continue
                evaluated.add(rpa.point_index)
                complete.add(rpa.point_index)
                method_id = cloud_method_id(
                    sounding.cloud_method_effective,
                    ctx.cloud_method,
                )
                if method_id is not None:
                    methods.append(method_id)

                # Check cloud layers at cruise altitude
                has_bkn = False
                has_ovc = False
                for layer in sounding.cloud_layers:
                    if not layer.base_ft <= cruise <= layer.top_ft:
                        continue
                    if layer.coverage == CloudCoverage.OVC:
                        has_ovc = True
                        local_severity = AdvisoryStatus.RED
                        reason = "cruise_in_ovc_cloud"
                    elif layer.coverage == CloudCoverage.BKN:
                        has_bkn = True
                        local_severity = AdvisoryStatus.AMBER
                        reason = "cruise_in_bkn_cloud"
                    else:
                        continue
                    affected.add(rpa.point_index)
                    samples.append(
                        EvidenceSample(
                            point_index=rpa.point_index,
                            severity=local_severity,
                            reason_code=reason,
                            metric_id="cloud_coverage",
                            method_id=method_id,
                            lower_altitude_ft=round(layer.base_ft),
                            upper_altitude_ft=round(layer.top_ft),
                        )
                    )

                if has_ovc:
                    ovc_points.add(rpa.point_index)
                elif has_bkn:
                    bkn_points.add(rpa.point_index)

            summary = summarize_evidence(
                route_points=ctx.analyses,
                total_distance_nm=ctx.total_distance_nm,
                evaluated_point_indices=evaluated,
                complete_point_indices=complete,
                affected_point_indices=affected,
                evidence_samples=samples,
            )
            bkn_count = len(bkn_points)
            ovc_count = len(ovc_points)
            loc = ctx.locale
            if summary.total_points == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            else:
                ovc_pct = 100 * ovc_count / summary.total_points

                if ovc_pct >= ovc_pct_red:
                    status = AdvisoryStatus.RED
                    detail = adv_t(
                        "vmc_cruise.ovc",
                        loc,
                        extent=summary.format_extent(),
                    )
                elif summary.affected_pct >= bkn_pct_amber:
                    status = AdvisoryStatus.AMBER
                    detail = adv_t(
                        "vmc_cruise.imc",
                        loc,
                        extent=summary.format_extent(),
                    )
                elif summary.affected_points > 0:
                    status = AdvisoryStatus.GREEN
                    detail = adv_t(
                        "vmc_cruise.mostly_clear",
                        loc,
                        extent=summary.format_extent(),
                    )
                else:
                    status = AdvisoryStatus.GREEN
                    detail = adv_t("vmc_cruise.clear", loc)

            controlling_samples = (
                [sample for sample in samples if sample.severity == AdvisoryStatus.RED]
                if status == AdvisoryStatus.RED
                else samples
            )
            primary_method_id = next(
                (
                    sample.method_id
                    for sample in controlling_samples
                    if sample.method_id is not None
                ),
                methods[0] if methods else None,
            )
            per_model.append(
                summary.build_result(
                    model=model,
                    status=status,
                    detail=detail,
                    unavailable_detail=adv_t("partial_data", loc),
                    primary_method_id=primary_method_id,
                )
            )

        return RouteAdvisoryResult.from_per_model("vmc_cruise", per_model, params)
