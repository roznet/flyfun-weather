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
- **Primed** (AMBER): the profile has the freezing-rain shape (sub-zero
  surface wet-bulb under a warm nose, via ``detect_warm_nose``) but no
  precipitation is falling at that hour — precip onset or a small timing
  shift turns it active.
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.evidence import (
    EvidenceSample,
    summarize_evidence,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.analysis.sounding.precipitation import detect_warm_nose
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ModelAdvisoryResult,
    PrecipPhase,
    RouteAdvisoryResult,
    SoundingAnalysis,
)

_ACTIVE_PHASES = (PrecipPhase.FREEZING_RAIN, PrecipPhase.ICE_PELLETS)
_METHOD_ID = "nwp_precipitation_profile"


def _warm_nose_profile_is_usable(sounding: SoundingAnalysis) -> bool:
    """Return whether the profile can support ``detect_warm_nose``."""
    return sum(
        1
        for level in sounding.derived_levels
        if level.wet_bulb_c is not None and level.altitude_ft is not None
    ) >= 3


def _complete_bounds(
    lower: float | None,
    upper: float | None,
) -> tuple[int, int] | None:
    """Return one complete valid altitude pair, never mixed endpoints."""
    if lower is None or upper is None or lower > upper:
        return None
    return round(lower), round(upper)


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
                "NOT escape. Red when any model shows active freezing rain or ice "
                "pellets at any point (deliberately no coverage threshold: one "
                "transit exceeds all icing certification, FIKI included; ice "
                "pellets prove freezing rain in the layer above). Amber when a "
                "freezing-rain-shaped profile exists without active precipitation "
                "yet (primed — onset or a timing shift turns it active) over "
                "enough of the route. Unavailable (not green) on packs without "
                "precipitation data."
            ),
            category="icing",
            timing_class="scan",
            parameters=[
                AdvisoryParameterDef(
                    key="primed_pct_amber",
                    label="Primed profile amber",
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
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        primed_pct_amber = params.get("primed_pct_amber", 5)

        per_model: list[ModelAdvisoryResult] = []
        ordered_analyses = sorted(
            ctx.analyses,
            key=lambda rpa: (rpa.distance_from_origin_nm, rpa.point_index),
        )

        for model in ctx.models:
            evaluated: set[int] = set()
            complete: set[int] = set()
            active_points: set[int] = set()
            primed_points: set[int] = set()
            samples: list[EvidenceSample] = []
            loc = ctx.locale

            for rpa in ordered_analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    continue

                precip = sounding.precipitation
                precip_usable = precip is not None
                profile_usable = _warm_nose_profile_is_usable(sounding)
                if not precip_usable and not profile_usable:
                    continue
                if profile_usable:
                    detected_risk, detected_base, detected_top, ice_pellets = (
                        detect_warm_nose(sounding.derived_levels)
                    )
                else:
                    detected_risk = False
                    detected_base = detected_top = None
                    ice_pellets = False

                point_index = rpa.point_index
                evaluated.add(point_index)
                if precip_usable and profile_usable:
                    complete.add(point_index)
                stored_bounds = _complete_bounds(
                    precip.warm_nose_base_ft if precip is not None else None,
                    precip.warm_nose_top_ft if precip is not None else None,
                )
                detected_bounds = _complete_bounds(
                    detected_base,
                    detected_top,
                )
                bounds = stored_bounds or detected_bounds
                if bounds is None:
                    lower_ft = upper_ft = None
                else:
                    lower_ft, upper_ft = bounds

                active = precip is not None and (
                    precip.surface_phase in _ACTIVE_PHASES
                    or precip.freezing_rain_risk
                )
                if active:
                    active_points.add(point_index)
                    samples.append(
                        EvidenceSample(
                            point_index=point_index,
                            severity=AdvisoryStatus.RED,
                            reason_code="active_freezing_precip",
                            metric_id="sld_risk",
                            method_id=_METHOD_ID,
                            lower_altitude_ft=lower_ft,
                            upper_altitude_ft=upper_ft,
                        )
                    )
                    continue

                # Primed: freezing-rain profile shape without active precip.
                if detected_risk or ice_pellets:
                    primed_points.add(point_index)
                    samples.append(
                        EvidenceSample(
                            point_index=point_index,
                            severity=AdvisoryStatus.AMBER,
                            reason_code="primed_freezing_rain_profile",
                            metric_id="sld_risk",
                            method_id=_METHOD_ID,
                            lower_altitude_ft=lower_ft,
                            upper_altitude_ft=upper_ft,
                        )
                    )

            affected_points = active_points | primed_points
            summary = summarize_evidence(
                route_points=ordered_analyses,
                total_distance_nm=ctx.total_distance_nm,
                evaluated_point_indices=evaluated,
                complete_point_indices=complete,
                affected_point_indices=affected_points,
                evidence_samples=samples,
            )
            active_summary = summarize_evidence(
                route_points=ordered_analyses,
                total_distance_nm=ctx.total_distance_nm,
                evaluated_point_indices=evaluated,
                complete_point_indices=complete,
                affected_point_indices=active_points,
                evidence_samples=(),
            )
            primed_summary = summarize_evidence(
                route_points=ordered_analyses,
                total_distance_nm=ctx.total_distance_nm,
                evaluated_point_indices=evaluated,
                complete_point_indices=complete,
                affected_point_indices=primed_points,
                evidence_samples=(),
            )

            primed_pct = (
                100 * len(primed_points) / summary.total_points
                if summary.total_points
                else 0
            )

            if summary.total_points == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            elif active_points:
                status = AdvisoryStatus.RED
                detail = f"Freezing precipitation {active_summary.format_extent()}"
                if primed_points:
                    detail += (
                        f"; primed profile "
                        f"{primed_summary.format_extent()}"
                    )
            elif primed_points and primed_pct >= primed_pct_amber:
                status = AdvisoryStatus.AMBER
                detail = (
                    "Freezing-rain profile (no active precip) "
                    f"{primed_summary.format_extent()}"
                )
            else:
                status = AdvisoryStatus.GREEN
                detail = "No freezing precipitation signature"

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
                    primary_method_id=_METHOD_ID,
                )
            )

        return RouteAdvisoryResult.from_per_model("freezing_precip", per_model, params)
