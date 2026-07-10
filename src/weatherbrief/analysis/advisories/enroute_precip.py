"""En-route precipitation advisory — precipitation along the route as a
visibility proxy.

No model provides visibility at altitude — parameterized visibility is a
surface-only diagnostic, and only three of seven models deliver even that.
What every model does deliver is hourly precipitation split by phase
(rain/showers/snowfall), already assessed per sounding into a surface phase +
intensity (``PrecipitationAssessment``). Precipitation is the dominant driver
of en-route visibility loss for VFR:

- **Snow** collapses visibility at any intensity (often below 1500 m in a
  shower, near zero in moderate snow) and falls through the whole column, so
  the surface phase is representative of flight levels below the melting
  layer. Wet snow also adheres to the airframe.
- **Moderate+ rain** reduces visibility and ceilings and makes a windscreen
  view marginal; light rain is mostly a comfort issue.
- **Freezing rain / ice pellets** are counted here as significant
  precipitation for visibility extent, but their icing severity is owned by
  the dedicated freezing-precipitation advisory (which REDs independently).

The assessment is shared with the VFR feasibility composite via
:func:`assess_enroute_precip` (capped at AMBER there — a pilot VMC-on-top is
not directly affected by surface rain below, but widespread snow degrades
every divert/descent option, which is worth a composite caution).
"""

from __future__ import annotations

from dataclasses import dataclass

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.evidence import (
    EvidenceSample,
    EvidenceSummary,
    guard_status_for_data_state,
    summarize_evidence,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ModelAdvisoryResult,
    PrecipIntensity,
    PrecipPhase,
    RouteAdvisoryResult,
)

_SNOW_PHASES = (PrecipPhase.SNOW, PrecipPhase.MIXED)
_FREEZING_PHASES = (PrecipPhase.FREEZING_RAIN, PrecipPhase.ICE_PELLETS)
_SIGNIFICANT = (PrecipIntensity.MODERATE, PrecipIntensity.HEAVY)
_METHOD_ID = "nwp_precipitation_profile"

_DEFAULTS = {
    "snow_pct_amber": 5.0,
    "snow_moderate_pct_red": 25.0,
    "rain_pct_amber": 30.0,
}


@dataclass(frozen=True)
class EnroutePrecipAssessment:
    status: AdvisoryStatus
    detail: str
    summary: EvidenceSummary
    has_signal: bool
    snow_point_indices: frozenset[int]
    moderate_snow_point_indices: frozenset[int]
    significant_rain_point_indices: frozenset[int]
    light_point_indices: frozenset[int]


def assess_enroute_precip(
    ctx: RouteContext,
    model: str,
    params: dict[str, float] | None = None,
) -> EnroutePrecipAssessment:
    """Assess one model's precipitation grade, point sets, and evidence."""
    p = {**_DEFAULTS, **(params or {})}
    snow_pct_amber = p["snow_pct_amber"]
    snow_moderate_pct_red = p["snow_moderate_pct_red"]
    rain_pct_amber = p["rain_pct_amber"]
    loc = ctx.locale

    ordered_analyses = sorted(
        ctx.analyses,
        key=lambda rpa: (rpa.distance_from_origin_nm, rpa.point_index),
    )
    sounding_points: set[int] = set()
    signal_points: set[int] = set()
    snow_points: set[int] = set()
    moderate_snow_points: set[int] = set()
    significant_rain_points: set[int] = set()
    light_points: set[int] = set()
    samples: list[EvidenceSample] = []

    for rpa in ordered_analyses:
        sounding = rpa.sounding.get(model)
        if sounding is None:
            continue
        point_index = rpa.point_index
        sounding_points.add(point_index)
        precip = sounding.precipitation
        if precip is None:
            continue
        signal_points.add(point_index)
        phase = precip.surface_phase
        intensity = precip.surface_intensity
        if phase == PrecipPhase.DRY or intensity == PrecipIntensity.NONE:
            continue
        if phase in _SNOW_PHASES:
            snow_points.add(point_index)
            if intensity in _SIGNIFICANT:
                moderate_snow_points.add(point_index)
                severity = AdvisoryStatus.RED
            else:
                severity = AdvisoryStatus.AMBER
        elif phase in _FREEZING_PHASES:
            # Visibility extent only — severity owned by freezing_precip.
            significant_rain_points.add(point_index)
            severity = AdvisoryStatus.AMBER
        elif intensity in _SIGNIFICANT:
            significant_rain_points.add(point_index)
            severity = AdvisoryStatus.AMBER
        else:
            light_points.add(point_index)
            severity = AdvisoryStatus.GREEN
        samples.append(
            EvidenceSample(
                point_index=point_index,
                severity=severity,
                reason_code="precip_visibility",
                metric_id="precipitation_mm",
                method_id=_METHOD_ID,
            )
        )

    has_signal = bool(signal_points)
    evaluated_points = sounding_points if has_signal else set()
    affected_points = snow_points | significant_rain_points | light_points
    summary = summarize_evidence(
        route_points=ordered_analyses,
        total_distance_nm=ctx.total_distance_nm,
        evaluated_point_indices=evaluated_points,
        complete_point_indices=signal_points,
        affected_point_indices=affected_points,
        evidence_samples=samples,
        moderate_point_indices=moderate_snow_points,
    )

    def subset_summary(point_indices: set[int]) -> EvidenceSummary:
        return summarize_evidence(
            route_points=ordered_analyses,
            total_distance_nm=ctx.total_distance_nm,
            evaluated_point_indices=evaluated_points,
            complete_point_indices=signal_points,
            affected_point_indices=point_indices,
            evidence_samples=(),
        )

    snow_summary = subset_summary(snow_points)
    rain_summary = subset_summary(significant_rain_points)
    light_summary = subset_summary(light_points)

    total = summary.total_points
    snow_pct = 100.0 * len(snow_points) / total if total else 0.0
    snow_moderate_pct = (
        100.0 * len(moderate_snow_points) / total if total else 0.0
    )
    sig_rain_pct = (
        100.0 * len(significant_rain_points) / total if total else 0.0
    )

    parts: list[str] = []
    if snow_points:
        parts.append(adv_t(
            "enroute_precip.snow", loc,
            extent=snow_summary.format_extent(),
        ))
    if significant_rain_points:
        parts.append(adv_t(
            "enroute_precip.rain", loc,
            extent=rain_summary.format_extent(),
        ))

    if not has_signal:
        raw_status = AdvisoryStatus.UNAVAILABLE
        parts = [adv_t("no_data", loc)]
    elif snow_moderate_pct >= snow_moderate_pct_red:
        raw_status = AdvisoryStatus.RED
    elif snow_pct >= snow_pct_amber or sig_rain_pct >= rain_pct_amber:
        raw_status = AdvisoryStatus.AMBER
    else:
        raw_status = AdvisoryStatus.GREEN
        if not parts and light_points:
            parts.append(adv_t(
                "enroute_precip.light", loc,
                extent=light_summary.format_extent(),
            ))
        if not parts:
            parts.append(adv_t("enroute_precip.clear", loc))

    status = guard_status_for_data_state(raw_status, summary.data_state)
    if status == AdvisoryStatus.UNAVAILABLE:
        detail = adv_t(
            "no_data" if summary.data_state == "unavailable" else "partial_data",
            loc,
        )
    else:
        detail = " | ".join(parts)

    return EnroutePrecipAssessment(
        status=status,
        detail=detail,
        summary=summary,
        has_signal=has_signal,
        snow_point_indices=frozenset(snow_points),
        moderate_snow_point_indices=frozenset(moderate_snow_points),
        significant_rain_point_indices=frozenset(significant_rain_points),
        light_point_indices=frozenset(light_points),
    )


def classify_enroute_precip(
    ctx: RouteContext,
    model: str,
    params: dict[str, float] | None = None,
) -> tuple[AdvisoryStatus, str, int, int, bool]:
    """Compatibility wrapper returning the historical five-tuple."""
    assessment = assess_enroute_precip(ctx, model, params)
    legacy_total = sum(
        1 for rpa in ctx.analyses if rpa.sounding.get(model) is not None
    )
    return (
        assessment.status,
        assessment.detail,
        assessment.summary.affected_points,
        legacy_total,
        assessment.has_signal,
    )


@register
class EnroutePrecipEvaluator:
    """Evaluates precipitation along the route as a visibility hazard."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="enroute_precip",
            name="En-route Precipitation",
            short_description="Precipitation along the route (visibility)",
            description=(
                "Grades precipitation along the route as a visibility hazard — "
                "no model forecasts visibility at altitude, so precipitation "
                "phase and intensity are the honest proxy. Snow is the VFR "
                "killer: visibility collapses in even light snow showers and "
                "the surface phase is representative of the whole column below "
                "the melting layer, so any snow over more than a small fraction "
                "of the route is amber and widespread moderate snow is red. "
                "Moderate-or-heavier rain over a large fraction of the route is "
                "amber (reduced visibility and ceilings); light rain is noted "
                "but stays green. Freezing rain / ice pellets count toward the "
                "extent here, but their icing severity is graded by the "
                "dedicated Freezing Precipitation advisory. Unavailable (not "
                "green) on packs without precipitation data."
            ),
            category="precipitation",
            parameters=[
                AdvisoryParameterDef(
                    key="snow_pct_amber",
                    label="Snow % (amber)",
                    description="Route percentage with any snow for amber",
                    type="percent",
                    unit="%",
                    default=5,
                    min=0,
                    max=50,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="snow_moderate_pct_red",
                    label="Moderate snow % (red)",
                    description="Route percentage with moderate+ snow for red",
                    type="percent",
                    unit="%",
                    default=25,
                    min=5,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="rain_pct_amber",
                    label="Rain % (amber)",
                    description="Route percentage with moderate+ rain for amber",
                    type="percent",
                    unit="%",
                    default=30,
                    min=5,
                    max=80,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            assessment = assess_enroute_precip(
                ctx, model, params,
            )
            missing_detail = adv_t(
                (
                    "no_data"
                    if assessment.summary.data_state == "unavailable"
                    else "partial_data"
                ),
                ctx.locale,
            )
            per_model.append(
                assessment.summary.build_result(
                    model=model,
                    status=assessment.status,
                    detail=assessment.detail,
                    unavailable_detail=missing_detail,
                    primary_method_id=_METHOD_ID,
                )
            )

        return RouteAdvisoryResult.from_per_model("enroute_precip", per_model, params)
