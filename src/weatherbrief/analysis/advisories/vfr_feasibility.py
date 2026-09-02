"""VFR feasibility advisory — overall VFR flight viability assessment.

Composite advisory combining airport conditions, en-route cloud clearance,
VMC compliance, climb-out/descent corridor decks, and en-route precipitation
into a single go/no-go style assessment for VFR flights.

The precipitation axis (shared classifier with the en-route precipitation
advisory) is capped at AMBER here: a pilot VMC-on-top is not directly
affected by surface rain below, but widespread snow or heavy rain degrades
visibility and every descent/divert option, which deserves a composite
caution. The standalone advisory still grades it fully (snow can RED).

The convective axis follows §22 — the colour is taken verbatim from
``grade_convective_model``, the same call the Convective Activity card grades
with — and is then read through ``convective_character``, which is the axis
built to answer "can a VFR pilot operate *around* this?". Until this was wired
the composite had no convective input at all, so a route with HIGH convection
over 47% of its length published VFR Feasibility GREEN beside IFR Feasibility
RED: the §22 divergence, in the one composite whose name promises the VFR
answer. Character is re-derived at whatever altitude the composite is evaluated
at, so the altitude table sweeps it for free — which is how a pilot sees that
the flight is infeasible at the filed level and available beneath the cells.
"""

from __future__ import annotations

from collections.abc import Iterator

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    EXTENT_MIN_NM,
    extent_min_nm_param,
    FlaggedCell,
    apply_airport_endpoints,
    below_coverage,
    build_cost_model,
    build_regions,
    build_ribbon,
    driving_method_id,
    RouteExtent,
    format_extent,
    grade_extent,
    route_extent,
    ribbon_peak,
    to_mitigation_profile,
    worst_severity,
)
from weatherbrief.analysis.advisories.convective_character import (
    CHARACTER_STATUS,
    below_base_escapes,
    classify_route_character,
    resolve_character_params,
)
from weatherbrief.analysis.advisories.convective_grading import (
    ConvectiveModelGrade,
    grade_convective_model,
    resolve_convective_params,
)
from weatherbrief.analysis.advisories.enroute_precip import (
    classify_enroute_precip,
    classify_precip_point,
    precip_point_severity,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.analysis.advisories.vertical_profile import (
    INF,
    MITIGATION_BIN_STEP_FT,
    Blockage,
    CostModel,
    Profile,
    solve,
)
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryHighlights,
    AdvisoryParameterDef,
    AdvisoryStatus,
    CloudCoverage,
    ConvectiveCharacter,
    HighlightSeverity,
    Mitigation,
    MitigationKind,
    MitigationProfile,
    MitigationSegment,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)
from weatherbrief.models.airport_conditions import FlightCategory


#: Character bands that justify softening the convective colour, and how far.
#:
#: Deliberately NOT ``CHARACTER_STATUS``: that map is the character *card's own
#: grade*, where NONE/UNKNOWN → GREEN is correct ("nothing to characterise").
#: Reused as a cap it silently converts "no answer" into "it's fine" — the #391
#: false-GREEN, and the very §22 divergence this axis exists to close. A LOW-risk
#: route is the live case: the character axis floors at ``min_risk`` MODERATE
#: while grading floors at LOW, so an AMBER convective card yields character NONE
#: and the axis softened to GREEN. Cells can also clear the risk floor with no
#: precip/cover signal to realize them (seen on UKMO/MétéoFrance beside a RED
#: peak-HIGH), reaching NONE the same way.
#:
#: Only the two genuinely circumnavigable bands appear here. Every other band —
#: the RED-mapped ones, NONE, UNKNOWN, and a model with no soundings — leaves the
#: convective colour exactly as the Convective Activity card graded it.
_CIRCUMNAVIGABLE_CAP: dict[ConvectiveCharacter, AdvisoryStatus] = {
    ConvectiveCharacter.ISOLATED: AdvisoryStatus.AMBER,
    ConvectiveCharacter.SCATTERED: AdvisoryStatus.AMBER,
}

#: Bands that positively establish the convection is NOT flyable around, as
#: opposed to bands that establish nothing. Only these earn the
#: "not circumnavigable VFR" sentence; the silent bands get a sentence that
#: claims neither.
_NOT_CIRCUMNAVIGABLE_BANDS: frozenset[ConvectiveCharacter] = frozenset({
    ConvectiveCharacter.WIDESPREAD,
    ConvectiveCharacter.ORGANIZED,
    ConvectiveCharacter.EMBEDDED,
})


#: Stable machine tag for the below-base escape tip. Distinct from the character
#: card's ``embedded_deck``: that one restores see-and-avoid for an EMBEDDED
#: deck in either direction, this one answers "could we fly this VFR underneath
#: the cells at all", and only downward.
CONV_MITIGATION_ADDRESSES = "convective_below_base"


def _least_severe(a: AdvisoryStatus, b: AdvisoryStatus) -> AdvisoryStatus:
    """The calmer of two statuses — the convective cap's one direction.

    Used so the character band can only ever soften the activity colour it
    qualifies. Expressed as "take the calmer" rather than a RED→AMBER special
    case so a future band mapping cannot accidentally escalate through it.
    """
    return b if AdvisoryStatus.worst([a, b]) == a else a


def _worst_status(*statuses: AdvisoryStatus) -> AdvisoryStatus:
    """Return the most severe status from the given values."""
    return AdvisoryStatus.worst(list(statuses))


def _check_airport_vfr(
    ctx: RouteContext,
    model: str,
) -> tuple[AdvisoryStatus, str, AdvisoryStatus, AdvisoryStatus]:
    """Check departure and arrival airport conditions for VFR feasibility.

    Uses the flight category (VFR/MVFR/IFR/LIFR) which already encodes
    ceiling and visibility thresholds per aviation standards.

    Returns (status, detail_fragment, dep_status, arr_status). The per-airport
    statuses colour the endpoint ribbon segments of the highlight (#375).

    Missing airport data is UNAVAILABLE, never a hardcoded clear GREEN (#391):
    the airport axis then simply doesn't contribute to the composite's ``worst``
    aggregate (which ignores UNAVAILABLE) instead of vouching for airports it
    never saw.
    """
    if ctx.airport_conditions is None:
        return (
            AdvisoryStatus.UNAVAILABLE, "",
            AdvisoryStatus.UNAVAILABLE, AdvisoryStatus.UNAVAILABLE,
        )

    dep = ctx.airport_conditions.departure
    arr = ctx.airport_conditions.arrival
    dep_cond = dep.condition_for_model(model)
    arr_cond = arr.condition_for_model(model)

    parts: list[str] = []
    per_airport: list[AdvisoryStatus] = []

    loc = ctx.locale
    for label_key, icao, cond in [("airport.dep", dep.icao, dep_cond), ("airport.arr", arr.icao, arr_cond)]:
        if cond is None:
            # No condition for this model at this airport — unassessable, not clear.
            per_airport.append(AdvisoryStatus.UNAVAILABLE)
            continue
        status = AdvisoryStatus.GREEN
        label = adv_t(label_key, loc)
        cat = cond.flight_category
        if cat in (FlightCategory.IFR, FlightCategory.LIFR):
            status = AdvisoryStatus.RED
            parts.append(f"{label} {icao} {cat.value}")
        elif cat == FlightCategory.MVFR:
            status = AdvisoryStatus.AMBER
            parts.append(f"{label} {icao} MVFR")
        per_airport.append(status)

    detail = " | ".join(parts) if parts else ""
    worst = _worst_status(*per_airport)
    return worst, detail, per_airport[0], per_airport[1]


def _point_enroute_vfr(
    sounding,
    altitude_ft: float,
    cloud_clearance_ft: float,
) -> tuple[str, float | None, float | None]:
    """Classify one point's en-route cloud clearance at ``altitude_ft``.

    Returns ``(cls, env_base_ft, env_top_ft)`` where ``cls`` is ``"imc"``
    (inside a BKN/OVC layer), ``"marginal"`` (clearance < threshold), or
    ``"clear"``; the envelope covers the BKN/OVC layer(s) containing the
    altitude (``None`` unless in cloud). Single source of the per-point
    classification, shared by :func:`_check_enroute_vfr` (grade) and the
    highlight geometry (#375) so the two cannot drift.
    """
    in_cloud = False
    marginal = False
    env_base: float | None = None
    env_top: float | None = None

    for cl in sounding.cloud_layers:
        if cl.coverage not in (CloudCoverage.BKN, CloudCoverage.OVC):
            continue
        # Check if the altitude is inside the cloud layer
        if cl.base_ft <= altitude_ft <= cl.top_ft:
            in_cloud = True
            env_base = cl.base_ft if env_base is None else min(env_base, cl.base_ft)
            env_top = cl.top_ft if env_top is None else max(env_top, cl.top_ft)
            continue
        # Check vertical clearance from cloud base or top
        min_dist = min(abs(altitude_ft - cl.base_ft), abs(altitude_ft - cl.top_ft))
        if min_dist < cloud_clearance_ft:
            marginal = True

    if in_cloud:
        return "imc", env_base, env_top
    return ("marginal" if marginal else "clear"), None, None


def _check_enroute_vfr(
    ctx: RouteContext,
    model: str,
    cloud_clearance_ft: float,
    altitude_ft: float,
) -> tuple[int, int, int, int, RouteExtent, RouteExtent]:
    """Check en-route cloud clearance for VFR at ``altitude_ft``.

    Takes the evaluated altitude explicitly (rather than reading
    ``ctx.cruise_altitude_ft``) so it can be re-run at candidate altitudes when
    searching for a lower-altitude mitigation.

    Returns (total, imc_count, marginal_count, clear_count, imc_extent, extent).
    - imc_count: points where the altitude is inside BKN/OVC cloud
    - marginal_count: points where cloud clearance < threshold (but not in cloud)
    - imc_extent: geometry-accurate coverage of the in-cloud points — the RED
      axis grades on this population, so it measures it (#571)
    - extent: the same for the imc+marginal union, which the AMBER axis grades on
      and the sentence prints
    """
    total = 0
    imc_count = 0
    marginal_count = 0
    clear_count = 0
    dists: list[float] = []
    assessed_flags: list[bool] = []
    imc_flags: list[bool] = []
    affected_flags: list[bool] = []

    for rpa in ctx.analyses:
        dists.append(rpa.distance_from_origin_nm or 0.0)
        assessed_flags.append(False)
        imc_flags.append(False)
        affected_flags.append(False)
        sounding = rpa.sounding.get(model)
        if sounding is None:
            continue
        total += 1
        assessed_flags[-1] = True

        cls, _, _ = _point_enroute_vfr(sounding, altitude_ft, cloud_clearance_ft)
        if cls == "imc":
            imc_count += 1
            imc_flags[-1] = True
            affected_flags[-1] = True
        elif cls == "marginal":
            marginal_count += 1
            affected_flags[-1] = True
        else:
            clear_count += 1

    speed_kt = ctx.cruise_groundspeed_kt
    imc_extent = route_extent(
        dists, ctx.total_distance_nm, imc_flags, assessed_flags,
        speed_kt=speed_kt,
    )
    extent = route_extent(
        dists, ctx.total_distance_nm, affected_flags, assessed_flags,
        speed_kt=speed_kt,
    )
    return total, imc_count, marginal_count, clear_count, imc_extent, extent


def _enroute_vfr_status(
    total: int,
    imc_extent: RouteExtent,
    affected_extent: RouteExtent,
    extent_pct_amber: float,
    extent_pct_red: float,
    extent_min_nm: float = EXTENT_MIN_NM,
) -> AdvisoryStatus:
    """Grade the en-route cloud-clearance axis from the two extents.

    Shared by ``evaluate`` and the vertical-mitigation scan so a candidate
    altitude is graded with exactly the same thresholds as cruise.

    Distance-based, through the shared gate and its minimum-extent floor (#571
    Stage 2). This axis is where the floor matters most: at ``extent_pct_red=15``
    two flagged points on a ~120 nm route used to clear RED outright.
    """
    if total <= 0:
        return AdvisoryStatus.GREEN
    if grade_extent(
        imc_extent, amber_pct=extent_pct_red, min_nm=extent_min_nm,
    ) != AdvisoryStatus.GREEN:
        return AdvisoryStatus.RED
    return grade_extent(
        affected_extent, amber_pct=extent_pct_amber, min_nm=extent_min_nm,
    )


def _field_elevation_ft(ctx: RouteContext, distance_nm: float) -> float:
    """Terrain elevation (ft MSL) at an along-track distance, 0.0 if unknown.

    Used as the floor for the climb/descent corridor check so a cloud layer
    buried below the field (base above cruise is already excluded) is not
    mistaken for a deck the aircraft must transit.
    """
    if ctx.elevation is None or not ctx.elevation.points:
        return 0.0
    nearest = min(ctx.elevation.points, key=lambda p: abs(p.distance_nm - distance_nm))
    return nearest.elevation_ft


def _corridor_points(
    ctx: RouteContext,
    model: str,
    corridor_nm: float,
    phase: str,
) -> Iterator[tuple[float, bool, bool, float | None, float | None, float | None]]:
    """Yield ``(distance_nm, has_ovc, has_bkn, base_agl_ft, deck_base_ft, deck_top_ft)``
    for each corridor point.

    Single source of truth for both corridor membership and the transitable-deck
    condition, shared by the corridor grade (``_check_corridor_vfr``) and the
    corridor mitigation (``_corridor_blocked_profile``):

    - **Membership**: within ``corridor_nm`` of the origin (climb) or destination
      (descent), with a nearer-half tiebreak so the two corridors stay mutually
      exclusive on short routes / large ``corridor_nm`` (a point at the midpoint
      is attributed to the climb-out).
    - **Deck**: a BKN/OVC layer lying entirely between field elevation and cruise
      (``floor < top_ft < cruise``) is one the flight must climb/descend through.
      A layer whose top reaches cruise is cruise-in-cloud and is left to
      ``_check_enroute_vfr`` rather than double-counted here.

    ``base_agl_ft`` is the height **above terrain** of the lowest blocking deck's
    base (``None`` when the point carries no deck) — the VFR room available
    *beneath* the deck, used by the mitigation's reachability gate. The grade
    ignores it. ``deck_base_ft``/``deck_top_ft`` are the MSL envelope of the
    deck layers at this point (``None`` when no deck) — the corridor-deck
    cutout geometry for the highlight (#375).

    Points are yielded in ``ctx.analyses`` order (callers that need distance order
    sort the result).
    """
    cruise = ctx.cruise_altitude_ft
    total = ctx.total_distance_nm

    for rpa in ctx.analyses:
        d = rpa.distance_from_origin_nm or 0.0
        if phase == "climb":
            in_corridor = d <= corridor_nm and d <= total / 2
        else:
            in_corridor = (total - d) <= corridor_nm and d > total / 2
        if not in_corridor:
            continue
        sounding = rpa.sounding.get(model)
        if sounding is None:
            continue
        floor = _field_elevation_ft(ctx, d)
        has_ovc = False
        has_bkn = False
        lowest_base_ft: float | None = None
        deck_top_ft: float | None = None
        for cl in sounding.cloud_layers:
            if cl.coverage not in (CloudCoverage.BKN, CloudCoverage.OVC):
                continue
            if floor < cl.top_ft < cruise:
                if cl.coverage == CloudCoverage.OVC:
                    has_ovc = True
                else:
                    has_bkn = True
                if lowest_base_ft is None or cl.base_ft < lowest_base_ft:
                    lowest_base_ft = cl.base_ft
                if deck_top_ft is None or cl.top_ft > deck_top_ft:
                    deck_top_ft = cl.top_ft
        base_agl_ft = (lowest_base_ft - floor) if lowest_base_ft is not None else None
        yield d, has_ovc, has_bkn, base_agl_ft, lowest_base_ft, deck_top_ft


def _check_corridor_vfr(
    ctx: RouteContext,
    model: str,
    corridor_nm: float,
) -> tuple[AdvisoryStatus, list[str]]:
    """Check the climb-out and descent corridors for transitable cloud decks.

    Cruise altitude can be clear (handled by ``_check_enroute_vfr``) while a
    BKN/OVC deck still sits between the surface and cruise near an airport — a
    layer the flight must climb up through or descend down through to remain
    VMC. Within ``corridor_nm`` of the origin (climb-out) and destination
    (descent), a BKN/OVC layer lying entirely between field elevation and cruise
    (``floor < top_ft < cruise``) is such a deck. A layer whose top reaches
    cruise is cruise-in-cloud and is left to ``_check_enroute_vfr`` rather than
    double-counted here. OVC -> RED (no holes to climb/descend through),
    BKN -> AMBER (likely gaps, pilot judgement).

    Returns (worst_status, detail_fragments).
    """
    worst = AdvisoryStatus.GREEN
    parts: list[str] = []
    loc = ctx.locale

    dep_icao = ctx.airport_conditions.departure.icao if ctx.airport_conditions else None
    arr_icao = ctx.airport_conditions.arrival.icao if ctx.airport_conditions else None

    phases = (
        ("climb", "vfr.corridor_climb", dep_icao, adv_t("airport.dep", loc)),
        ("descent", "vfr.corridor_descent", arr_icao, adv_t("airport.arr", loc)),
    )

    for phase, key, icao, fallback in phases:
        has_ovc = False
        has_bkn = False
        for _d, p_ovc, p_bkn, _base, _db, _dt in _corridor_points(ctx, model, corridor_nm, phase):
            has_ovc = has_ovc or p_ovc
            has_bkn = has_bkn or p_bkn

        if has_ovc:
            worst = _worst_status(worst, AdvisoryStatus.RED)
            parts.append(adv_t(key, loc, cov="OVC", icao=icao or fallback))
        elif has_bkn:
            worst = _worst_status(worst, AdvisoryStatus.AMBER)
            parts.append(adv_t(key, loc, cov="BKN", icao=icao or fallback))

    return worst, parts


def _build_vfr_highlights(
    ctx: RouteContext,
    model: str,
    cloud_clearance_ft: float,
    corridor_nm: float,
    dep_status: AdvisoryStatus,
    arr_status: AdvisoryStatus,
    conv_grade: ConvectiveModelGrade | None = None,
    conv_capped: bool = False,
) -> AdvisoryHighlights:
    """Highlight geometry for the VFR composite (#375).

    Regions are the union of the firing sub-axes, each with its own kind:
    ``cruise_imc`` (cloud segments intersecting the cruise line, same geometry
    as ``vmc_cruise``), ``climb_deck``/``descent_deck`` (the corridor deck bands
    near departure/arrival), and the convective cells of the §22 axis. The
    ribbon is the worst firing sub-axis at each x — with en-route precipitation
    contributing per the shared classifier capped at AMBER (matching the
    composite's cap) — and the airport flight-category axis colouring only the
    endpoint segments.

    The convective axis MUST be represented here, not merely graded: it can be
    the sole driver of the composite's colour (the reported LFMD→EGTF shape had
    airport, cloud, corridor and precip all clear). Without its geometry the
    highlight came back with an empty ``regions`` list and an all-GREEN ribbon,
    and an empty region list makes both renderers skip the scrim entirely — so
    the cross-section drew a spotless chart beside an AMBER badge. Same merge the
    sibling composite already does (``ifr_feasibility``, which unions
    ``conv_grade.region_cells`` into its own regions).

    ``conv_capped`` says the circumnavigable cap softened the grade; the
    convective ribbon contribution is then clamped to AMBER to match. The ribbon
    is a per-point verdict strip read alongside the badge, so leaving RED cells
    painted under an AMBER grade would restate the contradiction this merge
    exists to remove.
    """
    cruise = ctx.cruise_altitude_ft

    # Corridor decks by distance: dist → (severity, deck_base, deck_top).
    corridor: dict[str, dict[float, tuple[HighlightSeverity, float | None, float | None]]] = {}
    for phase in ("climb", "descent"):
        by_dist: dict[float, tuple[HighlightSeverity, float | None, float | None]] = {}
        for d, p_ovc, p_bkn, _base, deck_base, deck_top in _corridor_points(
            ctx, model, corridor_nm, phase
        ):
            if p_ovc:
                by_dist[d] = (HighlightSeverity.RED, deck_base, deck_top)
            elif p_bkn:
                by_dist[d] = (HighlightSeverity.AMBER, deck_base, deck_top)
        corridor[phase] = by_dist

    ribbon_points: list[tuple[float, HighlightSeverity]] = []
    cruise_cells: list[tuple[float, FlaggedCell | None]] = []
    climb_cells: list[tuple[float, FlaggedCell | None]] = []
    descent_cells: list[tuple[float, FlaggedCell | None]] = []

    for rpa in ctx.analyses:
        dist = rpa.distance_from_origin_nm or 0.0
        sounding = rpa.sounding.get(model)
        if sounding is None:
            ribbon_points.append((dist, HighlightSeverity.UNAVAILABLE))
            cruise_cells.append((dist, None))
            climb_cells.append((dist, None))
            descent_cells.append((dist, None))
            continue

        # Cruise-line cloud axis (same geometry as vmc_cruise): IMC → red,
        # marginal clearance → amber (ribbon only, the near-miss layer is not
        # a cutout).
        cls, env_base, env_top = _point_enroute_vfr(sounding, cruise, cloud_clearance_ft)
        if cls == "imc":
            cruise_sev = HighlightSeverity.RED
            cruise_cells.append((dist, FlaggedCell(
                kind="cruise_imc",
                severity=cruise_sev,
                base_ft=int(env_base) if env_base is not None else None,
                top_ft=int(env_top) if env_top is not None else None,
                metric_id="cloud_cover",
                # The cloud method that actually produced these layers — "nwp" /
                # "nwp_synthesized" / "dd" under fallback (#408). Clouds are this
                # composite's only method-bearing axis.
                method_id=sounding.cloud_method_effective,
            )))
        else:
            cruise_sev = (
                HighlightSeverity.AMBER if cls == "marginal" else HighlightSeverity.GREEN
            )
            cruise_cells.append((dist, None))

        # Corridor-deck axes.
        deck_sev = HighlightSeverity.GREEN
        for phase, cells in (("climb", climb_cells), ("descent", descent_cells)):
            hit = corridor[phase].get(dist)
            if hit is None:
                cells.append((dist, None))
                continue
            sev, deck_base, deck_top = hit
            deck_sev = worst_severity(deck_sev, sev)
            cells.append((dist, FlaggedCell(
                kind="climb_deck" if phase == "climb" else "descent_deck",
                severity=sev,
                base_ft=int(deck_base) if deck_base is not None else None,
                top_ft=int(deck_top) if deck_top is not None else None,
                metric_id="cloud_cover",
                method_id=sounding.cloud_method_effective,
            )))

        # En-route precipitation axis, capped at AMBER like the composite grade.
        precip_sev = precip_point_severity(
            classify_precip_point(sounding.precipitation), cap_amber=True
        )

        ribbon_points.append((dist, worst_severity(cruise_sev, deck_sev, precip_sev)))

    # Fold in the §22 convective axis. `conv_grade.ribbon_points` is index-aligned
    # with `ctx.analyses` by construction, which is the same order this loop
    # walked, so the two zip without re-deriving which points were graded.
    if conv_grade is not None:
        for i, (_d, conv_sev) in enumerate(conv_grade.ribbon_points):
            if i >= len(ribbon_points):
                break
            if conv_capped and conv_sev == HighlightSeverity.RED:
                conv_sev = HighlightSeverity.AMBER
            dist_i, sev_i = ribbon_points[i]
            ribbon_points[i] = (dist_i, worst_severity(sev_i, conv_sev))

    # Airport flight-category axis colours only the endpoint segments.
    apply_airport_endpoints(ribbon_points, dep_status, arr_status)

    ribbon = build_ribbon(ribbon_points, ctx.total_distance_nm)
    regions = (
        build_regions(cruise_cells, ctx.total_distance_nm)
        + build_regions(climb_cells, ctx.total_distance_nm)
        + build_regions(descent_cells, ctx.total_distance_nm)
        + (
            build_regions(conv_grade.region_cells, ctx.total_distance_nm)
            if conv_grade is not None else []
        )
    )
    return AdvisoryHighlights(
        ribbon=ribbon,
        regions=regions,
        peak_dist_nm=ribbon_peak(ribbon),
    )


# Severity order for the strict-improvement check on the cruise_imc mitigation.
# Both operands come from `_enroute_vfr_status` (always GREEN/AMBER/RED), so
# UNAVAILABLE is never indexed.
_SEVERITY = {AdvisoryStatus.GREEN: 0, AdvisoryStatus.AMBER: 1, AdvisoryStatus.RED: 2}

# Finite occupancy cost of a cell within cloud-clearance of a deck ("marginal"): a
# flyable-but-VFR-marginal band. Any positive constant works — it only has to make the
# solver prefer a truly-clear (0-cost) band over a marginal one on the hazard tier.
_MARGINAL_COST = 1.0

# A terminal (departure/arrival) deck earns a corridor tip only when it covers more than a
# single route point: >= 2 route points OR a >= 15 nm along-track run. A lone terminal-field
# cloud — which every normal climb-out transits — fails this and yields no "climb after /
# descend before" tip. The profile-shape gate alone (``segments[0].alt_ft < cruise``) fires
# for both a real deck and a single field cloud and cannot tell them apart (#342 Bug A).
_TERMINAL_DECK_MIN_POINTS = 2
_TERMINAL_DECK_MIN_RUN_NM = 15.0


def _vfr_cell_cost(sounding, alt_ft: float, cloud_clearance_ft: float) -> float:
    """Occupancy cost of one altitude at one route point for the VFR cost field.

    ``INF`` inside a BKN/OVC layer (a hard wall — cannot fly VFR in cloud, regardless of
    coverage; the BKN/OVC distinction lives in the *grade*, not the path — decision 7),
    ``_MARGINAL_COST`` within ``cloud_clearance_ft`` of a layer (flyable but sub-VFR
    separation), else ``0``. Mirrors the per-point logic of ``_check_enroute_vfr`` so the
    solver's feasibility matches the advisory's own grading of the same cell.
    """
    marginal = False
    for cl in sounding.cloud_layers:
        if cl.coverage not in (CloudCoverage.BKN, CloudCoverage.OVC):
            continue
        if cl.base_ft <= alt_ft <= cl.top_ft:
            return INF
        if min(abs(alt_ft - cl.base_ft), abs(alt_ft - cl.top_ft)) < cloud_clearance_ft:
            marginal = True
    return _MARGINAL_COST if marginal else 0.0


def _build_vfr_cost_model(
    ctx: RouteContext,
    model: str,
    cloud_clearance_ft: float,
    floor_margin_ft: float,
) -> CostModel | None:
    """VFR cost model: the shared builder plus this advisory's cloud hazard→cost mapping.

    ``INF`` in BKN/OVC cloud, ``_MARGINAL_COST`` within clearance, else ``0`` (see
    ``_vfr_cell_cost``). The floor is ``terrain + floor_margin_ft`` — the unified
    conservative floor (the scud-running margin under a deck AND the terrain clearance for
    a lower cruise are one floor here, #335). Bin construction, terrain-floor / ceiling
    walling and floor-band anchoring all live in ``build_cost_model``.
    """
    return build_cost_model(
        ctx, model,
        lambda sounding, alt: _vfr_cell_cost(sounding, alt, cloud_clearance_ft),
        floor_margin_ft,
    )


def _has_interior_below_cruise(profile: Profile, cruise: int) -> bool:
    """True if the profile dips below cruise somewhere OTHER than the terminal runs.

    A corridor mitigation ("climb to cruise after ~X nm" / "descend before ~X nm") is
    only honest when the low flying is confined to the departure climb-out and/or the
    arrival descent. If the profile *also* drops below cruise in the interior — a mid-route
    deck the profile has to descend under and climb back out of — then a "climb to cruise
    after X nm" claim is misleading (you'd have to come back down further out). This is the
    exact "climb after 10 nm when cruise is IMC 40 nm later" failure the redesign removes;
    the emergent mutual-exclusivity only holds for *contiguous* IMC, so an interior deck
    needs this explicit guard. When it fires we offer no corridor mitigation (the old
    ``enroute_status == GREEN`` gate's safe-but-silent behavior).
    """
    segs = profile.segments
    n = len(segs)
    below = [i for i, s in enumerate(segs) if s.alt_ft < cruise]
    if not below:
        return False
    lead: set[int] = set()
    i = 0
    while i < n and segs[i].alt_ft < cruise:
        lead.add(i)
        i += 1
    trail: set[int] = set()
    j = n - 1
    while j >= 0 and segs[j].alt_ft < cruise:
        trail.add(j)
        j -= 1
    return any(i not in lead and i not in trail for i in below)


def _terminal_deck_span(
    ctx: RouteContext,
    model: str,
    cruise: float,
    lo_nm: float,
    hi_nm: float,
) -> tuple[int, float]:
    """Count route points carrying a real climb/descent deck within ``[lo_nm, hi_nm]``.

    A "deck" here is any BKN/OVC layer intruding the climb/descent band — ``top_ft`` above
    the field and ``base_ft`` below cruise — i.e. a layer the flight must transit to reach
    cruise (a sub-cruise deck, cruise itself clear) OR a cloud reaching cruise over the
    field. Scanning only the terminal below-cruise run and requiring more than one such
    point is what separates a genuine terminal deck from a lone departure-/arrival-field
    cloud that every normal climb-out passes through (#342 Bug A). ``_check_enroute_vfr``
    at cruise would count only the cruise-reaching clouds, silently dropping every
    ordinary sub-cruise deck (the *primary* ``climb_deck`` case), so the band test is used
    instead — it covers both while still keying on the same "not a single point" rule.

    Returns ``(point_count, run_nm)`` over the qualifying points, where
    ``run_nm`` is the longest **contiguous** along-track run measured with the
    shared midpoint-owned-cell convention. It used to be ``max(d) - min(d)``,
    which includes the gaps: two field clouds 20 nm apart with clear air between
    them scored a 20 nm "run" and offered a corridor tip for a deck that is not
    there (#571 D-geometry-4).
    """
    all_dists: list[float] = []
    flags: list[bool] = []
    count = 0
    for rpa in ctx.analyses:
        d = rpa.distance_from_origin_nm or 0.0
        all_dists.append(d)
        flags.append(False)
        if not (lo_nm <= d <= hi_nm):
            continue
        sounding = rpa.sounding.get(model)
        if sounding is None:
            continue
        floor = _field_elevation_ft(ctx, d)
        for cl in sounding.cloud_layers:
            if cl.coverage not in (CloudCoverage.BKN, CloudCoverage.OVC):
                continue
            if cl.top_ft > floor and cl.base_ft < cruise:
                flags[-1] = True
                count += 1
                break
    if count < _TERMINAL_DECK_MIN_POINTS:
        # A single point has no *run* — it has a cell. Under the old
        # ``max(d) - min(d)`` a lone point scored 0 by construction, so the
        # run arm could never fire alone; measuring its midpoint-owned cell
        # instead let one point clear the 15 nm bar wherever route sampling is
        # sparse (20 nm on a 40 nm-spaced leg), contradicting this function's
        # own rule that a lone field cloud never qualifies (#571 review).
        return count, 0.0
    return count, route_extent(
        all_dists, ctx.total_distance_nm, flags,
    ).longest_run_nm


def _is_real_terminal_deck(count: int, run_nm: float) -> bool:
    """A terminal deck earns a corridor tip only when it spans more than one point.

    ``>= 2`` route points OR ``>= 15`` nm of *contiguous* along-track run (#342
    Bug A, geometry corrected in #571). A single terminal point — the
    departure/arrival field's own cloud — never qualifies.
    """
    return count >= _TERMINAL_DECK_MIN_POINTS or run_nm >= _TERMINAL_DECK_MIN_RUN_NM


def _solver_mitigations(
    ctx: RouteContext,
    model: str,
    cloud_clearance_ft: float,
    extent_pct_amber: float,
    extent_pct_red: float,
    extent_min_nm: float,
    floor_margin_ft: float,
    max_reposition_nm: float,
    enroute_status: AdvisoryStatus,
    loc: str | None,
) -> list[Mitigation]:
    """Derive all VFR mitigations from a single solved vertical profile (#335).

    Replaces the former per-axis scans (`_vertical_mitigation` + `_corridor_mitigation`
    and their gates). One min-cost profile over the ``(distance × altitude)`` grid yields
    the right advice in every case, and the old special-cases fall out:

    - **cruise_imc** ("fly lower"): the cruise axis is flagged and the continuous profile
      never reaches planned cruise → the whole route is under cloud. The solver's role here
      is the feasibility gate (a :class:`Blockage` — a full-column cloud wall — means no
      lower band is flyable, so no tip); the reported single flat altitude is then found by
      scanning downward for the highest whole-route altitude that strictly improves,
      graded by ``_enroute_vfr_status`` so the status matches the advisory's own grading.
      (Scanning rather than reusing the profile's top band matters when the deck height
      varies along the route: the min-cost profile staircases, and its highest band can be
      inside the deck elsewhere — a single flat altitude lower down may still clear.)
    - **climb_deck** / **descent_deck**: the profile reaches cruise but is forced low
      next to an airport → a climb-to-cruise (near departure) or descent-from-cruise
      (near arrival) transition, reported at that break. The cruise-green mutual
      exclusivity is emergent: when cruise is IMC the profile never reaches it, so no
      corridor transition exists — nothing to suppress.

    A terminal (climb/descent) tip is emitted only when the forcing deck is *real* — it
    covers more than a single route point (>= 2 points / >= 15 nm), not just the departure/
    arrival field's own cloud that every climb-out transits (#342 Bug A) — and the break is
    within ``max_reposition_nm`` of the airport (a break far out means flying under the deck
    for most of the leg). The former ``<= total / 2`` half-route split is gone: ``clean_terminal``
    already guarantees the interior is clear, so the split only produced a knife-edge that
    silently dropped the correct arrival tip on a fractional-mile miss (#342 Bug B). A
    :class:`Blockage` (no continuous flyable band from the floor) yields no mitigation — the
    RED is genuine.
    """
    model_cm = _build_vfr_cost_model(ctx, model, cloud_clearance_ft, floor_margin_ft)
    if model_cm is None:
        return []

    cruise = ctx.cruise_altitude_ft
    result = solve(model_cm, preferred_alt_ft=cruise)
    if isinstance(result, Blockage):
        return []

    profile: Profile = result
    total = ctx.total_distance_nm
    max_alt = max(s.alt_ft for s in profile.segments)
    reaches_cruise = max_alt >= cruise
    # A corridor mitigation is only honest for a clean terminal deck — if the profile also
    # dips below cruise in the interior (a mid-route deck), "climb to cruise after X nm"
    # would be misleading, so suppress both corridor tips (#338 review finding 2).
    clean_terminal = not _has_interior_below_cruise(profile, cruise)
    prof_obj = to_mitigation_profile(profile)
    mitigations: list[Mitigation] = []

    # cruise_imc — the profile can't sustain cruise; the solver has confirmed a lower band
    # is flyable (not a Blockage), so scan downward for the best single flat altitude to
    # report (highest strictly-improving, preferring GREEN over AMBER — mirrors the old
    # per-step scan so a staircasing profile doesn't drop an otherwise-valid tip, #338).
    if enroute_status in (AdvisoryStatus.AMBER, AdvisoryStatus.RED) and not reaches_cruise:
        max_terrain = ctx.elevation.max_elevation_ft if ctx.elevation else 0.0
        floor = max_terrain + floor_margin_ft
        best_alt: int | None = None
        best_status: AdvisoryStatus | None = None
        alt = cruise - MITIGATION_BIN_STEP_FT
        while alt >= floor:
            tot, _imc, _marg, _, cand_imc_ext, cand_ext = _check_enroute_vfr(
                ctx, model, cloud_clearance_ft, alt,
            )
            cand = _enroute_vfr_status(
                tot, cand_imc_ext, cand_ext,
                extent_pct_amber, extent_pct_red, extent_min_nm,
            )
            if _SEVERITY[cand] < _SEVERITY[enroute_status]:
                if best_status is None or _SEVERITY[cand] < _SEVERITY[best_status]:
                    best_alt, best_status = int(alt), cand
                if cand == AdvisoryStatus.GREEN:
                    break  # highest GREEN — can't do better
            alt -= MITIGATION_BIN_STEP_FT
        if best_alt is not None and best_status is not None:
            detail_key = (
                "vfr.mitigation.altitude"
                if best_status == AdvisoryStatus.GREEN
                else "vfr.mitigation.altitude_marginal"
            )
            mitigations.append(Mitigation(
                kind=MitigationKind.ALTITUDE,
                addresses="cruise_imc",
                detail=adv_t(detail_key, loc, alt=best_alt),
                mitigated_status=best_status,
                altitude_ft=best_alt,
                profile=prof_obj,
            ))

    # climb_deck — departure forced low by a *real* terminal deck, climbing to cruise near
    # departure. Two guards beyond profile shape:
    #   - the deck must span more than a single point (>= 2 points / >= 15 nm), else it's the
    #     departure field's own cloud that every climb-out transits — not a repositionable
    #     deck (#342 Bug A);
    #   - the break must be within `max_reposition_nm`. The old `<= total / 2` half-route
    #     split is dropped: `clean_terminal` already guarantees the interior is clear, so a
    #     terminal tip is meaningful whenever the break is close to the field — the half-route
    #     knife-edge that silently dropped the correct tip on a 0.4 nm miss is gone (#342 Bug B).
    if reaches_cruise and clean_terminal and profile.segments[0].alt_ft < cruise:
        climb = next(
            (t for t in profile.transitions if t.to_alt_ft > t.from_alt_ft and t.to_alt_ft >= cruise),
            None,
        )
        if (
            climb is not None
            and climb.from_nm <= max_reposition_nm
            and _is_real_terminal_deck(*_terminal_deck_span(ctx, model, cruise, 0.0, climb.to_nm))
        ):
            dist = round(climb.from_nm)
            mitigations.append(Mitigation(
                kind=MitigationKind.ROUTE_POSITION,
                addresses="climb_deck",
                detail=adv_t("vfr.mitigation.climb_after", loc, dist=dist),
                mitigated_status=AdvisoryStatus.GREEN,
                distance_nm=dist,
                reference="departure",
                profile=prof_obj,
            ))

    # descent_deck — arrival forced low by a *real* terminal deck, descending from cruise
    # near arrival. Same real-deck gate (#342 Bug A) and same drop of the `<= total / 2`
    # split (#342 Bug B) as climb_deck above.
    if reaches_cruise and clean_terminal and profile.segments[-1].alt_ft < cruise:
        descent = next(
            (t for t in reversed(profile.transitions) if t.from_alt_ft > t.to_alt_ft and t.from_alt_ft >= cruise),
            None,
        )
        if descent is not None:
            before = total - descent.to_nm
            if before <= max_reposition_nm and _is_real_terminal_deck(
                *_terminal_deck_span(ctx, model, cruise, descent.from_nm, total)
            ):
                dist = round(before)
                mitigations.append(Mitigation(
                    kind=MitigationKind.ROUTE_POSITION,
                    addresses="descent_deck",
                    detail=adv_t("vfr.mitigation.descend_before", loc, dist=dist),
                    mitigated_status=AdvisoryStatus.GREEN,
                    distance_nm=dist,
                    reference="arrival",
                    profile=prof_obj,
                ))

    return mitigations


@register
class VFRFeasibilityEvaluator:
    """Evaluates overall feasibility of VFR flight along the route.

    Combines airport flight category, en-route cloud clearance, and VMC
    compliance into a single advisory. GREEN means VFR conditions throughout;
    AMBER means marginal conditions; RED means IFR conditions detected.
    """

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="vfr_feasibility",
            name="VFR Feasibility",
            short_description="Overall VFR flight viability",
            description=(
                "Composite assessment of VFR flight feasibility. Checks airport "
                "conditions (VFR/MVFR/IFR), en-route cloud clearance relative to "
                "cruise altitude, minimum cloud separation, and whether the "
                "climb-out and descent corridors near the airports are clear of "
                "BKN/OVC layers the flight would have to transit. RED indicates IFR "
                "conditions, IMC at cruise, or an OVC layer blocking climb/descent; "
                "AMBER flags marginal conditions or a BKN layer in a corridor."
            ),
            category="flight_rules",
            timing_class="scan",
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="cloud_clearance_ft",
                    label="Cloud clearance",
                    description="Minimum vertical separation from BKN/OVC cloud layers",
                    type="altitude",
                    unit="ft",
                    default=1000,
                    min=500,
                    max=2000,
                    step=500,
                    audience="pilot",
                ),
                AdvisoryParameterDef(
                    key="extent_pct_amber",
                    label="% of route in IMC (amber)",
                    description="Route percentage in IMC or marginal clearance for amber",
                    type="percent",
                    unit="%",
                    default=15,
                    min=5,
                    max=50,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="extent_pct_red",
                    label="% of route in IMC (red)",
                    description="Route percentage in IMC for red",
                    type="percent",
                    unit="%",
                    default=30,
                    min=10,
                    max=80,
                    step=5,
                ),
                extent_min_nm_param(),
                AdvisoryParameterDef(
                    key="terminal_corridor_nm",
                    label="Terminal corridor",
                    description="Distance from departure/arrival to check for BKN/OVC layers in the climb-out and descent path",
                    type="distance",
                    unit="nm",
                    default=5,
                    min=0,
                    max=20,
                    step=1,
                ),
                AdvisoryParameterDef(
                    key="mitigation_min_base_agl_ft",
                    label="Mitigation floor (AGL)",
                    description="Minimum height above terrain for any mitigation altitude — the floor of the vertical-profile solver (#335). A lower cruise or an under-deck corridor must sit at least this far above terrain to be offered; below it there isn't safe VFR room, so the RED is genuine. Doubles as the scud-running margin beneath a deck.",
                    type="altitude",
                    unit="ft",
                    default=3000,
                    min=1000,
                    max=6000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="mitigation_max_reposition_nm",
                    label="Max reposition distance",
                    description="Only offer an along-route 'climb after / descend before' mitigation when the VMC break is within this distance of the airport. Beyond it the deck is too extensive to thread by repositioning (you'd fly under it for most of the leg), so no mitigation is offered.",
                    type="distance",
                    unit="nm",
                    default=25,
                    min=5,
                    max=50,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        cloud_clearance_ft = params.get("cloud_clearance_ft", 1000)
        extent_pct_amber = params.get("extent_pct_amber", 15)
        extent_pct_red = params.get("extent_pct_red", 30)
        extent_min_nm = params.get("extent_min_nm", EXTENT_MIN_NM)
        corridor_nm = params.get("terminal_corridor_nm", 5)
        mitigation_min_base_agl_ft = params.get("mitigation_min_base_agl_ft", 3000)
        mitigation_max_reposition_nm = params.get("mitigation_max_reposition_nm", 25)

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            # 1. Airport conditions
            airport_status, airport_detail, dep_status, arr_status = (
                _check_airport_vfr(ctx, model)
            )

            # 2. En-route cloud clearance
            (
                total, imc_count, marginal_count, _,
                imc_extent, enroute_extent,
            ) = _check_enroute_vfr(
                ctx, model, cloud_clearance_ft, ctx.cruise_altitude_ft
            )

            # 3. Climb-out / descent corridor decks (BKN/OVC below cruise near airports)
            corridor_status, corridor_parts = _check_corridor_vfr(
                ctx, model, corridor_nm
            )

            loc = ctx.locale
            if (
                total == 0
                and airport_status in (AdvisoryStatus.GREEN, AdvisoryStatus.UNAVAILABLE)
                and not airport_detail
                and corridor_status == AdvisoryStatus.GREEN
            ):
                per_model.append(ModelAdvisoryResult.build(
                    model=model, status=AdvisoryStatus.UNAVAILABLE,
                    detail=adv_t("no_data", loc), affected=0, total=0,
                    total_distance_nm=ctx.total_distance_nm,
                ))
                continue

            # 4. Determine en-route status
            affected = imc_count + marginal_count
            enroute_status = _enroute_vfr_status(
                total, imc_extent, enroute_extent,
                extent_pct_amber, extent_pct_red, extent_min_nm,
            )
            # Coverage tolerance (#391): a clear en-route cloud verdict from
            # soundings at too small a share of the route is UNAVAILABLE, not
            # clear — the same guard the standalone sibling (vmc_cruise) got in
            # this PR. Contributes nothing to the composite's `worst` rather than
            # a false GREEN; a flagged en-route verdict always stands.
            if enroute_status == AdvisoryStatus.GREEN and below_coverage(total, len(ctx.analyses)):
                enroute_status = AdvisoryStatus.UNAVAILABLE
            enroute_detail = ""

            # Each sentence quotes the extent of the population it NAMES, which
            # is not always the union ``affected`` counts (#571 review round 8).
            # RED is graded off ``imc_extent`` alone and says "IMC over …", so it
            # must quote the IMC miles: on a route with one solid-IMC point and
            # several marginal-clearance ones, the union inflates the number
            # beside the word "IMC" — the D1 defect on the composite that
            # actually reaches the pilot. Only the sentence naming *both*
            # populations quotes the union.
            named_extent = enroute_extent
            if total > 0 and affected > 0:
                if enroute_status == AdvisoryStatus.RED:
                    named_extent = imc_extent
                    enroute_detail = adv_t(
                        "vfr.imc_over", loc, extent=format_extent(imc_extent),
                    )
                elif enroute_status == AdvisoryStatus.AMBER:
                    if marginal_count > 0 and imc_count > 0:
                        enroute_detail = adv_t(
                            "vfr.imc_marginal", loc,
                            extent=format_extent(enroute_extent),
                        )
                    elif imc_count > 0:
                        named_extent = imc_extent
                        enroute_detail = adv_t(
                            "vfr.imc_over", loc, extent=format_extent(imc_extent),
                        )
                    else:
                        enroute_detail = adv_t(
                            "vfr.marginal", loc, extent=format_extent(enroute_extent),
                        )
                else:  # GREEN status but some points affected → minor clearance issues
                    enroute_detail = adv_t(
                        "vfr.minor", loc, extent=format_extent(enroute_extent),
                    )

            # 5. En-route precipitation (visibility proxy) — capped at AMBER
            # in the composite; the standalone advisory grades it fully.
            # Deliberately called WITHOUT params: the composite always uses the
            # fixed precip defaults, independent of any per-user tuning of the
            # standalone EnroutePrecipEvaluator. The params dict here carries
            # only VFR keys, so the two can grade differently if a user tunes
            # the standalone — that divergence is intentional; the composite is
            # a fixed-threshold sanity floor, not a mirror of the standalone.
            precip_status, precip_detail, _, _, precip_signal, _ = (
                classify_enroute_precip(ctx, model)
            )
            # Old pack without precip data (no signal / UNAVAILABLE) → treat as
            # GREEN in the composite rather than penalising a missing field.
            if not precip_signal or precip_status == AdvisoryStatus.UNAVAILABLE:
                precip_status, precip_detail = AdvisoryStatus.GREEN, ""
            elif precip_status == AdvisoryStatus.RED:
                precip_status = AdvisoryStatus.AMBER

            # 5b. Convection (§22): the colour is the Convective Activity card's,
            # taken verbatim — no second formula, no thresholds of this
            # composite's own — and then read through the character band, which
            # is the VFR-avoidability axis. `CHARACTER_STATUS` is that mapping's
            # single source of truth ("RED = not circumnavigable VFR, AMBER =
            # avoidable but committing"), so the cap below cannot drift from the
            # character card beside it.
            #
            # The cap is the deterministic twin of the digest's already-pinned
            # rule (§15/#568): activity RED + ISOLATED/SCATTERED is a localised,
            # avoidable hazard and reads AMBER here, while WIDESPREAD / ORGANIZED
            # / EMBEDDED stand RED — no altitude and no see-and-avoid fixes
            # horizontal extent, and EMBEDDED is by definition "you cannot see
            # them". The cap only ever *softens*, and never below the character
            # band's own status: a GREEN convective axis stays GREEN, so this can
            # add a colour to the composite but never remove one the convective
            # card is showing.
            conv_params = resolve_convective_params(ctx)
            char_params = resolve_character_params(ctx)
            conv_grade = grade_convective_model(ctx, model, conv_params)
            conv_status = conv_grade.status
            character = classify_route_character(ctx, model, char_params)
            conv_detail = ""
            cap_applied = False
            if conv_status not in (AdvisoryStatus.GREEN, AdvisoryStatus.UNAVAILABLE):
                # Soften ONLY on a band that positively establishes the cells can
                # be flown around, and only downward — `_least_severe` keeps an
                # ISOLATED band from ever raising an AMBER activity grade to RED.
                # A band that establishes nothing (NONE / UNKNOWN / no soundings)
                # leaves the graded colour untouched: the composite may not
                # publish calmer than the Convective Activity card on the
                # strength of an axis that did not run.
                cap = _CIRCUMNAVIGABLE_CAP.get(character) if character else None
                if cap is not None:
                    conv_status = _least_severe(conv_status, cap)
                    cap_applied = True
                # The sentence tracks what was established, not the colour: a
                # grade that never softened must not assert avoidability either
                # way, or the card claims a judgement the data does not carry.
                if cap is not None:
                    conv_key = "vfr.conv_circumnavigable"
                elif character in _NOT_CIRCUMNAVIGABLE_BANDS:
                    conv_key = "vfr.conv_not_circumnavigable"
                else:
                    conv_key = "vfr.conv_uncharacterised"
                conv_detail = adv_t(
                    conv_key,
                    loc,
                    risk=conv_grade.worst_risk.value.upper(),
                    # Quote the extent of the tier the sentence names (#571 D1) —
                    # same rule the convective and IFR cards follow.
                    extent=format_extent(
                        conv_grade.extent_mod
                        if conv_grade.affected_mod
                        else conv_grade.extent
                    ),
                )
            # Coverage tolerance (#391), matching the axes above: a clear
            # convective verdict off soundings covering too little of the route
            # abstains rather than vouching for the rest. Flagged verdicts stand.
            if conv_status == AdvisoryStatus.GREEN and below_coverage(
                conv_grade.total, len(ctx.analyses)
            ):
                conv_status = AdvisoryStatus.UNAVAILABLE

            # 6. Combine airport + en-route + corridor + precipitation + convection
            status = _worst_status(
                airport_status, enroute_status, corridor_status, precip_status,
                conv_status,
            )

            detail_parts = []
            if airport_detail:
                detail_parts.append(airport_detail)
            if enroute_detail:
                detail_parts.append(enroute_detail)
            detail_parts.extend(corridor_parts)
            if precip_status != AdvisoryStatus.GREEN and precip_detail:
                detail_parts.append(precip_detail)
            if conv_detail:
                detail_parts.append(conv_detail)

            if not detail_parts:
                if total > 0:
                    detail = adv_t("vfr.throughout", loc)
                else:
                    detail = adv_t("vfr.airports_ok", loc)
            else:
                detail = " | ".join(detail_parts)

            # Composite coverage tolerance (#391 review): VFR feasibility is
            # fundamentally about the en-route cloud picture, so a GREEN grade
            # while most of the route's soundings are missing overstates
            # confidence — even when a single assessed corridor point or the
            # missing-precip→GREEN mapping would otherwise carry it past the
            # per-axis guards. Downgrade a would-be-GREEN composite to UNAVAILABLE
            # on thin en-route coverage; a flagged (AMBER/RED) grade always stands.
            if status == AdvisoryStatus.GREEN and below_coverage(total, len(ctx.analyses)):
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)

            # 7. Mitigations (advice only — never change the grade). All derived
            # from a single continuous vertical profile over the (distance ×
            # altitude) grid (#335): "fly lower" (cruise_imc) when the profile
            # can't sustain cruise, "climb after / descend before" (climb_deck /
            # descent_deck) when a terminal deck forces the climb/descent low.
            mitigations: list[Mitigation] = _solver_mitigations(
                ctx, model, cloud_clearance_ft,
                extent_pct_amber, extent_pct_red, extent_min_nm,
                mitigation_min_base_agl_ft, mitigation_max_reposition_nm,
                enroute_status, loc,
            )
            # The below-base escape (#298 geometry, altitude-swept). Offered only
            # when the convective axis is the thing being flagged: a level under
            # the cells answers convection, not a terminal deck or an airport
            # category, and a tip that does not address the flagged sub-issue is
            # noise. Advice only — like every mitigation it never moves the
            # grade; the *grade* at a lower level is what the altitude table
            # shows, since character is re-derived at each altitude it sweeps.
            if conv_status in (AdvisoryStatus.AMBER, AdvisoryStatus.RED):
                # One tip per convective cluster (#593). A route crosses more
                # than one system, and the systems are answered differently: on
                # LFMD→EGTF 2026-08-27 the mid-route cells were flyable
                # underneath while the arrival-end cells, hours later, were not.
                # A cluster with no escape must not silence one that has it, so
                # each is reported on its own with the miles it applies to.
                for escape in below_base_escapes(ctx, model, char_params):
                    mitigations.append(Mitigation(
                        kind=MitigationKind.ALTITUDE,
                        addresses=CONV_MITIGATION_ADDRESSES,
                        detail=adv_t(
                            "vfr.mitigation.below_base", loc,
                            alt=escape.altitude_ft, fl=escape.base_fl,
                            frm=round(escape.dist_from_nm),
                            to=round(escape.dist_to_nm),
                        ),
                        # The sub-issue's status at that level — the character
                        # band actually re-derived there, never the composite's.
                        mitigated_status=CHARACTER_STATUS.get(
                            escape.band, AdvisoryStatus.AMBER
                        ),
                        altitude_ft=escape.altitude_ft,
                        # The cluster's own band, at the level that clears it.
                        # Deliberately NOT a whole-route profile: the transitions
                        # to and from cruise are the pilot's to plan, and the
                        # below-base test is not a per-point cost
                        # `vertical_profile.solve` could propose one from — the
                        # same limit the character card's EMBEDDED tip documents.
                        profile=MitigationProfile(segments=[MitigationSegment(
                            dist_from_nm=escape.dist_from_nm,
                            dist_to_nm=escape.dist_to_nm,
                            altitude_ft=escape.altitude_ft,
                        )]),
                    ))

            # 8. Highlights (#375) only when the model has en-route data. The
            # multi-kind cutouts (cruise IMC + corridor decks) are the point of
            # this composite's geometry.
            highlights = None
            if total > 0:
                highlights = _build_vfr_highlights(
                    ctx, model, cloud_clearance_ft, corridor_nm,
                    dep_status, arr_status,
                    conv_grade=conv_grade,
                    conv_capped=cap_applied,
                )

            # primary_method_id: clouds are this composite's only method-bearing
            # axis — the airport and precipitation axes carry none, and every
            # region it emits (cruise_imc / climb_deck / descent_deck) is a cloud
            # region. So driving_method_id over the pooled list is single-axis and
            # correct; the only risk is *borrowing* it for a grade the clouds did
            # not drive. Gate on the cloud axis reaching the composite status, the
            # same guard ifr_feasibility applies to its icing axis (#409).
            cloud_status = _worst_status(enroute_status, corridor_status)
            cloud_primary = driving_method_id(highlights, status)
            primary_method_id = cloud_primary if cloud_status == status else None

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
                extent=enroute_extent,
                # The tier the sentence named rides the higher-threshold field,
                # so the number a pilot reads is one the object also publishes.
                affected_mod=named_extent.points,
                extent_mod=named_extent,
                mitigations=mitigations,
                highlights=highlights,
                primary_method_id=primary_method_id,
            ))

        return RouteAdvisoryResult.from_per_model("vfr_feasibility", per_model, params)
