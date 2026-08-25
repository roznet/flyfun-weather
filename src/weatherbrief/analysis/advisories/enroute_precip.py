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
    EvidenceSample,
    FlaggedCell,
    below_coverage,
    format_extent,
    route_extent,
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
    # Per-point flags for the geometry-accurate extents (#571). The three
    # sentences below each name a DIFFERENT population (snow / significant rain
    # / light rain), so each needs its own reduction over the route's cell
    # edges — a share of the union's nm would describe the wrong points.
    dists: list[float] = []
    snow_flags: list[bool] = []
    sig_flags: list[bool] = []
    light_flags: list[bool] = []

    for rpa in ctx.analyses:
        dists.append(rpa.distance_from_origin_nm or 0.0)
        snow_flags.append(False)
        sig_flags.append(False)
        light_flags.append(False)
        sounding = rpa.sounding.get(model)
        if sounding is None:
            continue
        precip = sounding.precipitation
        if precip is None:
            # Sounding present but no precipitation assessment — unassessable for
            # this evaluator. It must NOT count into the denominator, or it
            # dilutes the snow/rain percentage (2 snow points among 8 blanks
            # would read 20%, not 100%) — #391.
            continue
        total += 1
        has_signal = True
        cls = classify_precip_point(precip)
        if cls is None:
            continue
        if cls in ("snow", "snow_moderate"):
            snow_pts += 1
            snow_flags[-1] = True
            if cls == "snow_moderate":
                snow_moderate_pts += 1
        elif cls == "sig":
            sig_rain_pts += 1
            sig_flags[-1] = True
        else:
            light_pts += 1
            light_flags[-1] = True

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
            extent=format_extent(
                route_extent(dists, ctx.total_distance_nm, snow_flags)
            ),
        ))
    if sig_rain_pts:
        parts.append(adv_t(
            "enroute_precip.rain", loc,
            extent=format_extent(
                route_extent(dists, ctx.total_distance_nm, sig_flags)
            ),
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
                extent=format_extent(
                    route_extent(dists, ctx.total_distance_nm, light_flags)
                ),
            ))
        if not parts:
            parts.append(adv_t("enroute_precip.clear", loc))

    # Coverage tolerance (#391): a clear/light verdict from points with a precip
    # assessment at too small a share of the route cannot vouch for the rest.
    # A flagged snow/rain verdict always stands. (The VFR composite maps this
    # UNAVAILABLE back to GREEN, so the composite is unaffected.)
    if status == AdvisoryStatus.GREEN and below_coverage(total, len(ctx.analyses)):
        return AdvisoryStatus.UNAVAILABLE, adv_t("no_data", loc), affected, total, has_signal

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
            # The extent-thresholded grade (snow/rain sub-percentages) stays in
            # the shared classifier; VFR feasibility reuses it too. The evidence
            # list drives the geometry-accurate affected_nm and the highlight, and
            # keys off the SAME per-point predicate (``classify_precip_point``), so
            # the grade's ``affected`` and the ribbon cannot drift (#393).
            status, detail, affected, total, has_signal = classify_enroute_precip(
                ctx, model, params,
            )

            # One evidence sample per route point. ``severity`` (ribbon) maps
            # light rain to GREEN; ``affected`` (grade) counts any precipitation
            # incl. light rain — deliberately different, so each is passed
            # explicitly. Full-column precip cutouts (hazard = whole column below
            # the melting layer) on flagged points only.
            samples: list[EvidenceSample] = []
            for rpa in ctx.analyses:
                dist = rpa.distance_from_origin_nm or 0.0
                sounding = rpa.sounding.get(model)
                # No sounding, or a sounding with no precipitation assessment, is
                # unassessable → UNAVAILABLE ribbon (matches the grade's
                # denominator, which excludes these points).
                if sounding is None or sounding.precipitation is None:
                    samples.append(EvidenceSample(
                        distance_nm=dist, assessed=False,
                        severity=HighlightSeverity.UNAVAILABLE,
                    ))
                    continue
                cls = classify_precip_point(sounding.precipitation)
                severity = precip_point_severity(cls)
                region = None
                if severity in (HighlightSeverity.AMBER, HighlightSeverity.RED):
                    region = FlaggedCell(
                        kind="precip_column",
                        severity=severity,
                        base_ft=None,
                        top_ft=None,
                        metric_id="precipitation",
                    )
                samples.append(EvidenceSample(
                    distance_nm=dist, assessed=True, severity=severity,
                    affected=cls is not None, region=region,
                ))

            summary = summarize_evidence(samples, ctx.total_distance_nm)

            # Highlights only when the model has a precipitation signal (the
            # no-signal case grades UNAVAILABLE). ``affected``/``total`` stay the
            # classifier's authoritative counts (equal to the summary's, by the
            # shared predicate); affected_nm is the summary's geometry-accurate
            # extent of the same affected points.
            highlights = summary.highlights if has_signal and total > 0 else None

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
                affected_nm=summary.affected_nm,
                highlights=highlights,
            ))

        return RouteAdvisoryResult.from_per_model("enroute_precip", per_model, params)
