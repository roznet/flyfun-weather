"""Cloud top advisory — can fly above cloud tops."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    EvidenceSample,
    FlaggedCell,
    driving_method_id,
    format_extent,
    pct_above_threshold,
    summarize_evidence,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    HighlightSeverity,
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
                    audience="pilot",
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

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            max_top: float | None = None
            # One evidence sample per route point (#393): the grade count
            # (above_ceiling) and the ribbon/scrim both derive from this list.
            # Binary per point: green = toppable or no cloud, amber = can't get on
            # top here; the cutout is the blocking deck.
            samples: list[EvidenceSample] = []

            for rpa in ctx.analyses:
                dist = rpa.distance_from_origin_nm or 0.0
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    samples.append(EvidenceSample(
                        distance_nm=dist, assessed=False,
                        severity=HighlightSeverity.UNAVAILABLE,
                    ))
                    continue

                # Only consider layers the pilot would actually encounter:
                # base must be within margin of cruise altitude (close enough
                # to enter) AND below flight ceiling.  Layers well above
                # cruise are irrelevant — the pilot flies clear below them.
                cruise = ctx.cruise_altitude_ft
                reachable = [
                    cl for cl in sounding.cloud_layers
                    if cl.base_ft <= cruise + margin_ft and cl.base_ft <= ceiling
                ]
                if not reachable:
                    samples.append(EvidenceSample(
                        distance_nm=dist, assessed=True,
                        severity=HighlightSeverity.GREEN,
                    ))
                    continue

                highest_top = max(cl.top_ft for cl in reachable)
                if max_top is None or highest_top > max_top:
                    max_top = highest_top

                if highest_top + margin_ft > ceiling:
                    # The blocking deck: the reachable layers whose tops violate
                    # the ceiling margin.
                    blocking = [
                        cl for cl in reachable if cl.top_ft + margin_ft > ceiling
                    ]
                    samples.append(EvidenceSample(
                        distance_nm=dist, assessed=True,
                        severity=HighlightSeverity.AMBER,
                        region=FlaggedCell(
                            kind="blocking_deck",
                            severity=HighlightSeverity.AMBER,
                            base_ft=int(min(cl.base_ft for cl in blocking)),
                            top_ft=int(max(cl.top_ft for cl in blocking)),
                            metric_id="cloud_cover",
                            # The cloud method behind these layers (#408).
                            method_id=sounding.cloud_method_effective,
                        ),
                    ))
                else:
                    samples.append(EvidenceSample(
                        distance_nm=dist, assessed=True,
                        severity=HighlightSeverity.GREEN,
                    ))

            summary = summarize_evidence(samples, ctx.total_distance_nm)
            total = summary.assessed
            above_ceiling = summary.affected
            loc = ctx.locale
            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            elif above_ceiling == 0:
                status = AdvisoryStatus.GREEN
                if max_top is not None:
                    detail = adv_t("cloud_top.reachable", loc, top=f"{max_top:.0f}", ceiling=ceiling)
                else:
                    detail = adv_t("cloud_top.no_layers", loc)
            else:
                status = pct_above_threshold(above_ceiling, total, pct_amber, red_pct=60)
                ext = format_extent(above_ceiling, total, ctx.total_distance_nm)
                detail = adv_t("cloud_top.above_ceiling", loc, extent=ext, top=f"{max_top:.0f}")

            # Coverage tolerance (#391): a clear verdict on a sounding subset that
            # is too small to represent the route becomes UNAVAILABLE — a clear
            # 2-of-20-points model has not established the other 18 are clear. A
            # flagged verdict is never downgraded (real hazard evidence stands).
            if status == AdvisoryStatus.GREEN and summary.below_coverage:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)

            # Highlights (#375) only when the model has data.
            highlights = summary.highlights if total > 0 else None

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=above_ceiling, total=total,
                total_distance_nm=ctx.total_distance_nm,
                affected_nm=summary.affected_nm,
                highlights=highlights,
                primary_method_id=driving_method_id(highlights, status),
            ))

        return RouteAdvisoryResult.from_per_model("cloud_top", per_model, params)
