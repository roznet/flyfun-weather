"""VMC cruise advisory — can maintain VMC at cruise altitude."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    EvidenceSample,
    FlaggedCell,
    driving_method_id,
    format_extent,
    summarize_evidence,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
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
            ovc_count = 0
            # One evidence sample per route point (#393) — grade counts and the
            # highlight geometry both derive from this single list, so the BKN/OVC
            # verdict and the ribbon cannot drift. ``ovc_count`` is the one
            # sub-count the shared summary can't infer (the OVC-only red
            # threshold), tracked alongside.
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
                    severity = HighlightSeverity.AMBER
                else:
                    severity = HighlightSeverity.GREEN

                region = None
                if severity in (HighlightSeverity.AMBER, HighlightSeverity.RED):
                    region = FlaggedCell(
                        kind="cruise_imc",
                        severity=severity,
                        base_ft=int(env_base) if env_base is not None else None,
                        top_ft=int(env_top) if env_top is not None else None,
                        metric_id="cloud_cover",
                        # The cloud method that actually produced these layers —
                        # "nwp" / "nwp_synthesized" / "dd" under fallback (#408).
                        method_id=sounding.cloud_method_effective,
                    )
                samples.append(EvidenceSample(
                    distance_nm=dist, assessed=True, severity=severity, region=region,
                ))

            summary = summarize_evidence(samples, ctx.total_distance_nm)
            total = summary.assessed
            affected = summary.affected  # bkn + ovc
            # The OVC message names a narrower population than the grade's
            # bkn+ovc union, so it quotes that population's own geometry rather
            # than a scaled share of the union's (#571 D1).
            ovc_extent = summary.extent_of(
                lambda s: s.severity == HighlightSeverity.RED
            )
            loc = ctx.locale
            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            else:
                ovc_pct = 100 * ovc_count / total

                if ovc_pct >= ovc_pct_red:
                    status = AdvisoryStatus.RED
                    detail = adv_t("vmc_cruise.ovc", loc, extent=format_extent(ovc_extent))
                elif 100 * affected / total >= bkn_pct_amber:
                    status = AdvisoryStatus.AMBER
                    detail = adv_t("vmc_cruise.imc", loc, extent=format_extent(summary.extent))
                elif affected > 0:
                    status = AdvisoryStatus.GREEN
                    detail = adv_t("vmc_cruise.mostly_clear", loc, extent=format_extent(summary.extent))
                else:
                    status = AdvisoryStatus.GREEN
                    detail = adv_t("vmc_cruise.clear", loc)

            # Coverage tolerance (#391): a clear verdict on a sounding subset too
            # small to represent the route becomes UNAVAILABLE — a clear subset
            # does not establish the unassessed remainder is clear. A flagged
            # (AMBER/RED) verdict is never downgraded.
            if status == AdvisoryStatus.GREEN and summary.below_coverage:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)

            # Attach highlights only when the model has data (total > 0); an
            # all-UNAVAILABLE model gets no scrim/ribbon.
            highlights = summary.highlights if total > 0 else None

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
                affected_nm=summary.affected_nm,
                highlights=highlights,
                primary_method_id=driving_method_id(highlights, status),
            ))

        return RouteAdvisoryResult.from_per_model("vmc_cruise", per_model, params)
