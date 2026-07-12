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

The classifier is shared with the VFR feasibility composite via
:func:`enroute_precip_check` (capped at AMBER there — a pilot VMC-on-top is
not directly affected by surface rain below, but widespread snow degrades
every divert/descent option, which is worth a composite caution).
"""

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
    HighlightSeverity,
    ModelAdvisoryResult,
    PrecipIntensity,
    PrecipPhase,
    RouteAdvisoryResult,
)

_SNOW_PHASES = (PrecipPhase.SNOW, PrecipPhase.MIXED)
_FREEZING_PHASES = (PrecipPhase.FREEZING_RAIN, PrecipPhase.ICE_PELLETS)
_SIGNIFICANT = (PrecipIntensity.MODERATE, PrecipIntensity.HEAVY)

_DEFAULTS = {
    "snow_pct_amber": 5.0,
    "snow_moderate_pct_red": 25.0,
    "rain_pct_amber": 30.0,
}


def classify_precip_point(precip) -> str | None:
    """Classify one point's precipitation assessment into a hazard class.

    Returns ``"snow_moderate"`` (moderate+ snow/mixed), ``"snow"`` (lighter
    snow/mixed), ``"sig"`` (moderate+ rain, or FZRA/PL counted for visibility
    extent only), ``"light"`` (light rain — comfort, not a hazard), or ``None``
    (dry / no assessment). Single source of the per-point phase/intensity
    bucketing, shared by :func:`classify_enroute_precip` and the highlight
    ribbons (#375) so the geometry cannot drift from the grade.
    """
    if precip is None:
        return None
    phase = precip.surface_phase
    intensity = precip.surface_intensity
    if phase == PrecipPhase.DRY or intensity == PrecipIntensity.NONE:
        return None
    if phase in _SNOW_PHASES:
        return "snow_moderate" if intensity in _SIGNIFICANT else "snow"
    if phase in _FREEZING_PHASES:
        # Visibility extent only — severity owned by freezing_precip.
        return "sig"
    if intensity in _SIGNIFICANT:
        return "sig"
    return "light"


def precip_point_severity(cls: str | None, *, cap_amber: bool = False) -> HighlightSeverity:
    """Ribbon severity for a :func:`classify_precip_point` class (#375).

    Moderate+ snow → red (amber when ``cap_amber``, matching the VFR
    composite's cap); any snow or significant rain/FZRA/PL → amber; light/dry
    → green.
    """
    if cls == "snow_moderate":
        return HighlightSeverity.AMBER if cap_amber else HighlightSeverity.RED
    if cls in ("snow", "sig"):
        return HighlightSeverity.AMBER
    return HighlightSeverity.GREEN


def classify_enroute_precip(
    ctx: RouteContext,
    model: str,
    params: dict[str, float] | None = None,
) -> tuple[AdvisoryStatus, str, int, int, bool]:
    """Classify en-route precipitation for one model.

    Returns ``(status, detail, affected, total, has_signal)``.
    ``has_signal`` is False when no point carries a precipitation assessment
    (old pack) — callers must treat that as UNAVAILABLE, not GREEN.
    """
    p = {**_DEFAULTS, **(params or {})}
    snow_pct_amber = p["snow_pct_amber"]
    snow_moderate_pct_red = p["snow_moderate_pct_red"]
    rain_pct_amber = p["rain_pct_amber"]
    loc = ctx.locale

    total = 0
    snow_pts = 0           # any snow/mixed, any intensity
    snow_moderate_pts = 0  # snow/mixed at moderate+
    sig_rain_pts = 0       # rain moderate+ — and FZRA/PL (vis extent only)
    light_pts = 0          # light rain — comfort, not a hazard
    has_signal = False

    for rpa in ctx.analyses:
        sounding = rpa.sounding.get(model)
        if sounding is None:
            continue
        total += 1
        precip = sounding.precipitation
        if precip is None:
            continue
        has_signal = True
        cls = classify_precip_point(precip)
        if cls is None:
            continue
        if cls in ("snow", "snow_moderate"):
            snow_pts += 1
            if cls == "snow_moderate":
                snow_moderate_pts += 1
        elif cls == "sig":
            sig_rain_pts += 1
        else:
            light_pts += 1

    if total == 0 or not has_signal:
        return AdvisoryStatus.UNAVAILABLE, adv_t("no_data", loc), 0, total, has_signal

    affected = snow_pts + sig_rain_pts + light_pts
    snow_pct = 100.0 * snow_pts / total
    snow_moderate_pct = 100.0 * snow_moderate_pts / total
    sig_rain_pct = 100.0 * sig_rain_pts / total

    parts: list[str] = []
    if snow_pts:
        parts.append(adv_t(
            "enroute_precip.snow", loc,
            extent=format_extent(snow_pts, total, ctx.total_distance_nm),
        ))
    if sig_rain_pts:
        parts.append(adv_t(
            "enroute_precip.rain", loc,
            extent=format_extent(sig_rain_pts, total, ctx.total_distance_nm),
        ))

    if snow_moderate_pct >= snow_moderate_pct_red:
        status = AdvisoryStatus.RED
    elif snow_pct >= snow_pct_amber or sig_rain_pct >= rain_pct_amber:
        status = AdvisoryStatus.AMBER
    else:
        status = AdvisoryStatus.GREEN
        if not parts and light_pts:
            parts.append(adv_t(
                "enroute_precip.light", loc,
                extent=format_extent(light_pts, total, ctx.total_distance_nm),
            ))
        if not parts:
            parts.append(adv_t("enroute_precip.clear", loc))

    return status, " | ".join(parts), affected, total, has_signal


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
            status, detail, affected, total, has_signal = classify_enroute_precip(
                ctx, model, params,
            )

            # Per-point highlight geometry (#375): full-column precip cutouts
            # (the hazard is the whole column below the melting layer) + a
            # ribbon mirroring classify_precip_point. Skipped when the model
            # has no precipitation signal (status UNAVAILABLE).
            highlights = None
            if has_signal and total > 0:
                ribbon_points: list[tuple[float, HighlightSeverity]] = []
                region_cells: list[tuple[float, FlaggedCell | None]] = []
                for rpa in ctx.analyses:
                    dist = rpa.distance_from_origin_nm or 0.0
                    sounding = rpa.sounding.get(model)
                    if sounding is None:
                        ribbon_points.append((dist, HighlightSeverity.UNAVAILABLE))
                        region_cells.append((dist, None))
                        continue
                    severity = precip_point_severity(
                        classify_precip_point(sounding.precipitation)
                    )
                    ribbon_points.append((dist, severity))
                    if severity in (HighlightSeverity.AMBER, HighlightSeverity.RED):
                        region_cells.append((dist, FlaggedCell(
                            kind="precip_column",
                            severity=severity,
                            base_ft=None,
                            top_ft=None,
                        )))
                    else:
                        region_cells.append((dist, None))
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

        return RouteAdvisoryResult.from_per_model("enroute_precip", per_model, params)
