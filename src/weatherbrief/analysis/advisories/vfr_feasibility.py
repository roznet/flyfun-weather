"""VFR feasibility advisory — overall VFR flight viability assessment.

Composite advisory combining airport conditions, en-route cloud clearance,
VMC compliance, climb-out/descent corridor decks, and en-route precipitation
into a single go/no-go style assessment for VFR flights.

The precipitation axis (shared classifier with the en-route precipitation
advisory) is capped at AMBER here: a pilot VMC-on-top is not directly
affected by surface rain below, but widespread snow or heavy rain degrades
visibility and every descent/divert option, which deserves a composite
caution. The standalone advisory still grades it fully (snow can RED).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from typing import Literal

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    build_cost_model,
    to_mitigation_profile,
)
from weatherbrief.analysis.advisories.enroute_precip import (
    EnroutePrecipAssessment,
    assess_enroute_precip,
)
from weatherbrief.analysis.advisories.evidence import (
    DataState,
    EvidenceSample,
    cloud_method_id,
    combine_data_states,
    data_state_from_domains,
    summarize_evidence,
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
    AdvisoryParameterDef,
    AdvisoryStatus,
    CloudCoverage,
    EnhancedCloudLayer,
    Mitigation,
    MitigationKind,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
    SoundingAnalysis,
)
from weatherbrief.models.airport_conditions import FlightCategory


def _worst_status(*statuses: AdvisoryStatus) -> AdvisoryStatus:
    """Return the most severe status from the given values."""
    return AdvisoryStatus.worst(list(statuses))


@dataclass(frozen=True)
class VFRPointAssessment:
    """One route point's authoritative VFR cloud assessment."""

    point_index: int
    method_id: str | None
    available: bool
    complete: bool
    in_cloud: bool
    marginal: bool
    cloud_samples: tuple[EvidenceSample, ...]


@dataclass(frozen=True)
class CorridorPointAssessment:
    """One point in a climb/descent corridor and its exact blocking layers."""

    point_index: int
    method_id: str | None
    distance_nm: float
    phase: Literal["climb", "descent"]
    has_ovc: bool
    has_bkn: bool
    base_agl_ft: float | None
    blocking_layers: tuple[EnhancedCloudLayer, ...]


def _classify_vfr_cloud_layers(
    sounding: SoundingAnalysis,
    altitude_ft: float,
    cloud_clearance_ft: float,
) -> tuple[tuple[EnhancedCloudLayer, ...], tuple[EnhancedCloudLayer, ...]]:
    """Return in-cloud and marginal layers for the canonical VFR predicate."""
    in_cloud_layers: list[EnhancedCloudLayer] = []
    marginal_layers: list[EnhancedCloudLayer] = []
    for layer in sounding.cloud_layers:
        if layer.coverage not in (CloudCoverage.BKN, CloudCoverage.OVC):
            continue
        if layer.base_ft <= altitude_ft <= layer.top_ft:
            in_cloud_layers.append(layer)
            continue
        if min(
            abs(altitude_ft - layer.base_ft),
            abs(altitude_ft - layer.top_ft),
        ) < cloud_clearance_ft:
            marginal_layers.append(layer)
    return tuple(in_cloud_layers), tuple(marginal_layers)


def _check_airport_vfr(
    ctx: RouteContext,
    model: str,
) -> tuple[AdvisoryStatus, str, DataState]:
    """Check departure and arrival airport conditions for VFR feasibility.

    Uses the flight category (VFR/MVFR/IFR/LIFR) which already encodes
    ceiling and visibility thresholds per aviation standards.

    Returns (status, detail_fragment, two-endpoint data state).
    """
    expected = {"departure", "arrival"}
    if ctx.airport_conditions is None:
        return (
            AdvisoryStatus.GREEN,
            "",
            data_state_from_domains(
                expected=expected,
                evaluated=(),
                complete=(),
            ),
        )

    dep = ctx.airport_conditions.departure
    arr = ctx.airport_conditions.arrival
    dep_cond = dep.condition_for_model(model)
    arr_cond = arr.condition_for_model(model)

    parts: list[str] = []
    worst = AdvisoryStatus.GREEN
    evaluated: set[str] = set()

    loc = ctx.locale
    for endpoint, label_key, icao, cond in [
        ("departure", "airport.dep", dep.icao, dep_cond),
        ("arrival", "airport.arr", arr.icao, arr_cond),
    ]:
        if cond is None:
            continue
        evaluated.add(endpoint)
        label = adv_t(label_key, loc)
        cat = cond.flight_category
        if cat in (FlightCategory.IFR, FlightCategory.LIFR):
            worst = _worst_status(worst, AdvisoryStatus.RED)
            parts.append(f"{label} {icao} {cat.value}")
        elif cat == FlightCategory.MVFR:
            worst = _worst_status(worst, AdvisoryStatus.AMBER)
            parts.append(f"{label} {icao} MVFR")

    detail = " | ".join(parts) if parts else ""
    return (
        worst,
        detail,
        data_state_from_domains(
            expected=expected,
            evaluated=evaluated,
            complete=evaluated,
        ),
    )


def _assess_enroute_vfr(
    ctx: RouteContext,
    model: str,
    cloud_clearance_ft: float,
    altitude_ft: float,
) -> list[VFRPointAssessment]:
    """Assess every stable route point using the established VFR cloud predicate."""
    assessments: list[VFRPointAssessment] = []
    ordered_analyses = sorted(
        ctx.analyses,
        key=lambda rpa: (rpa.distance_from_origin_nm, rpa.point_index),
    )

    for rpa in ordered_analyses:
        sounding = rpa.sounding.get(model)
        if sounding is None:
            assessments.append(
                VFRPointAssessment(
                    point_index=rpa.point_index,
                    method_id=None,
                    available=False,
                    complete=False,
                    in_cloud=False,
                    marginal=False,
                    cloud_samples=(),
                )
            )
            continue

        method_id = cloud_method_id(
            sounding.cloud_method_effective,
            ctx.cloud_method,
        )
        in_cloud_layers, marginal_layers = _classify_vfr_cloud_layers(
            sounding,
            altitude_ft,
            cloud_clearance_ft,
        )

        if in_cloud_layers:
            in_cloud = True
            marginal = False
            selected_layers = in_cloud_layers
            severity = AdvisoryStatus.RED
            reason_code = "vfr_cruise_imc"
        elif marginal_layers:
            in_cloud = False
            marginal = True
            selected_layers = marginal_layers
            severity = AdvisoryStatus.AMBER
            reason_code = "vfr_cloud_clearance"
        else:
            in_cloud = marginal = False
            selected_layers = ()
            severity = AdvisoryStatus.GREEN
            reason_code = ""

        samples = tuple(
            EvidenceSample(
                point_index=rpa.point_index,
                severity=severity,
                reason_code=reason_code,
                metric_id="cloud_coverage",
                method_id=method_id,
                lower_altitude_ft=round(layer.base_ft),
                upper_altitude_ft=round(layer.top_ft),
            )
            for layer in selected_layers
        )
        assessments.append(
            VFRPointAssessment(
                point_index=rpa.point_index,
                method_id=method_id,
                available=True,
                complete=True,
                in_cloud=in_cloud,
                marginal=marginal,
                cloud_samples=samples,
            )
        )

    return assessments


def _check_enroute_vfr(
    ctx: RouteContext,
    model: str,
    cloud_clearance_ft: float,
    altitude_ft: float,
) -> tuple[int, int, int, int]:
    """Check en-route cloud clearance for VFR at ``altitude_ft``.

    Takes the evaluated altitude explicitly (rather than reading
    ``ctx.cruise_altitude_ft``) so it can be re-run at candidate altitudes when
    searching for a lower-altitude mitigation.

    Returns (total, imc_count, marginal_count, clear_count).
    - imc_count: points where the altitude is inside BKN/OVC cloud
    - marginal_count: points where cloud clearance < threshold (but not in cloud)
    """
    assessments = _assess_enroute_vfr(
        ctx,
        model,
        cloud_clearance_ft,
        altitude_ft,
    )
    available = [assessment for assessment in assessments if assessment.available]
    total = len(available)
    imc_count = sum(assessment.in_cloud for assessment in available)
    marginal_count = sum(assessment.marginal for assessment in available)
    clear_count = total - imc_count - marginal_count
    return total, imc_count, marginal_count, clear_count


def _enroute_vfr_status(
    total: int,
    imc_count: int,
    marginal_count: int,
    imc_pct_amber: float,
    imc_pct_red: float,
) -> AdvisoryStatus:
    """Grade the en-route cloud-clearance axis from point counts.

    Shared by ``evaluate`` and the vertical-mitigation scan so a candidate
    altitude is graded with exactly the same thresholds as cruise.
    """
    if total <= 0:
        return AdvisoryStatus.GREEN
    affected = imc_count + marginal_count
    imc_pct = 100.0 * imc_count / total
    affected_pct = 100.0 * affected / total
    if imc_pct >= imc_pct_red:
        return AdvisoryStatus.RED
    if affected_pct >= imc_pct_amber:
        return AdvisoryStatus.AMBER
    return AdvisoryStatus.GREEN


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


def _in_corridor(
    distance_nm: float,
    total_distance_nm: float,
    corridor_nm: float,
    phase: Literal["climb", "descent"],
) -> bool:
    if phase == "climb":
        return distance_nm <= corridor_nm and distance_nm <= total_distance_nm / 2
    return (
        (total_distance_nm - distance_nm) <= corridor_nm
        and distance_nm > total_distance_nm / 2
    )


def _grade_corridor_layer(
    layer: EnhancedCloudLayer,
    floor_ft: float,
    cruise_ft: float,
) -> bool:
    """Existing grade predicate: a sub-cruise deck the flight must transit."""
    return floor_ft < layer.top_ft < cruise_ft


def _terminal_mitigation_layer(
    layer: EnhancedCloudLayer,
    floor_ft: float,
    cruise_ft: float,
) -> bool:
    """Broader existing solver guard, including decks that reach cruise."""
    return layer.top_ft > floor_ft and layer.base_ft < cruise_ft


def _corridor_point_assessment(
    ctx: RouteContext,
    model: str,
    rpa,
    phase: Literal["climb", "descent"],
    layer_predicate: Callable[[EnhancedCloudLayer, float, float], bool],
) -> CorridorPointAssessment | None:
    """Build the shared corridor record for either grade or mitigation use."""
    sounding = rpa.sounding.get(model)
    if sounding is None:
        return None
    distance_nm = rpa.distance_from_origin_nm or 0.0
    floor_ft = _field_elevation_ft(ctx, distance_nm)
    blocking_layers = tuple(
        layer
        for layer in sounding.cloud_layers
        if layer.coverage in (CloudCoverage.BKN, CloudCoverage.OVC)
        and layer_predicate(layer, floor_ft, ctx.cruise_altitude_ft)
    )
    lowest_base_ft = min(
        (layer.base_ft for layer in blocking_layers),
        default=None,
    )
    return CorridorPointAssessment(
        point_index=rpa.point_index,
        method_id=cloud_method_id(
            sounding.cloud_method_effective,
            ctx.cloud_method,
        ),
        distance_nm=distance_nm,
        phase=phase,
        has_ovc=any(
            layer.coverage == CloudCoverage.OVC for layer in blocking_layers
        ),
        has_bkn=any(
            layer.coverage == CloudCoverage.BKN for layer in blocking_layers
        ),
        base_agl_ft=(
            lowest_base_ft - floor_ft if lowest_base_ft is not None else None
        ),
        blocking_layers=blocking_layers,
    )


def _corridor_points(
    ctx: RouteContext,
    model: str,
    corridor_nm: float,
    phase: Literal["climb", "descent"],
) -> Iterator[CorridorPointAssessment]:
    """Yield stable corridor records using the established grade predicate.

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
    ignores it.

    Points are yielded in ``ctx.analyses`` order (callers that need distance order
    sort the result).
    """
    total = ctx.total_distance_nm

    for rpa in ctx.analyses:
        d = rpa.distance_from_origin_nm or 0.0
        if not _in_corridor(d, total, corridor_nm, phase):
            continue
        assessment = _corridor_point_assessment(
            ctx,
            model,
            rpa,
            phase,
            _grade_corridor_layer,
        )
        if assessment is not None:
            yield assessment


def _check_corridor_vfr(
    ctx: RouteContext,
    model: str,
    corridor_nm: float,
    records: tuple[CorridorPointAssessment, ...] | None = None,
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
    if records is None:
        records = tuple(
            record
            for phase in ("climb", "descent")
            for record in _corridor_points(ctx, model, corridor_nm, phase)
        )

    for phase, key, icao, fallback in phases:
        phase_records = [record for record in records if record.phase == phase]
        has_ovc = any(record.has_ovc for record in phase_records)
        has_bkn = any(record.has_bkn for record in phase_records)

        if has_ovc:
            worst = _worst_status(worst, AdvisoryStatus.RED)
            parts.append(adv_t(key, loc, cov="OVC", icao=icao or fallback))
        elif has_bkn:
            worst = _worst_status(worst, AdvisoryStatus.AMBER)
            parts.append(adv_t(key, loc, cov="BKN", icao=icao or fallback))

    return worst, parts


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
    in_cloud_layers, marginal_layers = _classify_vfr_cloud_layers(
        sounding,
        alt_ft,
        cloud_clearance_ft,
    )
    if in_cloud_layers:
        return INF
    return _MARGINAL_COST if marginal_layers else 0.0


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
    phase: Literal["climb", "descent"],
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

    Returns ``(point_count, span_nm)`` over the qualifying points.
    """
    dists: list[float] = []
    for rpa in ctx.analyses:
        d = rpa.distance_from_origin_nm or 0.0
        if not (lo_nm <= d <= hi_nm):
            continue
        assessment = _corridor_point_assessment(
            ctx,
            model,
            rpa,
            phase,
            _terminal_mitigation_layer,
        )
        if assessment is not None and assessment.blocking_layers:
            dists.append(d)
    if not dists:
        return 0, 0.0
    return len(dists), max(dists) - min(dists)


def _is_real_terminal_deck(count: int, span_nm: float) -> bool:
    """A terminal deck earns a corridor tip only when it spans more than one point.

    ``>= 2`` route points OR ``>= 15`` nm of along-track run (#342 Bug A). A single
    terminal point — the departure/arrival field's own cloud — never qualifies.
    """
    return count >= _TERMINAL_DECK_MIN_POINTS or span_nm >= _TERMINAL_DECK_MIN_RUN_NM


def _solver_mitigations(
    ctx: RouteContext,
    model: str,
    cloud_clearance_ft: float,
    imc_pct_amber: float,
    imc_pct_red: float,
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
            tot, imc, marg, _ = _check_enroute_vfr(ctx, model, cloud_clearance_ft, alt)
            cand = _enroute_vfr_status(tot, imc, marg, imc_pct_amber, imc_pct_red)
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
            and _is_real_terminal_deck(*_terminal_deck_span(
                ctx,
                model,
                cruise,
                0.0,
                climb.to_nm,
                "climb",
            ))
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
                *_terminal_deck_span(
                    ctx,
                    model,
                    cruise,
                    descent.from_nm,
                    total,
                    "descent",
                )
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


_AIRPORT_METHOD_ID = "airport_conditions"
_PRECIP_METHOD_ID = "nwp_precipitation_profile"


def _corridor_data_state(
    ctx: RouteContext,
    corridor_nm: float,
    records: tuple[CorridorPointAssessment, ...],
    route_assessments: list[VFRPointAssessment],
) -> DataState:
    """Classify corridor coverage from route-cloud and terrain availability."""
    phases: tuple[Literal["climb", "descent"], ...] = ("climb", "descent")
    expected_by_phase = {
        phase: {
            rpa.point_index
            for rpa in ctx.analyses
            if _in_corridor(
                rpa.distance_from_origin_nm or 0.0,
                ctx.total_distance_nm,
                corridor_nm,
                phase,
            )
        }
        for phase in phases
    }
    evaluated_by_phase = {
        phase: {
            record.point_index
            for record in records
            if record.phase == phase
        }
        for phase in phases
    }
    route_complete = {
        assessment.point_index
        for assessment in route_assessments
        if assessment.complete
    }
    elevation_usable = ctx.elevation is not None and bool(ctx.elevation.points)
    evaluated_phases = {
        phase for phase, point_indices in evaluated_by_phase.items() if point_indices
    }
    complete_phases = {
        phase
        for phase in phases
        if elevation_usable
        and expected_by_phase[phase]
        and expected_by_phase[phase] <= evaluated_by_phase[phase]
        and expected_by_phase[phase] <= route_complete
    }
    return data_state_from_domains(
        expected=phases,
        evaluated=evaluated_phases,
        complete=complete_phases,
    )


def _corridor_evidence_samples(
    records: tuple[CorridorPointAssessment, ...],
) -> tuple[EvidenceSample, ...]:
    samples: list[EvidenceSample] = []
    for record in records:
        reason_code = (
            "vfr_climb_deck"
            if record.phase == "climb"
            else "vfr_descent_deck"
        )
        for layer in record.blocking_layers:
            samples.append(
                EvidenceSample(
                    point_index=record.point_index,
                    severity=(
                        AdvisoryStatus.RED
                        if layer.coverage == CloudCoverage.OVC
                        else AdvisoryStatus.AMBER
                    ),
                    reason_code=reason_code,
                    metric_id="cloud_coverage",
                    method_id=record.method_id,
                    lower_altitude_ft=round(layer.base_ft),
                    upper_altitude_ft=round(layer.top_ft),
                )
            )
    return tuple(samples)


def _precip_evidence_samples(
    assessment: EnroutePrecipAssessment,
) -> tuple[EvidenceSample, ...]:
    """Remap the shared precipitation evidence for the capped VFR axis."""
    return tuple(
        replace(
            sample,
            severity=(
                AdvisoryStatus.AMBER
                if sample.severity == AdvisoryStatus.RED
                else sample.severity
            ),
            reason_code="vfr_precip_visibility",
            metric_id="precipitation_mm",
            method_id=_PRECIP_METHOD_ID,
        )
        for sample in assessment.evidence_samples
        if sample.severity != AdvisoryStatus.GREEN
    )


def _primary_method_for_status(
    raw_status: AdvisoryStatus,
    axes: tuple[tuple[AdvisoryStatus, set[str | None]], ...],
) -> str | None:
    """Choose one controlling method, or the explicit composite tie token."""
    controlling_methods: set[str] = set()
    for status, methods in axes:
        if status == raw_status:
            controlling_methods.update(
                method for method in methods if method is not None
            )
    if not controlling_methods:
        return None
    if len(controlling_methods) > 1:
        return "vfr_composite"
    return next(iter(controlling_methods))


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
                "BKN/OVC decks the flight would have to transit. RED indicates IFR "
                "conditions, IMC at cruise, or an OVC deck blocking climb/descent; "
                "AMBER flags marginal conditions or a BKN deck in a corridor."
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
                ),
                AdvisoryParameterDef(
                    key="imc_pct_amber",
                    label="IMC % (amber)",
                    description="Route percentage in IMC or marginal clearance for amber",
                    type="percent",
                    unit="%",
                    default=15,
                    min=5,
                    max=50,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="imc_pct_red",
                    label="IMC % (red)",
                    description="Route percentage in IMC for red",
                    type="percent",
                    unit="%",
                    default=30,
                    min=10,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="terminal_corridor_nm",
                    label="Terminal corridor",
                    description="Distance from departure/arrival to check for BKN/OVC decks in the climb-out and descent path",
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
        imc_pct_amber = params.get("imc_pct_amber", 15)
        imc_pct_red = params.get("imc_pct_red", 30)
        corridor_nm = params.get("terminal_corridor_nm", 5)
        mitigation_min_base_agl_ft = params.get("mitigation_min_base_agl_ft", 3000)
        mitigation_max_reposition_nm = params.get("mitigation_max_reposition_nm", 25)

        per_model: list[ModelAdvisoryResult] = []

        ordered_analyses = sorted(
            ctx.analyses,
            key=lambda rpa: (rpa.distance_from_origin_nm, rpa.point_index),
        )

        for model in ctx.models:
            # 1. Airport conditions
            airport_status, airport_detail, airport_state = _check_airport_vfr(
                ctx,
                model,
            )

            # 2. En-route cloud clearance
            route_assessments = _assess_enroute_vfr(
                ctx,
                model,
                cloud_clearance_ft,
                ctx.cruise_altitude_ft,
            )
            evaluated = {
                assessment.point_index
                for assessment in route_assessments
                if assessment.available
            }
            complete = {
                assessment.point_index
                for assessment in route_assessments
                if assessment.complete
            }
            imc_points = {
                assessment.point_index
                for assessment in route_assessments
                if assessment.available and assessment.in_cloud
            }
            marginal_points = {
                assessment.point_index
                for assessment in route_assessments
                if assessment.available and assessment.marginal
            }
            affected = imc_points | marginal_points
            cloud_samples = tuple(
                sample
                for assessment in route_assessments
                for sample in assessment.cloud_samples
            )
            available_route_methods = {
                assessment.method_id
                for assessment in route_assessments
                if assessment.available
            }
            total = len(evaluated)
            imc_count = len(imc_points)
            marginal_count = len(marginal_points)

            # 3. Climb-out / descent corridor decks (BKN/OVC below cruise near airports)
            corridor_records = tuple(
                record
                for phase in ("climb", "descent")
                for record in _corridor_points(ctx, model, corridor_nm, phase)
            )
            corridor_status, corridor_parts = _check_corridor_vfr(
                ctx,
                model,
                corridor_nm,
                corridor_records,
            )
            corridor_state = _corridor_data_state(
                ctx,
                corridor_nm,
                corridor_records,
                route_assessments,
            )
            corridor_samples = _corridor_evidence_samples(corridor_records)
            available_corridor_methods = {
                record.method_id
                for record in corridor_records
            }

            loc = ctx.locale

            # 4. Determine en-route status
            enroute_status = _enroute_vfr_status(
                total, imc_count, marginal_count, imc_pct_amber, imc_pct_red
            )
            if enroute_status == AdvisoryStatus.RED:
                route_methods = {
                    sample.method_id
                    for sample in cloud_samples
                    if sample.reason_code == "vfr_cruise_imc"
                }
            elif enroute_status == AdvisoryStatus.AMBER:
                route_methods = {sample.method_id for sample in cloud_samples}
            else:
                route_methods = available_route_methods

            if corridor_status == AdvisoryStatus.RED:
                corridor_methods = {
                    record.method_id
                    for record in corridor_records
                    if record.has_ovc
                }
            elif corridor_status == AdvisoryStatus.AMBER:
                corridor_methods = {
                    record.method_id
                    for record in corridor_records
                    if record.has_bkn
                }
            else:
                corridor_methods = available_corridor_methods

            # 5. En-route precipitation (visibility proxy) — capped at AMBER
            # in the composite; the standalone advisory grades it fully.
            # Deliberately called WITHOUT params: the composite always uses the
            # fixed precip defaults, independent of any per-user tuning of the
            # standalone EnroutePrecipEvaluator. The params dict here carries
            # only VFR keys, so the two can grade differently if a user tunes
            # the standalone — that divergence is intentional; the composite is
            # a fixed-threshold sanity floor, not a mirror of the standalone.
            precip_assessment = assess_enroute_precip(ctx, model)
            precip_status = precip_assessment.status
            precip_detail = precip_assessment.detail
            precip_signal = precip_assessment.has_signal
            # Missing precipitation is neutral to the raw VFR grade. Its missing
            # data state still participates in the final completeness guard.
            if not precip_signal or precip_status == AdvisoryStatus.UNAVAILABLE:
                precip_status, precip_detail = AdvisoryStatus.GREEN, ""
            elif precip_status == AdvisoryStatus.RED:
                precip_status = AdvisoryStatus.AMBER
            precip_samples = _precip_evidence_samples(precip_assessment)
            precip_methods = (
                {_PRECIP_METHOD_ID}
                if precip_assessment.summary.data_state != "unavailable"
                else set()
            )

            summary = summarize_evidence(
                route_points=ordered_analyses,
                total_distance_nm=ctx.total_distance_nm,
                evaluated_point_indices=evaluated,
                complete_point_indices=complete,
                affected_point_indices=affected,
                evidence_samples=(
                    *cloud_samples,
                    *corridor_samples,
                    *precip_samples,
                ),
            )
            combined_state = combine_data_states(
                summary.data_state,
                corridor_state,
                airport_state,
                precip_assessment.summary.data_state,
            )
            summary = replace(summary, data_state=combined_state)

            enroute_detail = ""
            if summary.total_points > 0 and summary.affected_points > 0:
                extent = summary.format_extent()
                if enroute_status == AdvisoryStatus.RED:
                    enroute_detail = adv_t("vfr.imc_over", loc, extent=extent)
                elif enroute_status == AdvisoryStatus.AMBER:
                    if marginal_count > 0 and imc_count > 0:
                        enroute_detail = adv_t(
                            "vfr.imc_marginal",
                            loc,
                            extent=extent,
                        )
                    elif imc_count > 0:
                        enroute_detail = adv_t(
                            "vfr.imc_over",
                            loc,
                            extent=extent,
                        )
                    else:
                        enroute_detail = adv_t(
                            "vfr.marginal",
                            loc,
                            extent=extent,
                        )
                else:
                    enroute_detail = adv_t("vfr.minor", loc, extent=extent)

            # 6. Combine airport + en-route + corridor + precipitation
            raw_status = _worst_status(
                airport_status, enroute_status, corridor_status, precip_status,
            )

            detail_parts = []
            if airport_detail:
                detail_parts.append(airport_detail)
            if enroute_detail:
                detail_parts.append(enroute_detail)
            detail_parts.extend(corridor_parts)
            if precip_status != AdvisoryStatus.GREEN and precip_detail:
                detail_parts.append(precip_detail)

            if not detail_parts:
                if summary.total_points > 0:
                    detail = adv_t("vfr.throughout", loc)
                else:
                    detail = adv_t("vfr.airports_ok", loc)
            else:
                detail = " | ".join(detail_parts)

            # 7. Mitigations (advice only — never change the grade). All derived
            # from a single continuous vertical profile over the (distance ×
            # altitude) grid (#335): "fly lower" (cruise_imc) when the profile
            # can't sustain cruise, "climb after / descend before" (climb_deck /
            # descent_deck) when a terminal deck forces the climb/descent low.
            mitigations: list[Mitigation] = _solver_mitigations(
                ctx, model, cloud_clearance_ft,
                imc_pct_amber, imc_pct_red,
                mitigation_min_base_agl_ft, mitigation_max_reposition_nm,
                enroute_status, loc,
            )

            primary_method_id = _primary_method_for_status(
                raw_status,
                (
                    (
                        airport_status,
                        ({_AIRPORT_METHOD_ID} if airport_state != "unavailable" else set()),
                    ),
                    (enroute_status, route_methods),
                    (corridor_status, corridor_methods),
                    (precip_status, precip_methods),
                ),
            )
            missing_detail = adv_t(
                "no_data" if combined_state == "unavailable" else "partial_data",
                loc,
            )
            per_model.append(
                summary.build_result(
                    model=model,
                    status=raw_status,
                    detail=detail,
                    unavailable_detail=missing_detail,
                    primary_method_id=primary_method_id,
                    mitigations=mitigations,
                )
            )

        return RouteAdvisoryResult.from_per_model("vfr_feasibility", per_model, params)
