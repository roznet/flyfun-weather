"""Mountain wind advisory — wind near terrain, with wave-signature corroboration.

Wind speed near ridge level is a necessary but not sufficient condition for
mountain wave / rotor: the classical ingredients are strong cross-barrier
flow AND a stable layer near ridge top (Scorer parameter decreasing with
height). Speed alone cannot separate "windy ridge" from "rotor day", so this
evaluator corroborates the wind grade with two signatures already computed
per sounding:

- an **inversion layer near ridge top** (the stable layer that supports
  trapped lee waves and rotors beneath them);
- an **OSCILLATING vertical-motion classification** (alternating ascent /
  descent couplets in the omega profile — the model resolving the wave
  itself).

When either signature is present at a strong-wind mountain point, the RED
threshold drops from ``wind_red_kt`` (speed so high it's hazardous
regardless) to ``corroborated_red_kt``. Cross-ridge wind *direction* is
deliberately not assessed: the elevation profile is one-dimensional along
the route, so ridge orientation is unknown — pretending otherwise would be
false precision.
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    EvidenceSample,
    FlaggedCell,
    build_regions,
    format_extent,
    max_terrain_near_point,
    summarize_evidence,
    wind_at_altitude,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryHighlights,
    AdvisoryParameterDef,
    AdvisoryStatus,
    HighlightSeverity,
    InversionLayer,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
    SoundingAnalysis,
    VerticalMotionClass,
)

# Stable-layer search band around ridge top: an inversion this far below to
# this far above the local peak counts as the wave-supporting layer. Classical
# wave theory (Scorer parameter decreasing with height) places the critical
# stable layer at or just above ridge level, so the upper bound is kept tight
# (+2000 ft) to exclude elevated frontal inversions that carry no wave
# mechanism — a wider band false-positives on any day with a mid-level stable
# layer above a ridge. See designs/meteorology-decisions.md §11d.
_INV_BELOW_FT = 1000.0
_INV_ABOVE_FT = 2000.0


def _wave_signatures(
    sounding: SoundingAnalysis | None,
    terrain_ft: float,
) -> tuple[set[str], InversionLayer | None]:
    """Wave-supporting signatures at one point: 'inversion' and/or 'oscillating'.

    Also returns the corroborating ridge-top inversion layer (``None`` when the
    signature is oscillation-only) — its band is the ``wave_signature`` cutout
    geometry for the highlight (#375).
    """
    sigs: set[str] = set()
    inv_layer: InversionLayer | None = None
    if sounding is None:
        return sigs, inv_layer
    vm = sounding.vertical_motion
    if vm is not None and vm.classification == VerticalMotionClass.OSCILLATING:
        sigs.add("oscillating")
    lo = terrain_ft - _INV_BELOW_FT
    hi = terrain_ft + _INV_ABOVE_FT
    for inv in sounding.inversion_layers:
        if inv.top_ft > lo and inv.base_ft < hi:
            sigs.add("inversion")
            inv_layer = inv
            break
    return sigs, inv_layer


@register
class MountainWindEvaluator:
    """Evaluates wind speed near terrain tops for mountain wave/rotor risk."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="mountain_wind",
            name="Mountain Wind",
            short_description="Wind and wave signatures near terrain",
            description=(
                "Evaluates wind speed near terrain tops at points where terrain "
                "exceeds a threshold, corroborated by the classical wave "
                "ingredients: a stable layer (inversion) near ridge top or a "
                "wave-like oscillating vertical-motion profile in the model. "
                "Strong wind alone is amber; very strong wind is red; and "
                "moderately strong wind WITH a wave signature is also red at a "
                "lower threshold — that combination is the rotor-day setup, "
                "not just a windy ridge."
            ),
            category="turbulence",
            parameters=[
                AdvisoryParameterDef(
                    key="terrain_threshold_ft",
                    label="Terrain threshold",
                    description="Only evaluate points where terrain exceeds this altitude",
                    type="altitude",
                    unit="ft",
                    default=3000,
                    min=1000,
                    max=8000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="altitude_margin_ft",
                    label="Altitude margin",
                    description="Check wind within this margin above terrain tops",
                    type="altitude",
                    unit="ft",
                    default=2000,
                    min=500,
                    max=5000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="wind_amber_kt",
                    label="Wind amber (kt)",
                    description="Wind speed above terrain for amber",
                    type="speed",
                    unit="kt",
                    default=20,
                    min=10,
                    max=40,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="wind_red_kt",
                    label="Wind red (kt)",
                    description="Wind speed above terrain for red (severe rotor)",
                    type="speed",
                    unit="kt",
                    default=40,
                    min=20,
                    max=60,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="corroborated_red_kt",
                    label="Wind red w/ signature",
                    description=(
                        "Red threshold when a wave signature (ridge-top "
                        "inversion or oscillating vertical motion) is present "
                        "at the same point"
                    ),
                    type="speed",
                    unit="kt",
                    default=30,
                    min=15,
                    max=50,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        terrain_threshold = params.get("terrain_threshold_ft", 3000)
        altitude_margin = params.get("altitude_margin_ft", 2000)
        wind_amber = params.get("wind_amber_kt", 20)
        wind_red = params.get("wind_red_kt", 40)
        corroborated_red = params.get("corroborated_red_kt", 30)

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            terrain_known = 0  # points with a known terrain elevation
            max_wind = 0.0
            wave_red = False          # corroborated point above the lower red bar
            wave_sigs: set[str] = set()  # signatures seen at strong-wind points
            # One evidence sample per route point (#393). Non-mountain points are
            # GREEN but out of the coverage domain (``in_domain=False``) so
            # coverage is measured over mountain points only — a flat route grades
            # GREEN, not UNAVAILABLE. Mountain points with no wind are in-domain
            # but unassessed. The sample's region is the ridge_wind band; the
            # wave_signature overlay (red points only) is built separately below.
            samples: list[EvidenceSample] = []
            wave_cells: list[tuple[float, FlaggedCell | None]] = []
            peak_dist: float | None = None
            peak_speed = 0.0

            for rpa in ctx.analyses:
                dist = rpa.distance_from_origin_nm or 0.0
                terrain_ft = max_terrain_near_point(
                    ctx.elevation, rpa.distance_from_origin_nm
                )
                if terrain_ft is None:
                    # No elevation data here — we cannot say whether there is
                    # terrain, so this is UNAVAILABLE, not "no mountains" GREEN.
                    samples.append(EvidenceSample(
                        distance_nm=dist, assessed=False, in_domain=False,
                        severity=HighlightSeverity.UNAVAILABLE,
                    ))
                    wave_cells.append((dist, None))
                    continue
                terrain_known += 1
                if terrain_ft < terrain_threshold:
                    # Terrain known and genuinely below the threshold — assessed
                    # flat, no wave mechanism → GREEN, out of the mountain domain.
                    samples.append(EvidenceSample(
                        distance_nm=dist, assessed=True, in_domain=False,
                        severity=HighlightSeverity.GREEN,
                    ))
                    wave_cells.append((dist, None))
                    continue

                wind = wind_at_altitude(
                    ctx.cross_sections, model, rpa.point_index,
                    terrain_ft + altitude_margin, rpa.forecast_hour,
                )
                if wind is None:
                    # Mountain point but no wind lookup — cannot assess the wave
                    # risk here. UNAVAILABLE, in-domain but unassessed (so an
                    # all-no-wind mountain route is UNAVAILABLE, not a "light
                    # winds (0kt)" GREEN).
                    samples.append(EvidenceSample(
                        distance_nm=dist, assessed=False, in_domain=True,
                        severity=HighlightSeverity.UNAVAILABLE,
                    ))
                    wave_cells.append((dist, None))
                    continue

                speed_kt, _ = wind
                if speed_kt > max_wind:
                    max_wind = speed_kt

                if speed_kt < wind_amber:
                    samples.append(EvidenceSample(
                        distance_nm=dist, assessed=True, in_domain=True,
                        severity=HighlightSeverity.GREEN,
                    ))
                    wave_cells.append((dist, None))
                    continue

                sigs, inv_layer = _wave_signatures(rpa.sounding.get(model), terrain_ft)
                if sigs:
                    wave_sigs |= sigs
                    if speed_kt >= corroborated_red:
                        wave_red = True

                # Red here = very strong wind regardless, or the rotor-day combo
                # (wave signature + the lower corroborated bar) — the same two
                # triggers as the route grade, located per point.
                point_red = speed_kt >= wind_red or (
                    bool(sigs) and speed_kt >= corroborated_red
                )
                severity = (
                    HighlightSeverity.RED if point_red else HighlightSeverity.AMBER
                )
                samples.append(EvidenceSample(
                    distance_nm=dist, assessed=True, in_domain=True,
                    severity=severity,
                    region=FlaggedCell(
                        kind="ridge_wind",
                        severity=severity,
                        base_ft=int(terrain_ft),
                        top_ft=int(terrain_ft + altitude_margin),
                        metric_id="wind_speed",
                    ),
                ))
                # The corroborating inversion band visually explains why the RED
                # threshold dropped ("rotor day").
                if point_red and inv_layer is not None:
                    wave_cells.append((dist, FlaggedCell(
                        kind="wave_signature",
                        severity=HighlightSeverity.RED,
                        base_ft=int(inv_layer.base_ft),
                        top_ft=int(inv_layer.top_ft),
                        metric_id="wind_speed",
                    )))
                else:
                    wave_cells.append((dist, None))

                if speed_kt > peak_speed:
                    peak_speed = speed_kt
                    peak_dist = dist

            summary = summarize_evidence(
                samples, ctx.total_distance_nm, peak_dist_nm=peak_dist,
                speed_kt=ctx.cruise_groundspeed_kt,
            )
            mountain_pts = summary.domain   # points where terrain exceeds threshold
            total = summary.assessed        # mountain points with wind data
            affected = summary.affected

            loc = ctx.locale
            # D3 (#571): this advisory's domain is the *mountain* points, so its
            # denominator is mountain miles — not the route length it used to be
            # multiplied by (ICON printed "543nm/582nm (93%)" for a 131.8 nm
            # footprint, a ~4x overstatement). ``summary.extent`` carries
            # ``domain_nm`` from the in-domain samples, and the sentence names
            # that denominator the way ``sun`` names "of the sunlit route".
            ext = format_extent(
                summary.extent, domain_label=adv_t("mountain_wind.of_terrain", loc),
            )
            sig_text = " + ".join(
                adv_t(f"mountain_wind.sig_{s}", loc)
                for s in sorted(wave_sigs)
            )
            if terrain_known == 0:
                # No elevation profile at all — the terrain axis is unassessable.
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            elif mountain_pts == 0:
                # Terrain known everywhere and below the threshold — assessed flat.
                status = AdvisoryStatus.GREEN
                detail = adv_t("mountain_wind.no_terrain", loc)
            elif total == 0:
                # Mountains on route but no wind data at any of them — the grade
                # and the (already-UNAVAILABLE) ribbon must agree: UNAVAILABLE.
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            elif max_wind >= wind_red:
                status = AdvisoryStatus.RED
                detail = adv_t("mountain_wind.severe", loc, speed=f"{max_wind:.0f}", extent=ext)
                if sig_text:
                    detail += adv_t("mountain_wind.sig_suffix", loc, signature=sig_text)
            elif wave_red:
                status = AdvisoryStatus.RED
                detail = adv_t(
                    "mountain_wind.wave_confirmed", loc,
                    speed=f"{max_wind:.0f}", signature=sig_text, extent=ext,
                )
            elif max_wind >= wind_amber:
                status = AdvisoryStatus.AMBER
                detail = adv_t("mountain_wind.wave_risk", loc, speed=f"{max_wind:.0f}", extent=ext)
                if sig_text:
                    detail += adv_t("mountain_wind.sig_suffix", loc, signature=sig_text)
            else:
                status = AdvisoryStatus.GREEN
                detail = adv_t("mountain_wind.light", loc, speed=f"{max_wind:.0f}")

            # Coverage tolerance (#391): a "light winds" verdict when wind
            # resolved at too few of the route's mountain points cannot vouch for
            # the unassessed mountain segment. Coverage is measured over mountain
            # points (mountain_pts), not the whole route — non-mountain points are
            # legitimately GREEN, and the flat-route GREEN (mountain_pts == 0) is
            # deliberately preserved (the "GREEN not UNAVAILABLE" UX).
            if (
                status == AdvisoryStatus.GREEN
                and mountain_pts > 0
                and summary.below_coverage
            ):
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)

            # Highlights (#375): built whenever the route has points at all —
            # a no-mountain route gets the all-green ribbon its GREEN grade
            # implies (not None). The ridge_wind regions come from the evidence
            # samples; the wave_signature overlay (red points only) is appended.
            # Peak = the strongest-wind affected point.
            highlights = None
            if samples:
                highlights = AdvisoryHighlights(
                    ribbon=summary.highlights.ribbon,
                    regions=(
                        summary.highlights.regions
                        + build_regions(wave_cells, ctx.total_distance_nm)
                    ),
                    peak_dist_nm=summary.highlights.peak_dist_nm,
                )

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
                affected_nm=summary.affected_nm,
                # The published denominator is mountain miles, not route miles
                # — and it is named, so no consumer can re-derive the ~4x
                # overstatement from ``affected_nm / total_nm`` (#571 D3).
                domain_nm=summary.extent.domain_nm,
                affected_domain=adv_t("mountain_wind.of_terrain", ctx.locale),
                highlights=highlights,
            ))

        return RouteAdvisoryResult.from_per_model("mountain_wind", per_model, params)
