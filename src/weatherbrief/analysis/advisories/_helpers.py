"""Shared utilities for advisory evaluators."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple

from collections.abc import Callable

from weatherbrief.models import (
    AdvisoryHighlights,
    AdvisoryStatus,
    ElevationProfile,
    HighlightRegion,
    HighlightSeverity,
    IcingZone,
    MitigationProfile,
    MitigationSegment,
    MitigationTransition,
    RibbonSegment,
)

if TYPE_CHECKING:
    from weatherbrief.analysis.advisories import RouteContext
    from weatherbrief.analysis.advisories.vertical_profile import CostModel, Profile
    from weatherbrief.models import RouteCrossSection, SoundingAnalysis


def build_cost_model(
    ctx: RouteContext,
    model: str,
    cell_cost: Callable[[SoundingAnalysis, float], float],
    floor_margin_ft: float,
) -> CostModel | None:
    """Assemble a ``(point × altitude)`` :class:`CostModel` for the shared solver (#335).

    The one place bin construction, per-point terrain-floor / ceiling walling, and
    floor-band start/end anchoring live — each advisory supplies only ``cell_cost``
    (its hazard→cost mapping) and the ``floor_margin_ft`` above terrain. Terrain is looked
    up once here via ``terrain_at_distance`` (linear interpolation) so every consumer of
    the shared solver computes the *same* floor for the same physical point. Returns None
    when no route point carries this model's sounding.
    """
    from weatherbrief.analysis.advisories.vertical_profile import (
        INF,
        MITIGATION_BIN_STEP_FT,
        CostModel,
        floor_bin,
        floor_reachable_bins,
    )

    cruise = ctx.cruise_altitude_ft
    ceiling = ctx.flight_ceiling_ft
    step = MITIGATION_BIN_STEP_FT
    top = max(int(ceiling), int(cruise))
    bins = list(range(step, top + step, step))

    points = [
        (rpa.distance_from_origin_nm or 0.0, rpa.sounding[model])
        for rpa in ctx.analyses
        if model in rpa.sounding
    ]
    points.sort(key=lambda t: t[0])
    if not points:
        return None

    distances = [d for d, _ in points]
    cost_field: list[list[float]] = []
    floors: list[float] = []
    for d, sounding in points:
        floor = (terrain_at_distance(ctx.elevation, d) or 0.0) + floor_margin_ft
        floors.append(floor)
        cost_field.append([
            INF if (alt < floor or alt > ceiling) else cell_cost(sounding, alt)
            for alt in bins
        ])

    start = floor_reachable_bins(cost_field[0], floor_bin(bins, floors[0]))
    end = floor_reachable_bins(cost_field[-1], floor_bin(bins, floors[-1]))
    return CostModel(
        cost_field=cost_field,
        distances_nm=distances,
        bin_altitudes_ft=bins,
        allowed_start_bins=start,
        allowed_end_bins=end,
    )


def to_mitigation_profile(profile: Profile) -> MitigationProfile:
    """Convert a solver :class:`Profile` into the storable :class:`MitigationProfile`.

    Shared by every advisory that derives mitigations from the vertical-profile solver
    (VFR feasibility, icing escape — issue #335), so the solver→model bridge lives once.
    """
    return MitigationProfile(
        segments=[
            MitigationSegment(dist_from_nm=s.dist_from_nm, dist_to_nm=s.dist_to_nm, altitude_ft=s.alt_ft)
            for s in profile.segments
        ],
        transitions=[
            MitigationTransition(
                from_nm=t.from_nm, to_nm=t.to_nm,
                from_altitude_ft=t.from_alt_ft, to_altitude_ft=t.to_alt_ft,
            )
            for t in profile.transitions
        ],
    )


def format_extent(
    affected: int,
    total: int,
    total_distance_nm: float,
) -> str:
    """Format affected/total as a distance string, e.g. '30nm/55nm (55%)'.

    Converts point counts to nautical miles using the actual route distance
    and number of analysis points. When there are too few points to compute
    spacing, falls back to the percentage only.
    """
    if total <= 0:
        return "0nm"
    affected_nm = round(total_distance_nm * affected / total)
    total_nm = round(total_distance_nm)
    pct = 100 * affected / total
    return f"{affected_nm}nm/{total_nm}nm ({pct:.0f}%)"


class FlaggedCell(NamedTuple):
    """A flagged route point's scrim geometry, for :func:`build_regions`.

    ``None`` base/top means a full column (the depth-unresolved convective
    ghost). Only flagged (AMBER/RED) points carry a ``FlaggedCell``; clean
    points pass ``None`` in :func:`build_regions`'s input.

    ``reason_code`` / ``metric_id`` / ``method_id`` are optional stable,
    non-localised provenance tokens (#393) carried onto the emitted
    :class:`HighlightRegion` — see that model. ``method_id`` participates in
    run-merging alongside ``kind`` + ``severity`` (it can vary point-to-point, so
    merging across it would mislabel provenance — #409); ``reason_code`` /
    ``metric_id`` do not, and the first cell of a run supplies them.
    """

    kind: str
    severity: HighlightSeverity
    base_ft: int | None
    top_ft: int | None
    reason_code: str | None = None
    metric_id: str | None = None
    method_id: str | None = None


def _cell_edges(distances: list[float], total_nm: float) -> tuple[list[float], list[float]]:
    """Per-point cell boundaries: each point owns ``[left, right]`` midway to its neighbours.

    First point's cell starts at 0, last point's ends at ``total_nm`` — so the
    per-point cells tile ``[0, total_nm]`` exactly. Boundaries fall midway
    between adjacent points, matching the ribbon/region convention.
    """
    n = len(distances)
    lefts = [
        0.0 if i == 0 else (distances[i - 1] + distances[i]) / 2.0
        for i in range(n)
    ]
    rights = [
        total_nm if i == n - 1 else (distances[i] + distances[i + 1]) / 2.0
        for i in range(n)
    ]
    return lefts, rights


def build_ribbon(
    per_point: list[tuple[float, HighlightSeverity]],
    total_nm: float,
) -> list[RibbonSegment]:
    """Merge per-point severities into a gapless 1-D route-verdict partition (#373).

    Input: ``(distance_from_origin_nm, severity)`` per route point, in route
    order (points with no sounding for the model → ``UNAVAILABLE``). Consecutive
    same-severity points merge into one run; run boundaries fall midway between
    adjacent points; the first run starts at 0 and the last ends at ``total_nm``.

    Invariants (guaranteed): segments are sorted, non-overlapping, gapless, and
    tile ``[0, total_nm]`` exactly. Returns ``[]`` for empty input.
    """
    if not per_point:
        return []
    pts = sorted(per_point, key=lambda t: t[0])
    distances = [d for d, _ in pts]
    severities = [s for _, s in pts]
    lefts, rights = _cell_edges(distances, total_nm)

    segments: list[RibbonSegment] = []
    run_start = 0
    n = len(pts)
    for i in range(1, n + 1):
        if i == n or severities[i] != severities[run_start]:
            segments.append(RibbonSegment(
                dist_from_nm=lefts[run_start],
                dist_to_nm=rights[i - 1],
                severity=severities[run_start],
            ))
            run_start = i
    return segments


def build_regions(
    per_point: list[tuple[float, FlaggedCell | None]],
    total_nm: float,
) -> list[HighlightRegion]:
    """Merge consecutive same-kind/severity flagged points into scrim cutouts (#373).

    Input mirrors :func:`build_ribbon` but the second tuple element is a
    :class:`FlaggedCell` on flagged points and ``None`` on clean ones. Adjacent
    flagged points sharing ``kind`` and ``severity`` merge into one region using
    the **envelope** (min ``base_ft`` / max ``top_ft`` across the run, ignoring
    ``None``); an all-``None`` run stays a full column. Region x-extent uses the
    same per-point cell boundaries as the ribbon.
    """
    if not per_point:
        return []
    pts = sorted(per_point, key=lambda t: t[0])
    distances = [d for d, _ in pts]
    cells = [c for _, c in pts]
    lefts, rights = _cell_edges(distances, total_nm)

    regions: list[HighlightRegion] = []
    n = len(pts)
    i = 0
    while i < n:
        cell = cells[i]
        if cell is None:
            i += 1
            continue
        j = i
        bases: list[int] = []
        tops: list[int] = []
        while (
            j < n
            and cells[j] is not None
            and cells[j].kind == cell.kind
            and cells[j].severity == cell.severity
            # method_id can genuinely vary point-to-point within one model (NWP
            # layers present at some route points, DD fallback at others), so it
            # joins the run key — merging across a method change would silently
            # mislabel the provenance of part of the region (#409 review). kind
            # and severity are constant within a run by construction; reason_code
            # and metric_id remain first-cell (dominant reason / target layer).
            and cells[j].method_id == cell.method_id
        ):
            if cells[j].base_ft is not None:
                bases.append(cells[j].base_ft)
            if cells[j].top_ft is not None:
                tops.append(cells[j].top_ft)
            j += 1
        regions.append(HighlightRegion(
            dist_from_nm=lefts[i],
            dist_to_nm=rights[j - 1],
            base_ft=min(bases) if bases else None,
            top_ft=max(tops) if tops else None,
            kind=cell.kind,
            severity=cell.severity,
            # Provenance tokens (#393) come from the first cell of the run — all
            # cells in a run share kind+severity and, by construction, the same
            # reason/metric/method.
            reason_code=cell.reason_code,
            metric_id=cell.metric_id,
            method_id=cell.method_id,
        ))
        i = j
    return regions


# Rank for worst-of merging of per-point highlight severities. UNAVAILABLE ranks
# lowest: it only enters a merge when a flagged verdict should override it (an
# airport-axis endpoint colour over a data gap) — never the other way round.
_SEVERITY_RANK = {
    HighlightSeverity.UNAVAILABLE: -1,
    HighlightSeverity.GREEN: 0,
    HighlightSeverity.AMBER: 1,
    HighlightSeverity.RED: 2,
}

_STATUS_TO_SEVERITY = {
    AdvisoryStatus.GREEN: HighlightSeverity.GREEN,
    AdvisoryStatus.AMBER: HighlightSeverity.AMBER,
    AdvisoryStatus.RED: HighlightSeverity.RED,
    AdvisoryStatus.UNAVAILABLE: HighlightSeverity.UNAVAILABLE,
}


def status_to_severity(status: AdvisoryStatus) -> HighlightSeverity:
    """Map a sub-axis :class:`AdvisoryStatus` onto the ribbon severity scale.

    Used by the composite evaluators (#375) to fold an airport-axis status into
    the endpoint ribbon segments.
    """
    return _STATUS_TO_SEVERITY[status]


def worst_severity(*severities: HighlightSeverity) -> HighlightSeverity:
    """Worst-of merge for per-point ribbon severities (composites, #375)."""
    return max(severities, key=lambda s: _SEVERITY_RANK[s])


def driving_method_id(
    highlights: AdvisoryHighlights | None,
    status: AdvisoryStatus,
) -> str | None:
    """The stamped ``method_id`` of the region that drove this model's grade (#408).

    The provenance rollup for :attr:`ModelAdvisoryResult.primary_method_id`: the
    analysis method a chip badges. Read from the very ``highlights`` the grade
    already produced — never a second pass over the route — so the badged method
    cannot drift from the geometry it came from (the #393 single-source
    principle).

    The driving region is normally the stamped one whose severity equals the
    grade. But several evaluators cap their per-point region severity below the
    grade the extent can reach — ``cloud_top`` decks are always AMBER yet ≥60%
    coverage grades RED; ``ifr_feasibility`` icing bands are AMBER yet
    ``icing_pct`` can grade RED; ``fiki_icing``'s clear-cruise fraction can grade
    RED off AMBER points. Requiring an exact severity match dropped the badge on
    exactly those RED grades — the worst case, where a pilot most needs to know
    the method (#409 review). So the lookup degrades in two steps:

    1. **Exact stamped match** — a stamped region at the grade severity drove it.
    2. **Escalated by extent** — no region reached the grade severity at all, so
       it escalated past every capped region by percentage; badge the
       method-bearing axis that produced those regions (its highest-severity
       stamped region). BUT if an *unstamped* region did reach the grade severity
       (the method-less convective ``tower`` in the IFR composite), that axis
       drove the grade — return None rather than misattribute it to a lesser
       method-bearing axis.

    Returns None for a GREEN/UNAVAILABLE grade (no regions), when only
    method-less regions exist (a non-method-controlled axis — the ``method_id``
    the contract reserves as "when one controlled it"), and in the unstamped-axis
    case above.
    """
    if highlights is None:
        return None
    target = _STATUS_TO_SEVERITY.get(status)
    if target is None:
        return None
    regions = highlights.regions

    # 1. A stamped region exactly at the grade severity is the driving region.
    exact = next(
        (r.method_id for r in regions
         if r.severity == target and r.method_id is not None),
        None,
    )
    if exact is not None:
        return exact

    # An unstamped region reached the grade severity → a method-less axis drove
    # it (e.g. the convective tower). Don't badge a lesser method-bearing axis.
    if any(r.severity == target for r in regions):
        return None

    # 2. No region reached the grade severity: it escalated past every capped
    # region by extent. Badge the method-bearing axis behind the highest region
    # *strictly below* the grade — the ones the percentage climbed over. A
    # would-be-flagged grade with no lower region (e.g. GREEN — no regions at
    # all) correctly yields no badge.
    target_rank = _SEVERITY_RANK[target]
    below = [
        r for r in regions
        if r.method_id is not None and _SEVERITY_RANK[r.severity] < target_rank
    ]
    if not below:
        return None
    # Ties (several method-bearing regions at the same severity below the grade)
    # break on list order, which is route order — arbitrary but deterministic.
    # Reachable only within a single method-bearing axis (composites resolve
    # their method per-axis before calling this), so a tie means the same axis
    # graded different route points on different effective methods; any is a fair
    # representative for the badge.
    return max(below, key=lambda r: _SEVERITY_RANK[r.severity]).method_id


def ribbon_peak(segments: list[RibbonSegment]) -> float | None:
    """Center of the longest RED run, else the longest AMBER run, else ``None``.

    A generic "worst point" pick for evaluators (e.g. ``vmc_cruise``) whose peak
    is defined purely by ribbon extent. Evaluators with a richer notion of worst
    (e.g. convective's highest-CAPE point) compute their own ``peak_dist_nm``.
    """
    for sev in (HighlightSeverity.RED, HighlightSeverity.AMBER):
        center: float | None = None
        best_len = -1.0
        for seg in segments:
            if seg.severity != sev:
                continue
            length = seg.dist_to_nm - seg.dist_from_nm
            if length > best_len:
                best_len = length
                center = (seg.dist_from_nm + seg.dist_to_nm) / 2.0
        if center is not None:
            return center
    return None


def icing_zones_in_altitude_range(
    zones: list[IcingZone],
    floor_ft: float,
    ceiling_ft: float,
) -> list[IcingZone]:
    """Return icing zones that overlap the altitude range [floor_ft, ceiling_ft]."""
    return [z for z in zones if z.top_ft > floor_ft and z.base_ft < ceiling_ft]


def apply_airport_endpoints(
    ribbon_points: list[tuple[float, HighlightSeverity]],
    dep_status: AdvisoryStatus,
    arr_status: AdvisoryStatus,
) -> None:
    """Worst-merge departure/arrival status into the first/last ribbon points, in place.

    The airport axis of a composite evaluator has no en-route extent: it colours only
    the endpoint segments. GREEN/UNAVAILABLE airport statuses leave the ribbon alone so
    a benign airport never overrides a flagged en-route point at the same distance.
    """
    if not ribbon_points:
        return
    first = min(range(len(ribbon_points)), key=lambda i: ribbon_points[i][0])
    last = max(range(len(ribbon_points)), key=lambda i: ribbon_points[i][0])
    for idx, ap_status in ((first, dep_status), (last, arr_status)):
        ap_sev = status_to_severity(ap_status)
        if ap_sev in (HighlightSeverity.AMBER, HighlightSeverity.RED):
            d, sev = ribbon_points[idx]
            ribbon_points[idx] = (d, worst_severity(sev, ap_sev))


def min_icing_clearance(
    zones: list[IcingZone],
    cruise_alt_ft: float,
) -> float:
    """Minimum vertical distance from cruise altitude to any icing zone.

    Returns ``float('inf')`` when no icing zones exist.
    """
    min_dist = float("inf")
    for zone in zones:
        if zone.base_ft <= cruise_alt_ft <= zone.top_ft:
            return 0.0
        elif cruise_alt_ft < zone.base_ft:
            min_dist = min(min_dist, zone.base_ft - cruise_alt_ft)
        else:
            min_dist = min(min_dist, cruise_alt_ft - zone.top_ft)
    return min_dist


# A clear (GREEN) verdict may only speak for the whole domain when at least this
# fraction of it was assessable. Below it, a clear subset of a mostly-unassessed
# domain is UNAVAILABLE — not GREEN (#391). Data-coverage tolerance (missing-data
# handling), NOT a meteorological threshold.
_MIN_ASSESSED_FRACTION = 0.5


def below_coverage(assessed: int, domain: int) -> bool:
    """True when a would-be-GREEN verdict rests on too small a share of its domain.

    ``assessed`` is the count of units the evaluator could actually grade;
    ``domain`` is the assessable universe it speaks for (route points for most
    evaluators, mountain points for ``mountain_wind``). Used to downgrade a
    would-be-GREEN to UNAVAILABLE when coverage is thin (#391) — a clear subset
    does not establish the unassessed remainder is clear. Only ever applied to a
    GREEN verdict; a flagged (AMBER/RED) verdict always stands, so real hazard
    evidence on partial coverage is never diluted. Returns False for an empty
    domain (``domain == 0`` is handled as UNAVAILABLE by the evaluators' own
    "nothing to assess" branch, not here).
    """
    return domain > 0 and assessed < domain * _MIN_ASSESSED_FRACTION


class EvidenceSample(NamedTuple):
    """One route point's evidence for one evaluator × one model (#393).

    The single per-point record that feeds **both** the grade (counts) and the
    highlight geometry (ribbon + regions). Emitting one list per model — instead
    of maintaining a ``total``/``affected`` counter loop *and* a separate
    ``ribbon_points``/``region_cells`` loop — is what keeps the verdict and the
    highlight from silently drifting: :func:`summarize_evidence` derives every
    downstream number from this one list.

    Fields:
        ``distance_nm`` — along-route position from origin.
        ``assessed`` — could this point actually be graded (does the model carry
            the data here)? ``False`` renders the ribbon UNAVAILABLE at this
            point and excludes it from the coverage numerator.
        ``severity`` — the per-point ribbon verdict (what the highlight shows).
            Unassessed points should carry ``UNAVAILABLE``; points an evaluator's
            relevance filter skips (above cruise, non-mountain) carry ``GREEN``.
        ``affected`` — whether this point counts toward the grade's ``affected``
            total. ``None`` (the common case) derives it as
            ``severity in {AMBER, RED}``. A few evaluators pass it explicitly
            because their ribbon and grade key on *different* predicates — e.g.
            turbulence colours the ribbon RED for SEVERE CAT anywhere in the
            column while the grade keys on the cruise band, and FIKI flags a
            corridor cutout while the grade keys on cruise clear-air. Passing it
            here keeps ``affected_nm`` consistent with ``affected_pct`` (both
            count the same points) without forcing the ribbon to match.
        ``in_domain`` — is this point part of the coverage *domain* (the
            denominator :func:`below_coverage` measures against)? Default True.
            ``mountain_wind`` sets it ``False`` for non-mountain points so
            coverage is measured over mountain points only — a genuinely flat
            route still grades rather than going UNAVAILABLE.
        ``region`` — the scrim cutout (:class:`FlaggedCell`) for a flagged point,
            or ``None``. Carries the region kind/altitude band plus the #393
            provenance tokens.
    """

    distance_nm: float
    assessed: bool
    severity: HighlightSeverity
    affected: bool | None = None
    in_domain: bool = True
    region: FlaggedCell | None = None


def _sample_affected(sample: EvidenceSample) -> bool:
    """Whether a sample counts toward the grade's ``affected`` total."""
    if sample.affected is not None:
        return sample.affected
    return sample.severity in (HighlightSeverity.AMBER, HighlightSeverity.RED)


class EvidenceSummary(NamedTuple):
    """Everything :func:`summarize_evidence` derives from one evidence list (#393).

    ``assessed`` is the grade denominator (``ModelAdvisoryResult.build``'s
    ``total``); ``affected`` its numerator. ``domain`` is the coverage universe.
    ``affected_nm`` is the geometry-accurate extent (midpoint-owned cells of the
    affected points) to pass to ``build``. ``highlights`` is the ribbon + scrim
    geometry. ``data_state`` is complete / partial / unavailable.
    """

    affected: int
    assessed: int
    domain: int
    affected_nm: float
    highlights: AdvisoryHighlights
    data_state: str  # "complete" | "partial" | "unavailable"

    @property
    def below_coverage(self) -> bool:
        """True when a would-be-GREEN verdict rests on too small a share of the domain.

        The same predicate the evaluators call directly today
        (``below_coverage(total, domain)``) — apply it only to a GREEN status, so
        a flagged verdict on thin coverage is never diluted.
        """
        return below_coverage(self.assessed, self.domain)


def summarize_evidence(
    samples: list[EvidenceSample],
    total_nm: float,
    *,
    peak_dist_nm: float | None = None,
) -> EvidenceSummary:
    """Derive grade counts, geometry-accurate extent, highlights and coverage (#393).

    One shared reduction over the evaluator's per-point :class:`EvidenceSample`
    list. Because the counts and the highlight both come from this single list,
    the grade and the ribbon cannot disagree, and ``affected_nm`` — the
    midpoint-owned-cell distance of the *affected* points — stays consistent with
    ``affected_pct`` (both count the same points). This is the #391 geometry fix,
    landed here where it belongs rather than as a second pass over the route.

    ``peak_dist_nm`` overrides the highlight's jump-to-worst point; when ``None``
    it defaults to :func:`ribbon_peak` (longest red run, else amber). The caller
    decides whether to attach ``.highlights`` to the result (evaluators gate on
    "the model has data").
    """
    pts = sorted(samples, key=lambda s: s.distance_nm)
    distances = [s.distance_nm for s in pts]

    domain = sum(1 for s in pts if s.in_domain)
    assessed = sum(1 for s in pts if s.assessed and s.in_domain)
    affected = sum(1 for s in pts if _sample_affected(s))

    # Geometry-accurate extent: each affected point owns the interval to the
    # midpoints of its neighbours; union (here, sum — cells are disjoint by
    # construction) the qualifying intervals. Falls out of the same cell edges
    # the ribbon uses, so extent and ribbon share one geometry.
    affected_nm = 0.0
    if pts:
        lefts, rights = _cell_edges(distances, total_nm)
        affected_nm = sum(
            rights[i] - lefts[i] for i, s in enumerate(pts) if _sample_affected(s)
        )

    ribbon = build_ribbon([(s.distance_nm, s.severity) for s in pts], total_nm)
    highlights = AdvisoryHighlights(
        ribbon=ribbon,
        regions=build_regions([(s.distance_nm, s.region) for s in pts], total_nm),
        peak_dist_nm=peak_dist_nm if peak_dist_nm is not None else ribbon_peak(ribbon),
    )

    if domain == 0 or assessed == 0:
        data_state = "unavailable"
    elif below_coverage(assessed, domain):
        data_state = "partial"
    else:
        data_state = "complete"

    return EvidenceSummary(
        affected=affected,
        assessed=assessed,
        domain=domain,
        affected_nm=round(affected_nm, 1),
        highlights=highlights,
        data_state=data_state,
    )


def pct_above_threshold(
    affected: int,
    total: int,
    amber_pct: float,
    red_pct: float | None = None,
) -> AdvisoryStatus:
    """Common pattern: GREEN below amber threshold, AMBER between, RED above red threshold."""
    if total == 0:
        return AdvisoryStatus.GREEN
    pct = 100.0 * affected / total
    if red_pct is not None and pct >= red_pct:
        return AdvisoryStatus.RED
    if pct >= amber_pct:
        return AdvisoryStatus.AMBER
    return AdvisoryStatus.GREEN


def terrain_at_distance(
    elevation: ElevationProfile | None,
    distance_nm: float,
) -> float | None:
    """Interpolate terrain elevation at a given distance along the route.

    Returns elevation in feet, or None if no profile available.
    """
    if elevation is None or not elevation.points:
        return None

    points = elevation.points

    # Clamp to range
    if distance_nm <= points[0].distance_nm:
        return points[0].elevation_ft
    if distance_nm >= points[-1].distance_nm:
        return points[-1].elevation_ft

    # Binary search for bracketing points
    lo, hi = 0, len(points) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if points[mid].distance_nm <= distance_nm:
            lo = mid
        else:
            hi = mid

    # Linear interpolation
    p0, p1 = points[lo], points[hi]
    if p1.distance_nm == p0.distance_nm:
        return p0.elevation_ft
    frac = (distance_nm - p0.distance_nm) / (p1.distance_nm - p0.distance_nm)
    return p0.elevation_ft + frac * (p1.elevation_ft - p0.elevation_ft)


def max_terrain_near_point(
    elevation: ElevationProfile | None,
    distance_nm: float,
    radius_nm: float = 5.0,
) -> float | None:
    """Find maximum terrain elevation within radius of a distance along the route."""
    if elevation is None or not elevation.points:
        return None

    max_elev = None
    for pt in elevation.points:
        if abs(pt.distance_nm - distance_nm) <= radius_nm:
            if max_elev is None or pt.elevation_ft > max_elev:
                max_elev = pt.elevation_ft
    return max_elev


def showers_at_point(
    cross_sections: list[RouteCrossSection],
    model: str,
    point_index: int,
    target_time: datetime,
) -> float | None:
    """Convective precipitation (showers, mm) at a route point for one model.

    ``showers`` is Open-Meteo's convective-only precip and is available for
    every model — the uniform realized-convection signal used by the convective
    character advisory (issue #294). Picks the hourly nearest ``target_time``.
    Returns None when unavailable.
    """
    for cs in cross_sections:
        if cs.model.value != model:
            continue
        if point_index >= len(cs.point_forecasts):
            return None
        hourly = cs.point_forecasts[point_index].at_time(target_time)
        if hourly is None:
            return None
        return hourly.showers_mm
    return None


def wind_at_altitude(
    cross_sections: list[RouteCrossSection],
    model: str,
    point_index: int,
    target_alt_ft: float,
    target_time: datetime,
) -> tuple[float, float] | None:
    """Find wind speed/direction at nearest pressure level to target altitude.

    Picks the hourly forecast nearest to *target_time* (rather than the
    first hour available, which can lag the route point's actual valid
    time on multi-hour flights). Returns (speed_kt, direction_deg) or
    None if unavailable.
    """
    from weatherbrief.analysis.wind import pick_wind_at_pressure
    from weatherbrief.models import altitude_to_pressure_hpa

    target_pressure = altitude_to_pressure_hpa(int(target_alt_ft))

    for cs in cross_sections:
        if cs.model.value != model:
            continue
        if point_index >= len(cs.point_forecasts):
            return None

        wf = cs.point_forecasts[point_index]
        hourly = wf.at_time(target_time)
        if hourly is None:
            return None

        best_level = pick_wind_at_pressure(hourly, target_pressure)
        if best_level is not None and best_level.wind_speed_kt is not None and best_level.wind_direction_deg is not None:
            return (best_level.wind_speed_kt, best_level.wind_direction_deg)

    return None
