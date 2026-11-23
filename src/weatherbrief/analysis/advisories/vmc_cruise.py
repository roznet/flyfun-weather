"""VMC cruise advisory — can maintain VMC at cruise altitude."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._flight_path import (
    cell_phases_in_cloud,
    flown_band,
    in_cloud_on_path,
    in_cloud_sentence,
    path_is_live,
    path_phases,
    phase_list,
    with_path_shadow,
)
from weatherbrief.analysis.advisories._helpers import (
    EXTENT_MIN_NM,
    extent_min_nm_param,
    EvidenceSample,
    FlaggedCell,
    driving_method_id,
    format_extent,
    grade_extent,
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
                "Checks cloud layers and NWP cloud cover at cruise altitude, "
                "and on the climb and descent when the flight path is live. "
                "BKN or OVC coverage where the aircraft flies means IMC conditions."
            ),
            category="cloud",
            timing_class="scan",
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="extent_pct_amber",
                    label="% of route in cloud (amber)",
                    description="Route percentage with BKN where flown for amber",
                    type="percent",
                    unit="%",
                    default=25,
                    min=5,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="extent_pct_red",
                    label="% of route overcast (red)",
                    description="Route percentage with OVC where flown for red",
                    type="percent",
                    unit="%",
                    default=50,
                    min=10,
                    max=100,
                    step=5,
                ),
                extent_min_nm_param(),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        return with_path_shadow(_evaluate, ctx, params)


def _evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
    extent_pct_amber = params.get("extent_pct_amber", 25)
    extent_pct_red = params.get("extent_pct_red", 50)
    extent_min_nm = params.get("extent_min_nm", EXTENT_MIN_NM)
    cruise = ctx.cruise_altitude_ft
    live = path_is_live(ctx)

    per_model: list[ModelAdvisoryResult] = []

    for model in ctx.models:
        ovc_count = 0
        # One evidence sample per route point (#393) — grade counts and the
        # highlight geometry both derive from this single list, so the BKN/OVC
        # verdict and the ribbon cannot drift. ``ovc_count`` is the OVC-only
        # sub-population's point count — the RED threshold grades on that
        # population's extent (``ovc_extent``, reduced from these same
        # samples) and the result publishes the pair as the higher-threshold
        # field, so the printed sentence and the structured field agree.
        samples: list[EvidenceSample] = []
        # Live path (#1011): the phases each flagged cell flies in cloud,
        # per population, so the text names them instead of "at cruise".
        phases_any: set[str] = set()
        phases_ovc: set[str] = set()

        for idx, rpa in enumerate(ctx.analyses):
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
            # Flight path (#1005): a climb / descent cell is graded on the
            # band it flies through, not at cruise.
            band_lo, band_hi = flown_band(ctx, idx) or (cruise, cruise)
            for cl in sounding.cloud_layers:
                if cl.base_ft <= band_hi and cl.top_ft >= band_lo:
                    env_base = cl.base_ft if env_base is None else min(env_base, cl.base_ft)
                    env_top = cl.top_ft if env_top is None else max(env_top, cl.top_ft)
                    if worst_coverage is None:
                        worst_coverage = cl.coverage
                    elif cl.coverage == CloudCoverage.OVC:
                        worst_coverage = CloudCoverage.OVC
                    elif cl.coverage == CloudCoverage.BKN and worst_coverage != CloudCoverage.OVC:
                        worst_coverage = CloudCoverage.BKN

            if live and worst_coverage in (CloudCoverage.BKN, CloudCoverage.OVC):
                phases_any |= cell_phases_in_cloud(ctx, idx, sounding.cloud_layers)
                if worst_coverage == CloudCoverage.OVC:
                    phases_ovc |= cell_phases_in_cloud(
                        ctx, idx, sounding.cloud_layers,
                        coverages=(CloudCoverage.OVC,),
                    )

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

        summary = summarize_evidence(
            samples, ctx.total_distance_nm,
            speed_kt=ctx.cruise_groundspeed_kt,
        )
        total = summary.assessed
        affected = summary.affected  # bkn + ovc
        # The OVC message names a narrower population than the grade's
        # bkn+ovc union, so it quotes that population's own geometry rather
        # than a scaled share of the union's (#571 D1).
        ovc_extent = summary.extent_of(
            lambda s: s.severity == HighlightSeverity.RED
        )
        loc = ctx.locale
        ovc_only = False
        if total == 0:
            status = AdvisoryStatus.UNAVAILABLE
            detail = adv_t("no_data", loc)
        else:
            if grade_extent(
                ovc_extent, amber_pct=extent_pct_red, min_nm=extent_min_nm,
            ) != AdvisoryStatus.GREEN:
                status = AdvisoryStatus.RED
                ovc_only = True
                detail = _text(ctx, "ovc", phases_ovc, extent=format_extent(ovc_extent))
            elif grade_extent(
                summary.extent, amber_pct=extent_pct_amber,
                min_nm=extent_min_nm,
            ) != AdvisoryStatus.GREEN:
                status = AdvisoryStatus.AMBER
                detail = _text(ctx, "imc", phases_any, extent=format_extent(summary.extent))
            elif affected > 0:
                status = AdvisoryStatus.GREEN
                detail = _text(ctx, "mostly_clear", phases_any, extent=format_extent(summary.extent))
            else:
                status = AdvisoryStatus.GREEN
                detail = _text(ctx, "clear", path_phases(ctx) if live else set())

        # Coverage tolerance (#391): a clear verdict on a sounding subset too
        # small to represent the route becomes UNAVAILABLE — a clear subset
        # does not establish the unassessed remainder is clear. A flagged
        # (AMBER/RED) verdict is never downgraded.
        if status == AdvisoryStatus.GREEN and summary.below_coverage:
            status = AdvisoryStatus.UNAVAILABLE
            detail = adv_t("no_data", loc)
        elif total > 0:
            # The breakdown counts the headline's own population (OVC only
            # under the OVC headline), so "this includes" is always true.
            detail += in_cloud_sentence(ctx, in_cloud_on_path(
                ctx, lambda i, m=model, o=ovc_only: _layers(ctx, i, m, ovc_only=o),
            ), loc)

        # Attach highlights only when the model has data (total > 0); an
        # all-UNAVAILABLE model gets no scrim/ribbon.
        highlights = summary.highlights if total > 0 else None

        per_model.append(ModelAdvisoryResult.build(
            model=model, status=status, detail=detail,
            affected=affected, total=total,
            total_distance_nm=ctx.total_distance_nm,
            extent=summary.extent,
            # Same rule as turbulence (#571 review): ``affected_nm`` is the
            # BKN+OVC union the grade counts, and the OVC-only extent the RED
            # branch grades and prints rides the higher-threshold field, so
            # the published object and the sentence agree.
            affected_mod=ovc_count,
            extent_mod=ovc_extent,
            highlights=highlights,
            primary_method_id=driving_method_id(highlights, status),
        ))

    return RouteAdvisoryResult.from_per_model("vmc_cruise", per_model, params)


def _layers(ctx: RouteContext, idx: int, model: str, *, ovc_only: bool = False):
    sounding = ctx.analyses[idx].sounding.get(model)
    if sounding is None:
        return None
    return _only_ovc(sounding.cloud_layers) if ovc_only else sounding.cloud_layers


def _only_ovc(layers):
    return [cl for cl in layers if cl.coverage == CloudCoverage.OVC]


def _text(ctx: RouteContext, key: str, phases: set[str], **params) -> str:
    """The headline: "at cruise" in flat mode, the phases with cloud on a live path (#1011)."""
    if not phases:
        return adv_t(f"vmc_cruise.{key}", ctx.locale, **params)
    return adv_t(
        f"vmc_cruise.path.{key}", ctx.locale,
        phases=phase_list(phases, ctx.locale), **params,
    )
