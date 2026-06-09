"""Route solar analysis: night/twilight shading, sun side, dep/arr glare.

Pure analysis (no I/O, no imports from ``tasks/``). Everything is built from the
solar primitive in ``euro_aip`` fed the ``lat``/``lon``/``interpolated_time`` and
``track_deg`` that every :class:`RoutePointAnalysis` already carries, plus the
wind-best runway headings from the already-computed airport conditions.

Heading reference: ``solar_azimuth`` returns *true* degrees, and euro_aip runway
headings (``RunwayEnd.heading_deg``) are also *true* (the ``_degT`` fields), so the
glare comparison stays entirely in true degrees with no magnetic conversion.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from euro_aip.utils.solar import (
    CIVIL_TWILIGHT_DEG,
    solar_azimuth,
    solar_elevation,
    solar_position,
)

from weatherbrief.models import (
    GlareAssessment,
    NightInterval,
    RoutePointAnalysis,
    RouteSunAnalysis,
    SunPoint,
    SunSideSegment,
    SunSideSummary,
)
from weatherbrief.models.airport_conditions import AirportConditionsSummary, RunwayWind


def normalize_180(deg: float) -> float:
    """Normalize an angle to the signed range [-180, 180) (180 maps to -180)."""
    return ((deg + 180.0) % 360.0) - 180.0


def _phase_for_elevation(elev_deg: float) -> str:
    """Classify a sun elevation: 'day' (>0), 'twilight' (0..-6), 'night' (<-6)."""
    if elev_deg > 0.0:
        return "day"
    if elev_deg >= -CIVIL_TWILIGHT_DEG:
        return "twilight"
    return "night"


def _interp_at(
    a: tuple[float, datetime, float],
    b: tuple[float, datetime, float],
    target_elev: float,
) -> tuple[float, datetime]:
    """Linear-interpolate (distance, time) where elevation crosses *target_elev*."""
    dist_a, time_a, elev_a = a
    dist_b, time_b, elev_b = b
    if elev_b == elev_a:
        return dist_a, time_a
    frac = (target_elev - elev_a) / (elev_b - elev_a)
    frac = max(0.0, min(1.0, frac))
    dist = dist_a + frac * (dist_b - dist_a)
    time = time_a + (time_b - time_a) * frac
    return dist, time


def _compute_night_intervals(
    samples: list[tuple[float, datetime, float]],
) -> list[NightInterval]:
    """Build contiguous twilight/night intervals with interpolated boundaries.

    *samples* is an ordered list of ``(distance_nm, time, elevation_deg)``.
    Day runs produce nothing; phase transitions (day/twilight at 0 deg,
    twilight/night at -6 deg) are interpolated for clean boundaries.
    """
    if len(samples) < 2:
        return []

    # Build phase "atoms": one (start_dist, start_time, end_dist, end_time, phase)
    # per sub-segment, splitting each adjacent pair at the 0 and -6 deg crossings.
    atoms: list[tuple[float, datetime, float, datetime, str]] = []
    for a, b in zip(samples, samples[1:]):
        dist_a, time_a, elev_a = a
        dist_b, time_b, elev_b = b

        # Crossing fractions for the two thresholds that lie strictly between
        # the endpoints' elevations.
        cuts: list[tuple[float, datetime]] = [(dist_a, time_a)]
        for thr in (0.0, -CIVIL_TWILIGHT_DEG):
            lo, hi = sorted((elev_a, elev_b))
            if lo < thr < hi:
                cuts.append(_interp_at(a, b, thr))
        cuts.append((dist_b, time_b))
        # Order the cut points along the segment (descending if the route
        # happens to run backwards in distance).
        cuts.sort(key=lambda c: c[0], reverse=(dist_b < dist_a))

        for c0, c1 in zip(cuts, cuts[1:]):
            mid_dist = (c0[0] + c1[0]) / 2.0
            # Elevation at the sub-segment midpoint (linear in distance).
            span = dist_b - dist_a
            frac = (mid_dist - dist_a) / span if span else 0.0
            mid_elev = elev_a + frac * (elev_b - elev_a)
            atoms.append((c0[0], c0[1], c1[0], c1[1], _phase_for_elevation(mid_elev)))

    # Merge consecutive same-phase atoms; drop day.
    intervals: list[NightInterval] = []
    for start_d, start_t, end_d, end_t, phase in atoms:
        if phase == "day":
            continue
        if intervals and intervals[-1].phase == phase and abs(
            intervals[-1].end_distance_nm - start_d
        ) < 1e-6:
            prev = intervals[-1]
            intervals[-1] = NightInterval(
                start_distance_nm=prev.start_distance_nm,
                end_distance_nm=end_d,
                start_time=prev.start_time,
                end_time=end_t,
                phase=phase,
            )
        else:
            intervals.append(NightInterval(
                start_distance_nm=start_d,
                end_distance_nm=end_d,
                start_time=start_t,
                end_time=end_t,
                phase=phase,
            ))
    return intervals


def _compute_sun_side(
    analyses: list[RoutePointAnalysis],
) -> SunSideSummary:
    """Aggregate which side the sun favours, weighting each segment by its length.

    Only daylit points (elevation > 0) contribute a side; night segments are
    skipped (no sun → no side).
    """
    left_nm = 0.0
    right_nm = 0.0
    # Per forward-segment side: "left" / "right" / None (no sun).
    seg_sides: list[tuple[float, float, str]] = []  # (start_nm, end_nm, side)

    for i in range(len(analyses) - 1):
        p = analyses[i]
        nxt = analyses[i + 1]
        seg_len = nxt.distance_from_origin_nm - p.distance_from_origin_nm
        if seg_len <= 0:
            continue
        elev = solar_elevation(p.lat, p.lon, p.interpolated_time)
        if elev <= 0.0:
            continue  # sun down → no side
        az = solar_azimuth(p.lat, p.lon, p.interpolated_time)
        rel = normalize_180(az - p.track_deg)
        if rel > 0:
            side = "right"
            right_nm += seg_len
        elif rel < 0:
            side = "left"
            left_nm += seg_len
        else:
            continue  # sun dead ahead/behind → no clear side
        seg_sides.append((p.distance_from_origin_nm, nxt.distance_from_origin_nm, side))

    sunlit_nm = left_nm + right_nm
    if sunlit_nm <= 0:
        return SunSideSummary(dominant_side="none", dominant_side_pct=0.0)

    if right_nm >= left_nm:
        dominant = "right"
        dominant_pct = round(100.0 * right_nm / sunlit_nm, 1)
    else:
        dominant = "left"
        dominant_pct = round(100.0 * left_nm / sunlit_nm, 1)

    # Merge consecutive same-side segments into runs.
    segments: list[SunSideSegment] = []
    for start_nm, end_nm, side in seg_sides:
        if segments and segments[-1].side == side and abs(
            segments[-1].end_distance_nm - start_nm
        ) < 1e-6:
            segments[-1] = SunSideSegment(
                side=side,
                start_distance_nm=segments[-1].start_distance_nm,
                end_distance_nm=end_nm,
            )
        else:
            segments.append(SunSideSegment(
                side=side, start_distance_nm=start_nm, end_distance_nm=end_nm,
            ))

    return SunSideSummary(
        dominant_side=dominant, dominant_side_pct=dominant_pct, segments=segments,
    )


def _consensus_best_runway(summary: AirportConditionsSummary) -> RunwayWind | None:
    """Pick a representative wind-best runway across the per-model conditions.

    The sun advisory is model-independent, so we collapse the per-model
    best-runway picks (which already use the shared selection logic) to the most
    common runway, falling back to the first available pick.
    """
    picks = [c.best_runway for c in summary.conditions if c.best_runway is not None]
    if not picks:
        return None
    counts = Counter(p.runway_id for p in picks)
    best_id = counts.most_common(1)[0][0]
    return next(p for p in picks if p.runway_id == best_id)


def _glare_for_airport(
    phase: str,
    point: RoutePointAnalysis,
    summary: AirportConditionsSummary | None,
    glare_azimuth_deg: float,
    glare_elev_max_deg: float,
) -> GlareAssessment:
    """Assess sun glare on the wind-best runway at one endpoint of the route.

    Uses the route endpoint's lat/lon/time for the solar position (the first/last
    route point sits at the departure/arrival airport).
    """
    icao = summary.icao if summary else (point.waypoint_icao or "")
    sun_el, sun_az = solar_position(point.lat, point.lon, point.interpolated_time)
    is_dark = sun_el <= 0.0

    best = _consensus_best_runway(summary) if summary else None
    if best is None:
        # No runway/wind data → glare unknown; advisory stays GREEN, note still shows.
        return GlareAssessment(
            phase=phase, airport_icao=icao,
            sun_azimuth_true=round(sun_az, 1), sun_elevation_deg=round(sun_el, 1),
            into_sun=False, is_dark=is_dark,
        )

    rel = normalize_180(sun_az - best.heading_deg)
    into_sun = (0.0 < sun_el <= glare_elev_max_deg) and (abs(rel) <= glare_azimuth_deg)
    return GlareAssessment(
        phase=phase,
        airport_icao=icao,
        runway_ident=best.runway_id,
        runway_heading_true=round(best.heading_deg, 1),
        sun_azimuth_true=round(sun_az, 1),
        sun_elevation_deg=round(sun_el, 1),
        relative_bearing_deg=round(rel, 1),
        into_sun=into_sun,
        is_dark=is_dark,
    )


def compute_route_sun(
    analyses: list[RoutePointAnalysis],
    departure: AirportConditionsSummary | None = None,
    arrival: AirportConditionsSummary | None = None,
    *,
    glare_azimuth_deg: float = 30.0,
    glare_elev_max_deg: float = 15.0,
) -> RouteSunAnalysis:
    """Compute night intervals, sun-side summary, and dep/arr glare for a route.

    ``night_intervals`` and ``sun_side`` need only the route-point geometry, so
    they are always computed. ``takeoff``/``landing`` glare additionally need the
    airport conditions (wind-best runway); pass ``departure``/``arrival`` to fill
    them, otherwise they stay ``None`` (advisory stays GREEN, note still shows).
    """
    if not analyses:
        return RouteSunAnalysis(sun_side=SunSideSummary(dominant_side="none", dominant_side_pct=0.0))

    # One solar_position call per route point feeds both the night intervals
    # (elevation crossings) and the per-point hover readout (azimuth + angle to
    # track). sun_side keeps its own pass — it only contributes daylit segments.
    positions = [solar_position(a.lat, a.lon, a.interpolated_time) for a in analyses]
    samples = [
        (a.distance_from_origin_nm, a.interpolated_time, elev)
        for a, (elev, _az) in zip(analyses, positions)
    ]
    points = [
        SunPoint(
            distance_nm=a.distance_from_origin_nm,
            elevation_deg=round(elev, 1),
            azimuth_deg=round(az, 1),
            relative_bearing_deg=round(normalize_180(az - a.track_deg), 1),
        )
        for a, (elev, az) in zip(analyses, positions)
    ]
    night_intervals = _compute_night_intervals(samples)
    sun_side = _compute_sun_side(analyses)

    takeoff = _glare_for_airport(
        "takeoff", analyses[0], departure, glare_azimuth_deg, glare_elev_max_deg,
    ) if departure is not None else None
    landing = _glare_for_airport(
        "landing", analyses[-1], arrival, glare_azimuth_deg, glare_elev_max_deg,
    ) if arrival is not None else None

    return RouteSunAnalysis(
        night_intervals=night_intervals,
        sun_side=sun_side,
        points=points,
        takeoff=takeoff,
        landing=landing,
    )
