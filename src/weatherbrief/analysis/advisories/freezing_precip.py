"""Freezing precipitation (FZRA/PL) advisory evaluator.

Freezing rain is the most severe icing hazard a GA aircraft can encounter:
snow melts in a warm nose aloft and falls as supercooled rain into sub-zero
air below, accreting clear ice at rates beyond any deice capability. Crucially
it happens *below* cloud — in air every in-cloud icing method (and the
cross-section cloud bands) shows as clear — and it is the one icing scenario
where the standard escape (descend into warmer/clearer air) is wrong.

Two tiers per route point per model:
- **Active** (RED): the precipitation assessment reports a freezing-rain or
  ice-pellet surface phase (warm-nose profile WITH active precipitation).
  Ice pellets are graded the same — they mean the refreeze completed before
  the ground, i.e. freezing rain exists in the layer above.
- **Model type** (AMBER, #411 step 2, §82): the model's own precipitation
  type (ECMWF ``ptype``) says freezing rain, freezing drizzle or ice pellets
  with at least 0.01 mm/h falling. AMBER at any point (no coverage threshold,
  like the active tier) until the winter experiment supports RED. It is the
  only signal that sees freezing drizzle, which has no warm nose.
- **Primed** (AMBER): the profile has the freezing-rain shape (sub-zero
  surface wet-bulb under a warm nose, via ``detect_warm_nose``) but no
  precipitation is falling at that hour — precip onset or a small timing
  shift turns it active.

Across models (#411, meteorology-decisions §81): under the default MAJORITY
aggregation one RED model floors the card at AMBER and two or more make it RED
(``escalate_on_red`` in ``RouteAdvisoryResult.from_per_model``). It only raises
the grade, never lowers it, and WORST is unaffected. A model AMBER on its own
precipitation type also floors the card at AMBER (``floors_aggregate``):
otherwise ECMWF, the only model with a type, would always be outvoted.
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    EXTENT_MIN_NM,
    extent_min_nm_param,
    EvidenceSample,
    FlaggedCell,
    format_extent,
    grade_extent,
    summarize_evidence,
    terrain_at_distance,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.analysis.sounding.precipitation import detect_warm_nose
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    HighlightSeverity,
    ModelAdvisoryResult,
    PrecipPhase,
    RouteAdvisoryResult,
)

_ACTIVE_PHASES = (PrecipPhase.FREEZING_RAIN, PrecipPhase.ICE_PELLETS)

# Fallback depth of the freezing-precip column cutout when the warm-nose top
# doesn't resolve at a flagged point: a fixed shallow AGL band (#375). The
# hazard column is surface → warm-nose top; without the nose top we still show
# where along the route it is, honestly shallow rather than full-column.
_FALLBACK_COLUMN_AGL_FT = 3000


@register
class FreezingPrecipEvaluator:
    """Evaluates freezing rain / ice pellet exposure along the route."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="freezing_precip",
            name="Freezing Precipitation",
            short_description="Freezing rain / ice pellets along the route",
            description=(
                "Detects freezing rain and ice pellets: a warm layer aloft over a "
                "sub-zero surface layer turns falling rain into supercooled drops "
                "that freeze on the airframe as clear ice. This happens BELOW "
                "cloud — where the in-cloud icing methods and cloud bands show "
                "nothing — and it is the one icing scenario where descending does "
                "NOT escape. A model is red when it shows active freezing rain or "
                "ice pellets at any point (deliberately no coverage threshold: one "
                "transit exceeds all icing certification, FIKI included; ice "
                "pellets prove freezing rain in the layer above). Across models, "
                "one model red makes the card at least amber and two or more make "
                "it red, whatever the majority says. Amber when a model's own "
                "precipitation type (ECMWF) forecasts freezing rain, freezing "
                "drizzle or ice pellets with precipitation falling, at any "
                "point; one such model is enough. Amber when a "
                "freezing-rain-shaped profile exists without active precipitation "
                "yet (primed — onset or a timing shift turns it active) over "
                "enough of the route. Unavailable (not green) on packs without "
                "precipitation data."
            ),
            category="icing",
            timing_class="scan",
            parameters=[
                AdvisoryParameterDef(
                    key="extent_pct_amber",
                    label="% of route with a primed profile (amber)",
                    description=(
                        "Percent of route with a freezing-rain-shaped profile "
                        "(no active precip) that triggers amber"
                    ),
                    type="percent",
                    unit="%",
                    default=5,
                    min=0,
                    max=50,
                    step=5,
                ),
                extent_min_nm_param(),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        extent_pct_amber = params.get("extent_pct_amber", 5)
        extent_min_nm = params.get("extent_min_nm", EXTENT_MIN_NM)

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            active_pts = 0
            primed_pts = 0
            ptype_pts = 0
            ptype_kinds: list[str] = []
            loc = ctx.locale

            # One evidence sample per route point (#393): ribbon red = active
            # FZRA/PL surface phase, amber = primed profile (warm nose, no active
            # precip); the cutout is the hazard column, surface → warm-nose top.
            # active/primed sub-counts are tracked alongside for the tiered grade.
            samples: list[EvidenceSample] = []

            for rpa in ctx.analyses:
                dist = rpa.distance_from_origin_nm or 0.0
                sounding = rpa.sounding.get(model)
                precip = sounding.precipitation if sounding is not None else None
                # "Assessed" for freezing precip means the point carries a signal
                # source: an active-precip phase (``precipitation``) OR a
                # thermodynamic profile to detect a warm nose (``derived_levels``).
                # A sounding with neither cannot be assessed and must NOT enter
                # the denominator — otherwise unassessable points dilute the
                # primed-profile percentage below its threshold (#391).
                point_source = precip is not None or bool(
                    sounding.derived_levels if sounding is not None else None
                )
                if sounding is None or not point_source:
                    samples.append(EvidenceSample(
                        distance_nm=dist, assessed=False,
                        severity=HighlightSeverity.UNAVAILABLE,
                    ))
                    continue

                active = False
                if precip is not None:
                    if (
                        precip.surface_phase in _ACTIVE_PHASES
                        or precip.freezing_rain_risk
                    ):
                        active_pts += 1
                        active = True

                # The model's own precipitation type, gated on precip (#411).
                ptype_kind = (
                    precip.model_freezing_type if precip is not None else None
                )

                # Warm-nose profile shape (also the cutout's top at active
                # points). Primed = the shape without active precip.
                primed = False
                nose_top: float | None = None
                nose_base: float | None = None
                if sounding.derived_levels:
                    fz_risk, nose_base, nose_top, ice_pellets = detect_warm_nose(
                        sounding.derived_levels
                    )
                    if not active and not ptype_kind and (fz_risk or ice_pellets):
                        primed_pts += 1
                        primed = True

                ptype_flag = bool(ptype_kind) and not active
                if ptype_flag:
                    ptype_pts += 1
                    if ptype_kind not in ptype_kinds:
                        ptype_kinds.append(ptype_kind)

                reason: str | None = None
                if active:
                    severity = HighlightSeverity.RED
                elif ptype_flag:
                    severity = HighlightSeverity.AMBER
                    reason = "model_ptype"
                    nose_top = _ptype_zone_top(ptype_kind, nose_base, sounding)
                elif primed:
                    severity = HighlightSeverity.AMBER
                else:
                    # Includes sounding-without-precip-data points: the model-
                    # level no-signal case is already UNAVAILABLE overall.
                    severity = HighlightSeverity.GREEN

                region = None
                if severity in (HighlightSeverity.AMBER, HighlightSeverity.RED):
                    if nose_top is None:
                        terrain_ft = terrain_at_distance(ctx.elevation, dist) or 0.0
                        nose_top = terrain_ft + _FALLBACK_COLUMN_AGL_FT
                    # base_ft=None renders down to the surface — exactly the
                    # below-cloud hazard column.
                    region = FlaggedCell(
                        kind="freezing_precip_column",
                        severity=severity,
                        base_ft=None,
                        top_ft=int(nose_top),
                        reason_code=reason,
                        metric_id="precipitation",
                    )
                samples.append(EvidenceSample(
                    distance_nm=dist, assessed=True, severity=severity, region=region,
                ))

            summary = summarize_evidence(
                samples, ctx.total_distance_nm,
                speed_kt=ctx.cruise_groundspeed_kt,
            )
            total = summary.assessed
            # Active (RED) and primed (AMBER) are two populations inside one
            # grade; each sentence quotes its own geometry (#571 D1).
            active_ext = summary.extent_of(
                lambda s: s.severity == HighlightSeverity.RED
            )
            # Model-type and primed points are both AMBER on the ribbon; the
            # sentences quote each population's own geometry, told apart by
            # the cell's reason code.
            def _is_ptype(s) -> bool:
                return s.region is not None and s.region.reason_code == "model_ptype"

            ptype_ext = summary.extent_of(
                lambda s: s.severity == HighlightSeverity.AMBER and _is_ptype(s)
            )
            primed_ext = summary.extent_of(
                lambda s: s.severity == HighlightSeverity.AMBER and not _is_ptype(s)
            )

            if total == 0:
                per_model.append(ModelAdvisoryResult.build(
                    model=model, status=AdvisoryStatus.UNAVAILABLE,
                    detail=adv_t("no_data", loc), affected=0, total=0,
                    total_distance_nm=ctx.total_distance_nm,
                ))
                continue

            if active_pts > 0:
                status = AdvisoryStatus.RED
                detail = adv_t(
                    "freezing_precip.active", loc,
                    extent=format_extent(active_ext, locale=loc),
                )
                if ptype_pts:
                    detail += "; " + _ptype_sentence(ptype_kinds, ptype_ext, loc)
                if primed_pts:
                    detail += adv_t(
                        "freezing_precip.primed_suffix", loc,
                        extent=format_extent(primed_ext, locale=loc),
                    )
            elif ptype_pts > 0:
                # Graded AMBER at any point, no coverage threshold (#411,
                # §82): RED only after the winter experiment supports it.
                status = AdvisoryStatus.AMBER
                detail = _ptype_sentence(ptype_kinds, ptype_ext, loc)
                if primed_pts:
                    detail += adv_t(
                        "freezing_precip.primed_suffix", loc,
                        extent=format_extent(primed_ext, locale=loc),
                    )
            elif primed_pts > 0 and grade_extent(
                primed_ext, amber_pct=extent_pct_amber, min_nm=extent_min_nm,
            ) != AdvisoryStatus.GREEN:
                status = AdvisoryStatus.AMBER
                detail = adv_t(
                    "freezing_precip.primed", loc,
                    extent=format_extent(primed_ext, locale=loc),
                )
            else:
                status = AdvisoryStatus.GREEN
                detail = adv_t("freezing_precip.none", loc)

            # Coverage tolerance (#391): a "no signature" verdict from assessable
            # points at too small a share of the route cannot vouch for the rest.
            # The active-FZRA RED and primed AMBER paths above are untouched — a
            # flagged verdict always stands on partial coverage.
            if status == AdvisoryStatus.GREEN and summary.below_coverage:
                per_model.append(ModelAdvisoryResult.build(
                    model=model, status=AdvisoryStatus.UNAVAILABLE,
                    detail=adv_t("no_data", loc), affected=0, total=total,
                    total_distance_nm=ctx.total_distance_nm,
                ))
                continue

            result = ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=active_pts + ptype_pts + primed_pts, total=total,
                total_distance_nm=ctx.total_distance_nm,
                extent=summary.extent,
                highlights=summary.highlights,
            )
            # An AMBER from the model's own type floors the card (§82).
            result.floors_aggregate = (
                status == AdvisoryStatus.AMBER and ptype_pts > 0
            )
            per_model.append(result)

        # One model's freezing rain must not vanish under MAJORITY (#411,
        # meteorology-decisions §81): one RED model floors the card at AMBER,
        # two or more make it RED. Never lowers; WORST is unaffected.
        return RouteAdvisoryResult.from_per_model(
            "freezing_precip", per_model, params,
            escalate_on_red=True, locale=ctx.locale,
        )


def _ptype_sentence(kinds: list[str], extent, locale: str | None) -> str:
    """"Model forecasts freezing drizzle 20nm/400nm (5%)" for the type tier."""
    names = ", ".join(adv_t(f"freezing_precip.ptype.{k}", locale) for k in kinds)
    return adv_t(
        "freezing_precip.ptype", locale, kinds=names,
        extent=format_extent(extent, locale=locale),
    )


def _ptype_zone_top(kind: str | None, nose_base: float | None, sounding) -> float | None:
    """Top of the cutout for a model-type point (#411 step 2, §82).

    Freezing rain and ice pellets fall through the cold layer under the warm
    nose: the zone tops out at the nose BASE when the profile resolves one.
    Freezing drizzle forms in the low deck: the zone tops out at the lowest
    cloud layer's top. Otherwise None, and the caller's existing shallow
    fallback (terrain + 3,000 ft) applies, as for any unresolved column.
    """
    if kind in ("freezing_rain", "ice_pellets"):
        return nose_base
    if kind == "freezing_drizzle":
        layers = list(sounding.cloud_layers or [])
        if layers:
            return min(layers, key=lambda c: c.base_ft).top_ft
    return None
