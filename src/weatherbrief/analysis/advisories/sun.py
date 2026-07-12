"""Sun advisory evaluator (issue #227).

Thin classifier over the precomputed :class:`RouteSunAnalysis` on
``ctx.sun``. Never a go/no-go: GREEN by default, AMBER only when a low sun sits
roughly down the wind-best runway on takeoff/landing (glare), or — for
day-VFR profiles — when the leg ends near/after sunset (gated by
``warn_near_sunset``). The detail text always carries the sun-side note (which
side the sun is on for most of the route).

Model-independent (the sun is not a weather model), so it returns a single
``model="all"`` result, like :class:`ModelAgreementEvaluator`.
"""

from __future__ import annotations

from datetime import timedelta

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    GlareAssessment,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
    SunSideSummary,
)

_SUN_ID = "sun"


def _glare_into_sun(
    g: GlareAssessment | None,
    glare_azimuth_deg: float,
    glare_elev_max_deg: float,
) -> bool:
    """Recompute the glare condition from the stored raw geometry + user params.

    Done here (not read from ``g.into_sun``) so tuning the angle/elevation params
    and recalculating actually changes the result.
    """
    if g is None or g.sun_elevation_deg is None or g.relative_bearing_deg is None:
        return False
    return (0.0 < g.sun_elevation_deg <= glare_elev_max_deg) and (
        abs(g.relative_bearing_deg) <= glare_azimuth_deg
    )


@register
class SunEvaluator:
    """Sun glare + night-proximity advisory with an informational sun-side note."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id=_SUN_ID,
            name="Sun",
            short_description="Low-sun glare on takeoff/landing + which side the sun is on",
            description=(
                "Informational, never a go/no-go. GREEN by default. Goes AMBER when a "
                "low sun would sit roughly down the wind-best runway on takeoff or "
                "landing (glare on the roll/flare), and — for day-VFR profiles — when "
                "the leg ends near or after sunset. It also always reports where the "
                "sun sits for most of the route — left or right (handy for deciding "
                "where to seat a passenger on the shaded side, or which window gives "
                "the better-lit shot for photos), or ahead/behind when you are flying "
                "into the sun or have it at your back. Night-capable profiles can disable the "
                "dusk AMBER via warn_near_sunset; glare AMBER always applies."
            ),
            category="sun",
            timing_class="cheap",
            default_enabled=True,
            altitude_dependent=False,
            parameters=[
                AdvisoryParameterDef(
                    key="glare_azimuth_deg",
                    label="Glare cone half-angle",
                    description="Sun within this many degrees of the runway heading counts as glare",
                    type="number",
                    unit="°",
                    default=30,
                    min=5,
                    max=90,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="glare_elev_max_deg",
                    label="Low-sun ceiling",
                    description="Sun at or below this elevation counts as a low (glaring) sun",
                    type="number",
                    unit="°",
                    default=15,
                    min=2,
                    max=30,
                    step=1,
                ),
                AdvisoryParameterDef(
                    key="warn_near_sunset",
                    label="Warn near sunset/sunrise",
                    description="Amber when landing near/after sunset or departing near/before sunrise (off for night-capable profiles)",
                    type="boolean",
                    default=1,
                    min=0,
                    max=1,
                    step=1,
                    audience="pilot",
                ),
                AdvisoryParameterDef(
                    key="sunset_margin_min",
                    label="Sunset margin",
                    description="Treat landing within this many minutes of sunset (or departure of sunrise) as near-dark",
                    type="number",
                    unit="min",
                    default=30,
                    min=0,
                    max=120,
                    step=5,
                    audience="pilot",
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        loc = ctx.locale
        sun = ctx.sun

        if sun is None:
            # Old pack / sun unavailable. Follow the ModelAgreementEvaluator
            # pattern: a single model="all" UNAVAILABLE entry. (The aggregate
            # badge resolves to GREEN under MAJORITY, but the per-model entry and
            # detail make the unavailability visible — and this keeps the
            # aggregation-mode invariant the registry enforces.)
            per_model = [ModelAdvisoryResult.build(
                model="all", status=AdvisoryStatus.UNAVAILABLE,
                detail=adv_t("sun.no_data", loc), affected=0, total=0,
                total_distance_nm=ctx.total_distance_nm,
            )]
            return RouteAdvisoryResult.from_per_model(_SUN_ID, per_model, params)

        glare_azimuth_deg = params.get("glare_azimuth_deg", 30)
        glare_elev_max_deg = params.get("glare_elev_max_deg", 15)
        warn_near_sunset = bool(params.get("warn_near_sunset", 1))
        sunset_margin_min = params.get("sunset_margin_min", 30)

        status = AdvisoryStatus.GREEN
        parts: list[str] = []

        # --- Glare on the wind-best runway (always applies) ---
        # `is not None` first so the type checker narrows GlareAssessment | None
        # before we read its fields; _glare_into_sun also no-ops on None.
        if sun.takeoff is not None and _glare_into_sun(
            sun.takeoff, glare_azimuth_deg, glare_elev_max_deg
        ):
            status = AdvisoryStatus.AMBER
            parts.append(adv_t(
                "sun.glare_takeoff", loc,
                elev=round(sun.takeoff.sun_elevation_deg or 0),
                runway=sun.takeoff.runway_ident or "?",
            ))
        if sun.landing is not None and _glare_into_sun(
            sun.landing, glare_azimuth_deg, glare_elev_max_deg
        ):
            status = AdvisoryStatus.AMBER
            parts.append(adv_t(
                "sun.glare_landing", loc,
                elev=round(sun.landing.sun_elevation_deg or 0),
                runway=sun.landing.runway_ident or "?",
            ))

        # --- Near-sunset / near-sunrise (gated for night-capable profiles) ---
        if warn_near_sunset:
            if _near_dark(ctx, "landing", sunset_margin_min):
                status = AdvisoryStatus.AMBER
                icao = sun.landing.airport_icao if sun.landing else ""
                parts.append(adv_t("sun.near_sunset", loc, icao=icao))
            if _near_dark(ctx, "takeoff", sunset_margin_min):
                status = AdvisoryStatus.AMBER
                icao = sun.takeoff.airport_icao if sun.takeoff else ""
                parts.append(adv_t("sun.near_sunrise", loc, icao=icao))

        # --- Sun-side note (always present) ---
        parts.append(_sun_side_note(sun.sun_side, loc))

        detail = " · ".join(p for p in parts if p)

        per_model = [ModelAdvisoryResult.build(
            model="all", status=status, detail=detail,
            affected=1 if status != AdvisoryStatus.GREEN else 0, total=1,
            total_distance_nm=ctx.total_distance_nm,
        )]
        return RouteAdvisoryResult.from_per_model(_SUN_ID, per_model, params)


def _sun_side_note(sun_side: SunSideSummary | None, loc: str | None) -> str:
    """Build the informational sun-side note.

    Just the directional fact: left/right gives the seating/shade side ("Sun on
    your left for ~90% of the route"), while ahead/behind reports flying into the
    sun or having it at your back. The practical use — which side to seat a
    passenger / better window for photos — lives in the advisory's (i)
    description, not on the advisory line itself.
    """
    if sun_side is None or sun_side.dominant_side == "none":
        return adv_t("sun.side_none", loc)
    dom = sun_side.dominant_side
    pct = round(sun_side.dominant_side_pct)
    if dom == "ahead":
        return adv_t("sun.ahead_note", loc, pct=pct)
    if dom == "behind":
        return adv_t("sun.behind_note", loc, pct=pct)
    # The "~pct%" already implies the sun sits elsewhere on the rest of the
    # route, so we don't append a separate "it shifts" clause.
    return adv_t("sun.side_note", loc, side=adv_t(f"sun.{dom}", loc), pct=pct)


def _near_dark(ctx: RouteContext, phase: str, margin_min: float) -> bool:
    """True when a landing is in the dusk-to-dark window or a takeoff in the
    dark-to-dawn window.

    Uses the route endpoint's lat/lon/time and the day's sunrise/sunset from the
    euro_aip solar primitive (fixed ``morning``/``evening`` keys). Polar day/night
    (no event) → not near-dark.

    The landing check fires for any arrival at or after ``sunset - margin`` on
    that date — i.e. the whole dusk-and-after-dark evening, not only the
    immediate near-sunset minutes. (An early-morning arrival is compared against
    that same date's later sunset, so it is not flagged here.) The advisory text
    is worded for a low-light arrival rather than "fading light" so it stays
    accurate for both dusk and fully-dark cases. The takeoff check is symmetric
    around ``sunrise + margin``.
    """
    sun = ctx.sun
    assessment = sun.landing if phase == "landing" else sun.takeoff
    if assessment is None or not ctx.analyses:
        return False

    point = ctx.analyses[-1] if phase == "landing" else ctx.analyses[0]
    try:
        from euro_aip.utils.solar import sun_events

        events = sun_events(point.lat, point.lon, point.interpolated_time.date())
    except Exception:
        return False

    when = point.interpolated_time
    margin = timedelta(minutes=margin_min)
    if phase == "landing":
        sunset = events.get("evening")
        return sunset is not None and when >= sunset - margin
    sunrise = events.get("morning")
    return sunrise is not None and when <= sunrise + margin
