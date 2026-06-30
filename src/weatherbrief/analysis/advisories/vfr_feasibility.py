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

from collections.abc import Iterator

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import format_extent
from weatherbrief.analysis.advisories.enroute_precip import classify_enroute_precip
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    CloudCoverage,
    Mitigation,
    MitigationKind,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)
from weatherbrief.models.airport_conditions import FlightCategory

# Minimum clearance above the highest terrain along the route for a lower cruise
# altitude to be a valid VFR mitigation. Mirrors the icing-escape terrain gate
# (``sounding/advisories.py:_TERRAIN_CLEARANCE_FT``): a clear band below MSA is
# not a flyable option, so the RED is genuine and no mitigation is offered.
_TERRAIN_CLEARANCE_FT = 1000

# Step (ft) for the downward scan when searching for a clear lower altitude.
_MITIGATION_STEP_FT = 500


def _worst_status(*statuses: AdvisoryStatus) -> AdvisoryStatus:
    """Return the most severe status from the given values."""
    return AdvisoryStatus.worst(list(statuses))


def _check_airport_vfr(
    ctx: RouteContext,
    model: str,
) -> tuple[AdvisoryStatus, str]:
    """Check departure and arrival airport conditions for VFR feasibility.

    Uses the flight category (VFR/MVFR/IFR/LIFR) which already encodes
    ceiling and visibility thresholds per aviation standards.

    Returns (status, detail_fragment).
    """
    if ctx.airport_conditions is None:
        return AdvisoryStatus.GREEN, ""

    dep = ctx.airport_conditions.departure
    arr = ctx.airport_conditions.arrival
    dep_cond = dep.condition_for_model(model)
    arr_cond = arr.condition_for_model(model)

    parts: list[str] = []
    worst = AdvisoryStatus.GREEN

    loc = ctx.locale
    for label_key, icao, cond in [("airport.dep", dep.icao, dep_cond), ("airport.arr", arr.icao, arr_cond)]:
        if cond is None:
            continue
        label = adv_t(label_key, loc)
        cat = cond.flight_category
        if cat in (FlightCategory.IFR, FlightCategory.LIFR):
            worst = _worst_status(worst, AdvisoryStatus.RED)
            parts.append(f"{label} {icao} {cat.value}")
        elif cat == FlightCategory.MVFR:
            worst = _worst_status(worst, AdvisoryStatus.AMBER)
            parts.append(f"{label} {icao} MVFR")

    detail = " | ".join(parts) if parts else ""
    return worst, detail


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
    total = 0
    imc_count = 0
    marginal_count = 0
    clear_count = 0
    cruise = altitude_ft

    for rpa in ctx.analyses:
        sounding = rpa.sounding.get(model)
        if sounding is None:
            continue
        total += 1

        in_cloud = False
        marginal = False

        for cl in sounding.cloud_layers:
            if cl.coverage not in (CloudCoverage.BKN, CloudCoverage.OVC):
                continue
            # Check if cruise altitude is inside the cloud layer
            if cl.base_ft <= cruise <= cl.top_ft:
                in_cloud = True
                break
            # Check vertical clearance from cloud base or top
            dist_to_base = abs(cruise - cl.base_ft)
            dist_to_top = abs(cruise - cl.top_ft)
            min_dist = min(dist_to_base, dist_to_top)
            if min_dist < cloud_clearance_ft:
                marginal = True

        if in_cloud:
            imc_count += 1
        elif marginal:
            marginal_count += 1
        else:
            clear_count += 1

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


def _corridor_points(
    ctx: RouteContext,
    model: str,
    corridor_nm: float,
    phase: str,
) -> Iterator[tuple[float, bool, bool, float | None]]:
    """Yield ``(distance_nm, has_ovc, has_bkn, base_agl_ft)`` for each corridor point.

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
        base_agl_ft = (lowest_base_ft - floor) if lowest_base_ft is not None else None
        yield d, has_ovc, has_bkn, base_agl_ft


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
        for _d, p_ovc, p_bkn, _base in _corridor_points(ctx, model, corridor_nm, phase):
            has_ovc = has_ovc or p_ovc
            has_bkn = has_bkn or p_bkn

        if has_ovc:
            worst = _worst_status(worst, AdvisoryStatus.RED)
            parts.append(adv_t(key, loc, cov="OVC", icao=icao or fallback))
        elif has_bkn:
            worst = _worst_status(worst, AdvisoryStatus.AMBER)
            parts.append(adv_t(key, loc, cov="BKN", icao=icao or fallback))

    return worst, parts


_SEVERITY = {AdvisoryStatus.GREEN: 0, AdvisoryStatus.AMBER: 1, AdvisoryStatus.RED: 2}


def _vertical_mitigation(
    ctx: RouteContext,
    model: str,
    cloud_clearance_ft: float,
    imc_pct_amber: float,
    imc_pct_red: float,
    cruise_status: AdvisoryStatus,
    loc: str | None,
) -> Mitigation | None:
    """Search for a lower cruise altitude that clears en-route cloud (cruise_imc).

    Scans downward from ``cruise - step`` to a terrain floor
    (``max terrain + _TERRAIN_CLEARANCE_FT``), re-grading the en-route axis at
    each candidate. Returns an ALTITUDE mitigation reporting the **highest**
    altitude whose status strictly improves on cruise, preferring a fully clear
    (GREEN) band over a merely-better (AMBER) one. Returns None when the
    en-route axis is already green, or when the only improving band lies below
    the terrain floor (the RED is genuine).

    ``mitigated_status`` is the status of the *cruise IMC axis alone* at the
    chosen altitude — NOT the overall advisory status (a corridor deck or
    airport issue can still hold the grade higher).
    """
    if cruise_status == AdvisoryStatus.GREEN:
        return None

    cruise = ctx.cruise_altitude_ft
    max_terrain = ctx.elevation.max_elevation_ft if ctx.elevation else 0.0
    floor = max_terrain + _TERRAIN_CLEARANCE_FT

    best_alt: int | None = None
    best_status: AdvisoryStatus | None = None

    alt = cruise - _MITIGATION_STEP_FT
    while alt >= floor:
        total, imc, marg, _ = _check_enroute_vfr(ctx, model, cloud_clearance_ft, alt)
        cand = _enroute_vfr_status(total, imc, marg, imc_pct_amber, imc_pct_red)
        # Only an altitude that strictly improves on cruise is worth offering.
        if _SEVERITY[cand] < _SEVERITY[cruise_status]:
            if best_status is None or _SEVERITY[cand] < _SEVERITY[best_status]:
                best_alt = int(alt)
                best_status = cand
            # Scanning high→low, the first GREEN is the highest GREEN and the
            # best we can do — stop rather than descend further for no gain.
            if cand == AdvisoryStatus.GREEN:
                break
        alt -= _MITIGATION_STEP_FT

    if best_alt is None or best_status is None:
        return None

    return Mitigation(
        kind=MitigationKind.ALTITUDE,
        addresses="cruise_imc",
        detail=adv_t("vfr.mitigation.altitude", loc, alt=best_alt),
        mitigated_status=best_status,
        altitude_ft=best_alt,
    )


def _corridor_blocked_profile(
    ctx: RouteContext,
    model: str,
    corridor_nm: float,
    phase: str,
) -> list[tuple[float, bool, float | None]]:
    """Per-point (distance, blocked, base_agl_ft) for a corridor phase, sorted by distance.

    A point is *blocked* when it carries a transitable BKN/OVC deck (see
    ``_corridor_points`` for the membership + deck condition). Collapses the
    per-point OVC/BKN split to a single ``blocked`` bool and carries the
    lowest-deck base height above terrain (``base_agl_ft``) so the mitigation can
    both locate where the deck breaks and check there is VFR room beneath it.
    """
    profile = [
        (d, p_ovc or p_bkn, base_agl)
        for d, p_ovc, p_bkn, base_agl in _corridor_points(ctx, model, corridor_nm, phase)
    ]
    profile.sort(key=lambda t: t[0])
    return profile


def _under_deck_flyable(
    blocked_points: list[tuple[float, float | None]],
    min_base_agl_ft: float,
) -> bool:
    """True if every blocked corridor point has VFR room beneath its deck.

    The along-route mitigation tells the pilot to stay *below* the deck until it
    breaks, then climb (or to descend below it before it starts). That only works
    if there is flyable VFR airspace under the deck along the whole blocked
    stretch — i.e. each blocked point's lowest deck base sits at least
    ``min_base_agl_ft`` above terrain. If the deck scrapes the ground anywhere on
    that stretch, the clear air can't be reached and the RED/AMBER is genuine.
    """
    return all(
        base_agl is not None and base_agl >= min_base_agl_ft
        for _d, base_agl in blocked_points
    )


def _corridor_mitigation(
    ctx: RouteContext,
    model: str,
    corridor_nm: float,
    min_base_agl_ft: float,
    loc: str | None,
) -> list[Mitigation]:
    """Find along-route repositioning mitigations for blocked corridor decks.

    Climb-out: if the corridor points nearest departure are blocked but farther
    ones clear, offer "climb to cruise after ~d* nm from departure" (``d*`` is
    the nearest confirmed-clear distance beyond the blocked region). Descent is
    symmetric near the arrival end. Two gates must BOTH hold:

    1. A genuine clear/blocked split — a uniformly blocked corridor cannot be
       repositioned around.
    2. The deck is flyable underneath along the blocked stretch
       (``_under_deck_flyable``) — otherwise the clear air is unreachable.

    If either fails, no mitigation (the RED/AMBER is genuine).

    ``mitigated_status`` is GREEN: repositioning clears that phase's deck. It
    speaks only for the corridor axis, not the overall advisory.
    """
    total = ctx.total_distance_nm
    mitigations: list[Mitigation] = []

    # --- Climb-out: deck near departure, clear beyond d* ---
    climb = _corridor_blocked_profile(ctx, model, corridor_nm, "climb")
    blocked = [(d, base_agl) for d, b, base_agl in climb if b]
    clear = [d for d, b, _ in climb if not b]
    if blocked and clear and _under_deck_flyable(blocked, min_base_agl_ft):
        max_blocked = max(d for d, _ in blocked)
        beyond = [d for d in clear if d > max_blocked]
        if beyond:
            # Round once: the phrasing is qualitative ("~X nm"), so detail and
            # the structured distance must report the same integer.
            dist = round(min(beyond))
            mitigations.append(Mitigation(
                kind=MitigationKind.ROUTE_POSITION,
                addresses="climb_deck",
                detail=adv_t("vfr.mitigation.climb_after", loc, dist=dist),
                mitigated_status=AdvisoryStatus.GREEN,
                distance_nm=dist,
                reference="departure",
            ))

    # --- Descent: deck near arrival, clear before it ---
    descent = _corridor_blocked_profile(ctx, model, corridor_nm, "descent")
    blocked = [(d, base_agl) for d, b, base_agl in descent if b]
    clear = [d for d, b, _ in descent if not b]
    if blocked and clear and _under_deck_flyable(blocked, min_base_agl_ft):
        min_blocked = min(d for d, _ in blocked)
        before = [d for d in clear if d < min_blocked]
        if before:
            d_star = max(before)
            # Round once (see climb case): nm before arrival, same int in both.
            dist = round(total - d_star)
            mitigations.append(Mitigation(
                kind=MitigationKind.ROUTE_POSITION,
                addresses="descent_deck",
                detail=adv_t("vfr.mitigation.descend_before", loc, dist=dist),
                mitigated_status=AdvisoryStatus.GREEN,
                distance_nm=dist,
                reference="arrival",
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
                "BKN/OVC decks the flight would have to transit. RED indicates IFR "
                "conditions, IMC at cruise, or an OVC deck blocking climb/descent; "
                "AMBER flags marginal conditions or a BKN deck in a corridor."
            ),
            category="flight_rules",
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
                    label="Mitigation min deck base (AGL)",
                    description="Minimum cloud-deck base above terrain for an along-route 'stay below the deck then climb/descend' mitigation to be offered — below this there isn't VFR room beneath the deck to reach the clear air",
                    type="altitude",
                    unit="ft",
                    default=3000,
                    min=1000,
                    max=6000,
                    step=500,
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

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            # 1. Airport conditions
            airport_status, airport_detail = _check_airport_vfr(ctx, model)

            # 2. En-route cloud clearance
            total, imc_count, marginal_count, _ = _check_enroute_vfr(
                ctx, model, cloud_clearance_ft, ctx.cruise_altitude_ft
            )

            # 3. Climb-out / descent corridor decks (BKN/OVC below cruise near airports)
            corridor_status, corridor_parts = _check_corridor_vfr(
                ctx, model, corridor_nm
            )

            loc = ctx.locale
            if (
                total == 0
                and airport_status == AdvisoryStatus.GREEN
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
                total, imc_count, marginal_count, imc_pct_amber, imc_pct_red
            )
            enroute_detail = ""

            if total > 0 and affected > 0:
                ext = format_extent(affected, total, ctx.total_distance_nm)
                if enroute_status == AdvisoryStatus.RED:
                    enroute_detail = adv_t("vfr.imc_over", loc, extent=ext)
                elif enroute_status == AdvisoryStatus.AMBER:
                    if marginal_count > 0 and imc_count > 0:
                        enroute_detail = adv_t("vfr.imc_marginal", loc, extent=ext)
                    elif imc_count > 0:
                        enroute_detail = adv_t("vfr.imc_over", loc, extent=ext)
                    else:
                        enroute_detail = adv_t("vfr.marginal", loc, extent=ext)
                else:  # GREEN status but some points affected → minor clearance issues
                    enroute_detail = adv_t("vfr.minor", loc, extent=ext)

            # 5. En-route precipitation (visibility proxy) — capped at AMBER
            # in the composite; the standalone advisory grades it fully.
            # Deliberately called WITHOUT params: the composite always uses the
            # fixed precip defaults, independent of any per-user tuning of the
            # standalone EnroutePrecipEvaluator. The params dict here carries
            # only VFR keys, so the two can grade differently if a user tunes
            # the standalone — that divergence is intentional; the composite is
            # a fixed-threshold sanity floor, not a mirror of the standalone.
            precip_status, precip_detail, _, _, precip_signal = (
                classify_enroute_precip(ctx, model)
            )
            # Old pack without precip data (no signal / UNAVAILABLE) → treat as
            # GREEN in the composite rather than penalising a missing field.
            if not precip_signal or precip_status == AdvisoryStatus.UNAVAILABLE:
                precip_status, precip_detail = AdvisoryStatus.GREEN, ""
            elif precip_status == AdvisoryStatus.RED:
                precip_status = AdvisoryStatus.AMBER

            # 6. Combine airport + en-route + corridor + precipitation
            status = _worst_status(
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
                if total > 0:
                    detail = adv_t("vfr.throughout", loc)
                else:
                    detail = adv_t("vfr.airports_ok", loc)
            else:
                detail = " | ".join(detail_parts)

            # 7. Mitigations (advice only — never change the grade). Vertical:
            # a lower altitude that clears cruise IMC. Along-route: reposition
            # the climb/descent to thread a corridor deck.
            mitigations: list[Mitigation] = []
            vertical = _vertical_mitigation(
                ctx, model, cloud_clearance_ft,
                imc_pct_amber, imc_pct_red, enroute_status, loc,
            )
            if vertical is not None:
                mitigations.append(vertical)
            mitigations.extend(_corridor_mitigation(
                ctx, model, corridor_nm, mitigation_min_base_agl_ft, loc,
            ))

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
                mitigations=mitigations,
            ))

        return RouteAdvisoryResult.from_per_model("vfr_feasibility", per_model, params)
