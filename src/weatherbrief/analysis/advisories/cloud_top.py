"""Cloud top advisory — can fly above cloud tops."""

from __future__ import annotations

from dataclasses import replace

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
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)


@register
class CloudTopEvaluator:
    """Evaluates whether the aircraft can fly above cloud tops near cruise altitude."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="cloud_top",
            name="Cloud Tops",
            short_description="Can fly above cloud tops",
            description=(
                "Checks cloud tops for layers near cruise altitude "
                "(base within clearance margin of cruise). Layers well "
                "above cruise are ignored — the pilot flies clear below "
                "them. Cloud tops near or above the flight ceiling means "
                "the pilot cannot get on top if needed."
            ),
            category="cloud",
            timing_class="scan",
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="margin_ft",
                    label="Margin above tops",
                    description="Required clearance above cloud tops",
                    type="altitude",
                    unit="ft",
                    default=1000,
                    min=500,
                    max=3000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="pct_amber",
                    label="Route % (amber)",
                    description="Route percentage with tops above ceiling for amber",
                    type="percent",
                    unit="%",
                    default=25,
                    min=5,
                    max=80,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        margin_ft = params.get("margin_ft", 1000)
        pct_amber = params.get("pct_amber", 25)
        ceiling = ctx.flight_ceiling_ft
        cruise = ctx.cruise_altitude_ft
        ordered_analyses = sorted(
            ctx.analyses,
            key=lambda rpa: (rpa.distance_from_origin_nm, rpa.point_index),
        )

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            evaluated: set[int] = set()
            complete: set[int] = set()
            affected: set[int] = set()
            samples: list[EvidenceSample] = []
            methods: list[str] = []
            max_top: float | None = None

            for rpa in ordered_analyses:
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

                # Only consider layers the pilot would actually encounter:
                # base must be within margin of cruise altitude (close enough
                # to enter) AND below flight ceiling.  Layers well above
                # cruise are irrelevant — the pilot flies clear below them.
                for layer in sounding.cloud_layers:
                    if (
                        layer.base_ft > cruise + margin_ft
                        or layer.base_ft > ceiling
                    ):
                        continue
                    if max_top is None or layer.top_ft > max_top:
                        max_top = layer.top_ft
                    if layer.top_ft + margin_ft <= ceiling:
                        continue
                    affected.add(rpa.point_index)
                    samples.append(
                        EvidenceSample(
                            point_index=rpa.point_index,
                            severity=AdvisoryStatus.AMBER,
                            reason_code="cloud_top_exceeds_ceiling",
                            metric_id="cloud_coverage",
                            method_id=method_id,
                            lower_altitude_ft=round(layer.base_ft),
                            upper_altitude_ft=round(layer.top_ft),
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

            loc = ctx.locale
            if summary.total_points == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            elif summary.affected_points == 0:
                status = AdvisoryStatus.GREEN
                if max_top is not None:
                    detail = adv_t("cloud_top.reachable", loc, top=f"{max_top:.0f}", ceiling=ceiling)
                else:
                    detail = adv_t("cloud_top.no_layers", loc)
            else:
                if summary.affected_pct >= 60:
                    status = AdvisoryStatus.RED
                elif summary.affected_pct >= pct_amber:
                    status = AdvisoryStatus.AMBER
                else:
                    status = AdvisoryStatus.GREEN
                detail = adv_t(
                    "cloud_top.above_ceiling",
                    loc,
                    extent=summary.format_extent(),
                    top=f"{max_top:.0f}",
                )

            summary = replace(
                summary,
                evidence_regions=[
                    region.model_copy(update={"severity": status})
                    for region in summary.evidence_regions
                ],
            )
            primary_method_id = next(
                (sample.method_id for sample in samples if sample.method_id is not None),
                methods[0] if methods else None,
            )
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
                )
            )

        return RouteAdvisoryResult.from_per_model("cloud_top", per_model, params)
