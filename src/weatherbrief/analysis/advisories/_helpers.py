"""Shared utilities for advisory evaluators."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple

from collections.abc import Callable, Sequence

from weatherbrief.analysis.route_geometry import (
    EMPTY_EXTENT,
    RouteExtent,
    cell_edges,
    route_extent,
)
from weatherbrief.models import (
    AdvisoryHighlights,
    AdvisoryStatus,
    ElevationProfile,
    HighlightRegion,
    HighlightSeverity,
    IcingRisk,
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


def format_extent(ext: RouteExtent, *, domain_label: str | None = None) -> str:
    """Format a :class:`RouteExtent` as '30nm/55nm (55%)'.

    Takes the extent, never counts — that is what keeps the sentence and the
    structured ``affected_nm`` the same number rather than two derivations of it
    (#571 D2). ``domain_label`` names a denominator that is *not* the whole
    route ("of high terrain"), which the domain-scoped evaluators must pass:
    "543nm/582nm" for a mountain-point fraction was the ~4× overstatement.
    """
    if ext.domain_nm <= 0:
        return "0nm"
    label = f" {domain_label}" if domain_label else ""
    return f"{round(ext.nm)}nm/{round(ext.domain_nm)}nm{label} ({ext.pct:.0f}%)"


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
    lefts, rights = cell_edges(distances, total_nm)

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
    lefts, rights = cell_edges(distances, total_nm)

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
    """The effective analysis method behind a flagged grade's evidence (#408).

    Sources :attr:`ModelAdvisoryResult.primary_method_id` — the method a chip
    badges — from the very ``highlights`` the grade produced, so the badge cannot
    drift from the geometry it came from (the #393 single-source principle).

    **Every caller passes the regions of a SINGLE method-bearing axis.** The four
    single-axis evaluators (``vmc_cruise``, ``cloud_top``, ``icing_escape``,
    ``fiki_icing``) each emit one region kind; the ``ifr_feasibility`` composite
    resolves its method per-axis and passes only its icing regions (#409). Within
    one axis every flagged region carries that axis's effective method, so the
    badge is simply the method of the flagged evidence — there is no need to
    match a region's severity to the grade's. An earlier version did match, and
    leaked an edge case in *each* direction: a grade escalating past capped-low
    regions by extent (``cloud_top`` ≥60% AMBER decks → RED), and a grade landing
    *below* the only regions present (``vmc_cruise`` sub-red OVC → AMBER off
    RED-severity regions). Dropping the severity match removes the whole class.

    The **highest-severity stamped region** is the representative — the one that
    most drove the grade when a model graded different route points on different
    effective methods (cloud NWP at some points, DD fallback at others).

    Returns None for an unflagged grade (GREEN/UNAVAILABLE — the badge explains a
    concern; isolated sub-threshold regions under a GREEN grade are not one), and
    when no region carries a method (a DD no-swap axis, or a non-method
    evaluator — the ``method_id`` the contract reserves for "when one controlled
    it").
    """
    if highlights is None:
        return None
    if status not in (AdvisoryStatus.AMBER, AdvisoryStatus.RED):
        return None
    stamped = [r for r in highlights.regions if r.method_id is not None]
    if not stamped:
        return None
    # Ties (same-severity stamped regions on different effective methods within
    # the one axis) break on route order — arbitrary but deterministic; any is a
    # fair representative for a single badge.
    return max(stamped, key=lambda r: _SEVERITY_RANK[r.severity]).method_id


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


def hazardous_icing_zones(zones: list[IcingZone]) -> list[IcingZone]:
    """Return only the zones that represent icing a pilot would meet.

    Zone *existence* is not a hazard predicate. The Ogimet methods emit a zone
    wherever cloud sits in the icing temperature band and stamp it with the
    computed index, so ``risk == NONE`` there means "assessed here, index below
    the LIGHT threshold" — the method's way of saying *no icing*, not a hazard.
    (SFIP is the opposite: it only ever emits zones at LIGHT or worse, which is
    why grading on bare existence looked correct for as long as DD/SFIP were the
    only sources.) Grading on existence turns every assessed-but-clean Ogimet
    point into an icing hit, and the cross-section — which filters ``none`` out
    of every icing layer — then draws nothing where the advisory claims icing.

    ``sld_risk`` survives a NONE risk — but note that today this branch cannot
    fire. NOTHING populates :attr:`IcingZone.sld_risk`: ``icing._build_zone_simple``
    (the only builder behind the Ogimet-DD/NWP and IENG zones) never passes it,
    and the SFIP→IcingZone conversion in ``tasks/advise._resolve_analyses`` has
    nothing to pass — :class:`SfipZone` has no such field. The real SLD detector
    output lives in a separate ``sounding.sld_zones`` list that no advisory
    evaluator reads. So the clause is a *forward contract*, not an observed path,
    and this helper is behaviourally ``risk != NONE`` on real data.

    It is kept deliberately rather than simplified away. Supercooled large
    droplets are a hazard in their own right regardless of the index, so if
    ``sld_risk`` is ever wired up, the safe default must already be "survives the
    filter" — dropping the clause would make SLD-bearing NONE-risk zones start
    disappearing silently. It also keeps this predicate identical to
    ``icing_escape._icing_cell_cost``'s (``risk == NONE and not sld_risk`` →
    skip), which carries the same dormant clause for the same reason; the grading
    paths had drifted from that predicate and this helper reunites them.

    The predicate itself lives on :attr:`IcingZone.is_hazardous` so non-advisory
    consumers (the digest text and LLM prompt) can apply it without importing
    from this private module.
    """
    return [z for z in zones if z.is_hazardous]


def icing_zones_in_altitude_range(
    zones: list[IcingZone],
    floor_ft: float,
    ceiling_ft: float,
) -> list[IcingZone]:
    """Return icing zones that overlap the altitude range [floor_ft, ceiling_ft].

    Altitude-only: pass the zones through :func:`hazardous_icing_zones` first
    when the result drives a grade.
    """
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
        ``tags`` — free-form markers for *sub-populations* this point belongs to
            (#571). An evaluator whose message names a narrower population than
            the one that produced the grade — turbulence's SEVERE tier,
            model_agreement's moderate-only points — tags them here and asks
            :meth:`EvidenceSummary.extent_of` for that population's own extent.
            Reducing the predicate over the same ``cell_edges`` is what keeps the
            severity word and the extent beside it describing the same points;
            the alternative (scaling the whole-population nm proportionally) is
            the defect this replaces.
    """

    distance_nm: float
    assessed: bool
    severity: HighlightSeverity
    affected: bool | None = None
    in_domain: bool = True
    region: FlaggedCell | None = None
    tags: frozenset[str] = frozenset()


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

    ``extent`` is the same measurement as a :class:`RouteExtent` — what messages
    format and what a gate reads — and ``extent_of`` reduces any sub-population
    predicate over the *same* geometry (#571).
"""

    affected: int
    assessed: int
    domain: int
    affected_nm: float
    highlights: AdvisoryHighlights
    data_state: str  # "complete" | "partial" | "unavailable"
    # Geometry the extents are reduced over. Defaulted so an old-style
    # positional construction still works; ``summarize_evidence`` always sets it.
    domain_nm: float = 0.0
    samples: tuple[EvidenceSample, ...] = ()
    total_nm: float = 0.0

    @property
    def below_coverage(self) -> bool:
        """True when a would-be-GREEN verdict rests on too small a share of the domain.

        The same predicate the evaluators call directly today
        (``below_coverage(total, domain)``) — apply it only to a GREEN status, so
        a flagged verdict on thin coverage is never diluted.
        """
        return below_coverage(self.assessed, self.domain)

    @property
    def extent(self) -> RouteExtent:
        """The affected population's extent — what the message must format."""
        return self.extent_of(_sample_affected)

    def extent_of(
        self, predicate: Callable[[EvidenceSample], bool]
    ) -> RouteExtent:
        """Extent of an arbitrary sub-population, over the same route geometry.

        For evaluators whose message names a narrower population than the grade
        (turbulence's SEVERE tier, enroute_precip's snow split, vmc_cruise's
        OVC): reduce the predicate here rather than scaling the whole-population
        nm proportionally, which is what made the printed number disagree with
        the object it shipped in (#571 D1/D2).
        """
        return route_extent(
            [s.distance_nm for s in self.samples],
            self.total_nm,
            [bool(predicate(s)) for s in self.samples],
            [s.in_domain and s.assessed for s in self.samples],
        )


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
    # the ribbon uses, so extent and ribbon share one geometry. ``domain_nm``
    # comes out of the same reduction, so a domain-scoped evaluator carries its
    # own denominator instead of borrowing the route's (#571 D3).
    extent = route_extent(
        distances,
        total_nm,
        [_sample_affected(s) for s in pts],
        # The denominator is what this model could grade: in-domain AND
        # assessed. Counting unassessable points would dilute a real signal
        # exactly the way #391 exists to prevent, and ``domain_nm`` still equals
        # the route length whenever the model resolved the whole route.
        [s.in_domain and s.assessed for s in pts],
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
        affected_nm=extent.nm,
        highlights=highlights,
        data_state=data_state,
        domain_nm=extent.domain_nm,
        samples=tuple(pts),
        total_nm=total_nm,
    )


# The shared minimum-extent floor for a coverage-driven promotion (#571 D4).
# 30 nm is "about three points" at ``interpolate_route``'s fixed 10 nm spacing,
# expressed in the unit that survives a change of route length or spacing.
#
# Why a floor at all: the point count scales with distance, so the *weight of one
# point* scales inversely. On a 582 nm route one point is 1.6% — harmless against
# a 20% gate. On a 120 nm route (~13 points) one point is 7.7% and two are 15%,
# which clears ``imc_pct_amber``/``no_escape_pct_red`` outright. The percentage
# gate was silently ~5x more sensitive on a short flight than a long one — and a
# short flight is exactly where a 20 nm band of weather is most avoidable.
EXTENT_MIN_NM = 30.0

# A floor can never exceed half the domain it measures. Without this a route
# shorter than the floor itself would grade GREEN however completely the hazard
# covered it — the false-GREEN failure mode (#391) reintroduced through the back
# door. At this cap any coverage of half the domain or more always reaches the
# percentage gate, so the floor only ever bites the partial-coverage case it was
# written for; on a 582 nm route it is inert (30 nm is 5%), on a 120 nm route it
# raises the effective bar to 25%, which is the intended tightening.
_EXTENT_MIN_NM_DOMAIN_CAP = 0.5


def effective_min_nm(ext: RouteExtent, min_nm: float) -> float:
    """The minimum-extent floor actually applied to ``ext`` — see :data:`EXTENT_MIN_NM`."""
    return min(min_nm, _EXTENT_MIN_NM_DOMAIN_CAP * ext.domain_nm)


def grade_extent(
    ext: RouteExtent,
    *,
    amber_pct: float,
    red_pct: float | None = None,
    min_nm: float = EXTENT_MIN_NM,
    min_run_nm: float | None = None,
    min_minutes: float | None = None,
) -> AdvisoryStatus:
    """The single coverage gate: GREEN / AMBER / RED from one :class:`RouteExtent`.

    Replaces ``pct_above_threshold``'s point ratio (#571). Two differences that
    matter:

    - **The percentage is distance-based** (``ext.pct``), so an unevenly spaced
      route no longer grades differently from an evenly spaced one carrying the
      same weather, and the number that decided the colour is the number the
      message prints.
    - **A minimum-extent floor** (``min_nm``, see :data:`EXTENT_MIN_NM`) must be
      cleared before coverage may promote anything. Deliberate severe-hazard
      bypasses live in the evaluators and are unaffected — turbulence's
      free-atmosphere severe rule still forces RED off a single point — but a
      bypassed grade must then describe itself honestly rather than borrow a
      coverage number.

    ``min_run_nm`` gates on the longest *contiguous* run instead of the union,
    for barrier-type hazards ("you cannot get around it" — the EMBEDDED case).
    ``min_minutes`` is accepted for symmetry and is opt-in only; see the time
    axis in ``designs/meteorology-decisions.md`` §27 for why it does not gate by
    default.
    """
    if ext.domain_nm <= 0 or ext.nm <= 0:
        return AdvisoryStatus.GREEN
    if min_nm and ext.nm < effective_min_nm(ext, min_nm):
        return AdvisoryStatus.GREEN
    if min_run_nm is not None and ext.longest_run_nm < min_run_nm:
        return AdvisoryStatus.GREEN
    if min_minutes is not None and (ext.minutes is None or ext.minutes < min_minutes):
        return AdvisoryStatus.GREEN

    pct = ext.pct
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
